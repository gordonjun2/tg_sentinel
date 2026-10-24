import time  # Add time module for timing
import os
import sys
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv
from openai import OpenAI
from typing import List
from config import GEMINI_API_KEY, GEMINI_ENRICHMENT_MODEL, OPENAI_API_KEY, OPENAI_ENRICHMENT_MODEL
from llm_chain import LLMChainError, run_llm_chain
from utils import (
    chunk_audio_with_overlap,
    remove_overlap_text,
    convert_text_to_pdf,
    convert_text_to_md,
    sys_msg,
    sys_msg_final_summary,
    convert_text_to_docx,
)
from database import db  # Import here to avoid circular imports


class AudioTranscriber:
    def __init__(self):
        self.asr_model = "whisperx"
        self.device = "cpu"
        # self.audio_file = "SISC Event 20250705 (Yuna).m4a"
        self.compute_type = "float32"
        self.chunk_size_seconds = 30
        self.overlap_seconds = 3
        self.language = "en"
        self.chunk_size_in_token = 32000
        self.chunk_size_in_len = self.chunk_size_in_token * 4

        # Model is loaded lazily on first transcribe() call so importing this
        # module (e.g. from bot.py) does not pull torch/whisperx into memory.
        self._model = None

        self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

    def _generate_text(self, system_instruction: str, contents: str, task: str) -> str:
        def _via_gemini() -> str:
            response = self.gemini_client.models.generate_content(
                model=GEMINI_ENRICHMENT_MODEL,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
                contents=contents,
            )
            return response.text

        def _via_openai() -> str:
            if self.openai_client is None:
                raise RuntimeError("OpenAI client not configured")
            response = self.openai_client.responses.create(
                model=OPENAI_ENRICHMENT_MODEL,
                instructions=system_instruction,
                input=contents,
            )
            return response.output_text

        outcome = run_llm_chain(
            task,
            [
                ("gemini", GEMINI_ENRICHMENT_MODEL, _via_gemini),
                ("openai", OPENAI_ENRICHMENT_MODEL, _via_openai),
            ],
        )
        return outcome.result

    def _ensure_model(self):
        if self._model is None:
            if self.asr_model.lower() == "whisper":
                import whisper

                self._model = whisper.load_model("turbo", self.device)
            else:
                import whisperx

                self._model = whisperx.load_model(
                    "medium", self.device, compute_type=self.compute_type
                )
        return self._model

    @staticmethod
    def _default_progress(current: int, total: int) -> None:
        # Used in CLI/subprocess mode: bot.py parses these lines for progress
        print(f"PROGRESS:{current}:{total}", flush=True)

    def transcribe(self, audio_file, progress_callback=None):
        if progress_callback is None:
            progress_callback = self._default_progress

        # Load audio (imports stay lazy to keep module import cheap)
        if self.asr_model.lower() == "whisper":
            import whisper

            audio = whisper.load_audio(audio_file)
        else:
            import whisperx

            audio = whisperx.load_audio(audio_file)

        model = self._ensure_model()

        # Chunk audio
        audio_chunks = chunk_audio_with_overlap(
            audio, self.chunk_size_seconds, self.overlap_seconds
        )

        # Process each chunk and collect transcriptions
        prev_chunk_text = ""
        all_transcriptions = []
        total_processing_time = 0  # Track total processing time
        total_chunks = len(audio_chunks)

        for i, chunk in enumerate(audio_chunks):
            # Update progress if callback provided
            if progress_callback:
                progress_callback(i, total_chunks)

            # Start timing this chunk
            chunk_start_time = time.time()

            # Transcribe the chunk
            result = model.transcribe(chunk, language=self.language)

            # Keep only segments after chunk_start
            chunk_text = " ".join(
                segment["text"].strip() for segment in result["segments"]
            )

            # Remove overlap with previous chunk
            if prev_chunk_text:
                chunk_text = remove_overlap_text(prev_chunk_text, chunk_text)

            # Calculate and display time taken for this chunk
            chunk_time = time.time() - chunk_start_time
            total_processing_time += chunk_time

            # Add to all transcriptions
            all_transcriptions.append(chunk_text)
            prev_chunk_text = chunk_text

        # Final progress update
        if progress_callback:
            progress_callback(total_chunks, total_chunks)

        # Combine all transcriptions
        final_transcription = " ".join(all_transcriptions)

        # Save to file
        base_filename = os.path.splitext(os.path.basename(audio_file))[0]
        output_file = f"./transcriptions/{base_filename}_transcription.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_transcription)

    def extract_discussion_insight(self, file_path):
        try:
            # Mark that we're starting insight extraction
            db.start_insight_extraction(file_path)

            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()

            # Initialize the text splitter
            # (lazy import: langchain pulls in torch at import time)
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size_in_len,
                chunk_overlap=self.chunk_size_in_len * 0.01,
                length_function=len,
                is_separator_regex=False,
            )

            # Split the text into chunks
            chunks = text_splitter.split_text(text)

            # Process each chunk and collect responses
            chunk_responses = []
            for i, chunk in enumerate(chunks, 1):
                response = self._generate_text(sys_msg, chunk, task="insight_chunk")
                chunk_responses.append(response)

                # Sleep for 10 seconds between chunks
                time.sleep(10)

            # Combine all responses
            combined_responses = "\n\n".join(chunk_responses)

            # Generate final summary
            final_text = self._generate_text(
                sys_msg_final_summary, combined_responses, task="insight_summary"
            )

            # Get base filename from transcription file path
            # Remove _transcription.txt to get the original base filename
            base_filename = os.path.splitext(os.path.basename(file_path))[0]
            if base_filename.endswith("_transcription"):
                base_filename = base_filename[:-14]  # remove '_transcription'

            # Define output paths
            # md_path = f"./discussion_insights/{base_filename}_insights.md"
            # pdf_path = f"./discussion_insights/{base_filename}_insights.pdf"
            docx_path = f"./discussion_insights/{base_filename}_insights.docx"

            # Convert to MD, PDF and DOCX with specific output paths
            # convert_text_to_md(final_text, md_path)
            # convert_text_to_pdf(final_text, pdf_path)
            convert_text_to_docx(final_text, docx_path)

            # Mark insight extraction as complete
            db.complete_insight_extraction(file_path)

        except Exception as e:
            # Mark insight extraction as complete even on error
            db.complete_insight_extraction(file_path)
            # Re-raise the exception to be handled by the caller
            raise e


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transcription / insight tools")
    parser.add_argument(
        "--transcribe",
        metavar="AUDIO_FILE",
        help="transcribe an audio file with whisperx, then exit (designed to "
        "run as a subprocess so all model memory is freed on exit)",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="transcription text file for discussion insight extraction",
    )
    args = parser.parse_args()

    if args.transcribe:
        AudioTranscriber().transcribe(args.transcribe)
        sys.exit(0)

    # Get file path from command line argument or prompt user
    if args.file:
        file_path = args.file
    else:
        file_path = input("Enter the transcription file path: ").strip()

    # Validate file exists
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    # Extract discussion insights
    print(f"Processing file: {file_path}")
    try:
        AudioTranscriber().extract_discussion_insight(file_path)
        print("Insight extraction completed successfully!")
    except Exception as e:
        print(f"Error during insight extraction: {e}")
        sys.exit(1)

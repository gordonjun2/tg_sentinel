import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

# Group IDs
ADMIN_GROUP_ID = os.getenv('ADMIN_GROUP_ID')
if not ADMIN_GROUP_ID:
    raise ValueError("ADMIN_GROUP_ID not found in environment variables")
try:
    ADMIN_GROUP_ID = int(ADMIN_GROUP_ID)
except ValueError:
    raise ValueError("ADMIN_GROUP_ID must be a valid integer")

TARGET_GROUP_ID = os.getenv('TARGET_GROUP_ID')
if not TARGET_GROUP_ID:
    raise ValueError("TARGET_GROUP_ID not found in environment variables")
try:
    TARGET_GROUP_ID = int(TARGET_GROUP_ID)
except ValueError:
    raise ValueError("TARGET_GROUP_ID must be a valid integer")

GOOGLE_DRIVE_MAIN_FOLDER_ID = os.getenv('GOOGLE_DRIVE_MAIN_FOLDER_ID')
if not GOOGLE_DRIVE_MAIN_FOLDER_ID:
    raise ValueError(
        "GOOGLE_DRIVE_MAIN_FOLDER_ID not found in environment variables")

GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID = os.getenv(
    'GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID')
if not GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID:
    raise ValueError(
        "GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID not found in environment variables"
    )

GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID = os.getenv(
    'GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID')
if not GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID:
    raise ValueError(
        "GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID not found in environment variables"
    )

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

TELEGRAM_API_KEY = os.getenv('TELEGRAM_API_KEY')
if not TELEGRAM_API_KEY:
    raise ValueError("TELEGRAM_API_KEY not found in environment variables")

TELEGRAM_HASH = os.getenv('TELEGRAM_HASH')
if not TELEGRAM_HASH:
    raise ValueError("TELEGRAM_HASH not found in environment variables")

# Survey questions
SURVEY_QUESTIONS = [
    "What's your name?", "Which company are you currently with?",
    "What is your current role or job title?",
    "Please share your LinkedIn profile.",
    "What's one AI tool you use regularly, and why do you find it valuable?"
]

# File size limits (in bytes)
MAX_AUDIO_FILE_SIZE = 1024 * 1024 * 1024

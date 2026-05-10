import asyncio
import json
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)

from ai_enrichment import (
    classify_worthiness,
    evaluate_poll_opportunity,
    extract_urls,
    fetch_url_content,
    generate_enrichment_reply,
    search_web,
    serialize_messages,
)


async def run_flow(message_text: str, label: str):
    messages = [
        {
            "message_id": 1,
            "username": "gordon",
            "text": message_text,
            "chat_id": -1001234567890,
            "first_name": "Gordon",
        }
    ]

    print("=" * 60)
    print(f"TEST: {label}")
    print("=" * 60)

    print("\n" + "-" * 40)
    print("STEP 1: Extract URLs")
    print("-" * 40)
    urls = extract_urls(message_text)
    print(f"URLs found: {urls}")

    print("\n" + "-" * 40)
    print("STEP 2: Serialize messages")
    print("-" * 40)
    context_text = serialize_messages(messages)
    print(f"Context text:\n{context_text}")

    print("\n" + "-" * 40)
    print("STEP 3: Fetch URL content")
    print("-" * 40)
    retrieved_context = ""
    for url in urls[:2]:
        content = fetch_url_content(url)
        print(f"  URL: {url}")
        if content:
            print(f"  Fetched {len(content)} chars")
            retrieved_context += f"\n\n--- Content from {url} ---\n{content}"
        else:
            print(f"  No content extracted")

    print("\n" + "-" * 40)
    print("STEP 4: Classify worthiness (Gemini -> OpenAI fallback)")
    print("-" * 40)
    classification = classify_worthiness(context_text)
    print(f"Classification: {json.dumps(classification, indent=2)}")

    if not classification.get("should_reply"):
        print("Classification says not worthy. Stopping.")
        return

    search_queries = classification.get("search_queries", [])
    print(f"\nSearch queries: {search_queries}")

    if not retrieved_context and search_queries:
        print("\n" + "-" * 40)
        print("STEP 5: Web search for context")
        print("-" * 40)
        all_results = []
        for query in search_queries[:3]:
            results = search_web(query, 3)
            all_results.extend(results)
            print(f"\n  Query: '{query}' -> {len(results)} results")
            for i, r in enumerate(results):
                print(f"    [{i}] title='{r['title'][:80]}'")
                print(f"        url='{r['url'][:80]}'")
                print(f"        content_len={len(r['content'])}")
                print(f"        content_preview='{r['content'][:150]}'")

        seen_urls = set()
        for r in all_results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                retrieved_context += f"\n- {r['title']} ({r['url']}): {r['content']}\n"

    print(f"\nTotal retrieved_context: {len(retrieved_context)} chars")
    if retrieved_context:
        print(f"Context preview:\n{retrieved_context[:500]}")

    if not retrieved_context:
        print("No context retrieved. Cannot generate reply.")
        return

    print("\n" + "-" * 40)
    print("STEP 6: Generate enrichment reply (Gemini -> OpenAI fallback)")
    print("-" * 40)
    reply = generate_enrichment_reply(context_text, retrieved_context)
    if reply:
        print(f"Reply ({len(reply)} chars):\n{reply}")
    else:
        print("Reply: NO_REPLY (context adds no value)")
        return

    print("\n" + "-" * 40)
    print("STEP 7: Evaluate poll opportunity (instructor structured output)")
    print("-" * 40)
    topic = classification.get("topic", "")
    poll_result = evaluate_poll_opportunity(context_text, topic, retrieved_context)
    if poll_result:
        print(f"should_create_poll: {poll_result.should_create_poll}")
        print(f"question: {poll_result.question}")
        print(f"options: {poll_result.options}")
        print(f"allows_multiple_answers: {poll_result.allows_multiple_answers}")
        if poll_result.should_create_poll and poll_result.options:
            final_options = list(poll_result.options) + ["Others (drop in chat!)"]
            print(f"\nFinal poll that would be sent:")
            print(f"  Q: {poll_result.question}")
            for i, opt in enumerate(final_options):
                print(f"  [{i}] {opt}")
            print(f"  multi-select: {poll_result.allows_multiple_answers or False}")
    else:
        print("Poll evaluation returned None (both providers failed)")


async def test_coinbase():
    message_text = (
        "The crossfire casualties of AI ... real and happening ... "
        "read Coinbase CEO x-post ref by this x-post ...\n\n"
        "https://x.com/i/status/2051670877440188453"
    )
    await run_flow(message_text, "Coinbase CEO AI crossfire casualties")


async def test_osint():
    message_text = (
        "recently saw this pretty cool agent skills to do OSINT.\n\n"
        "https://github.com/elementalsouls/Claude-OSINT\n\n"
        "if you are in cybersecurity, might be interesting for me"
    )
    await run_flow(message_text, "Claude-OSINT GitHub repo")


async def test_poll_only():
    print("=" * 60)
    print("TEST: Poll Evaluation Only (instructor structured output)")
    print("=" * 60)

    context_text = (
        "alice: Has anyone actually deployed AI agents in production? We tried but hallucination is a dealbreaker\n"
        "bob: We're using RAG with Gemini but it's still not reliable enough for customer-facing\n"
        "alice: Yeah I saw a recent survey showing 67% of enterprises experimenting but only 12% in production\n"
        "charlie: We've been using Cursor for internal tooling and it's been solid, but that's not really 'agents'\n"
        "bob: True, there's a big gap between coding assistants and autonomous agents"
    )
    topic = "AI agents in production - hallucination and reliability challenges"
    retrieved_context = (
        "- Recent Gartner survey: 67% of enterprises experimenting with AI agents, only 12% in production\n"
        "- Key challenges: hallucination (cited by 78%), tool reliability (65%), cost management (52%)\n"
        "- Emerging solutions: multi-agent verification, human-in-the-loop patterns, constrained outputs"
    )

    print(f"\nContext:\n{context_text}")
    print(f"\nTopic: {topic}")
    print(f"\nRetrieved context (first 300 chars):\n{retrieved_context[:300]}")

    print("\n" + "-" * 40)
    print("Evaluating poll opportunity...")
    print("-" * 40)
    result = evaluate_poll_opportunity(context_text, topic, retrieved_context)

    if result:
        print(f"\nshould_create_poll: {result.should_create_poll}")
        if result.should_create_poll:
            print(f"question: {result.question}")
            print(f"options: {result.options}")
            print(f"allows_multiple_answers: {result.allows_multiple_answers}")
            final_options = list(result.options or []) + ["Others (drop in chat!)"]
            print(f"\n--- Final Poll Preview ---")
            print(f"Q: {result.question}")
            for i, opt in enumerate(final_options):
                print(f"  [{i}] {opt}")
            print(f"  multi-select: {result.allows_multiple_answers or False}")
        else:
            print("Decision: No poll for this topic.")
    else:
        print("FAILED: Poll evaluation returned None")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "osint":
            asyncio.run(test_osint())
        elif arg == "coinbase":
            asyncio.run(test_coinbase())
        elif arg == "poll":
            asyncio.run(test_poll_only())
        else:
            print(f"Unknown test: {arg}")
            print("Usage: python test_enrichment_live.py [osint|coinbase|poll]")
    else:
        asyncio.run(test_osint())

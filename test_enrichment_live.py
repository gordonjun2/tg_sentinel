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
    extract_urls,
    fetch_url_content,
    generate_enrichment_reply,
    search_web,
    serialize_messages,
)


async def test_flow():
    message_text = (
        "The crossfire casualties of AI ... real and happening ... "
        "read Coinbase CEO x-post ref by this x-post ...\n\n"
        "https://x.com/i/status/2051670877440188453"
    )

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
    print("STEP 1: Extract URLs")
    print("=" * 60)
    urls = extract_urls(message_text)
    print(f"URLs found: {urls}")

    print("\n" + "=" * 60)
    print("STEP 2: Serialize messages")
    print("=" * 60)
    context_text = serialize_messages(messages)
    print(f"Context text:\n{context_text}")

    print("\n" + "=" * 60)
    print("STEP 3: Fetch URL content")
    print("=" * 60)
    for url in urls[:2]:
        content = fetch_url_content(url)
        print(f"  URL: {url}")
        print(f"  Content: {repr(content[:200]) if content else 'None'}")

    print("\n" + "=" * 60)
    print("STEP 4: Classify worthiness (Gemini -> OpenAI fallback)")
    print("=" * 60)
    classification = classify_worthiness(context_text)
    print(f"Classification: {json.dumps(classification, indent=2)}")

    if not classification.get("should_reply"):
        print("Classification says not worthy. Stopping.")
        return

    search_queries = classification.get("search_queries", [])
    print(f"\nSearch queries: {search_queries}")

    print("\n" + "=" * 60)
    print("STEP 5: Web search for context")
    print("=" * 60)
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

    retrieved_context = ""
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

    print("\n" + "=" * 60)
    print("STEP 6: Generate enrichment reply (Gemini -> OpenAI fallback)")
    print("=" * 60)
    reply = generate_enrichment_reply(context_text, retrieved_context)
    if reply:
        print(f"Reply ({len(reply)} chars):\n{reply}")
    else:
        print("Reply: NO_REPLY (context adds no value)")


if __name__ == "__main__":
    asyncio.run(test_flow())

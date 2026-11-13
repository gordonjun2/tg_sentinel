import json
import os

from linkedin.mapping import (
    PostKind,
    build_message_text,
    classify_and_map,
    permalink_for,
)

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "posts_samples.json")


def _samples():
    with open(_FIXTURES) as f:
        return json.load(f)


def test_permalink():
    assert permalink_for("urn:li:share:111") == (
        "https://www.linkedin.com/feed/update/urn:li:share:111"
    )


def test_classify_kinds():
    samples = _samples()
    assert classify_and_map(samples["text"]).kind == PostKind.TEXT
    assert classify_and_map(samples["image"]).kind == PostKind.IMAGE
    assert classify_and_map(samples["image"]).image_urns == ["urn:li:image:ABC"]
    multi = classify_and_map(samples["multi"])
    assert multi.kind == PostKind.MULTI_IMAGE
    assert multi.image_urns == ["urn:li:image:A", "urn:li:image:B"]
    art = classify_and_map(samples["article"])
    assert art.kind == PostKind.ARTICLE
    assert art.article_url == "https://example.com/post"
    assert classify_and_map(samples["video"]).kind == PostKind.VIDEO
    assert classify_and_map(samples["poll"]).kind == PostKind.SKIP


def test_build_message_appends_permalink_last():
    text = build_message_text("Hello", "https://www.linkedin.com/feed/update/urn:li:share:1", None, 4096)
    assert text.endswith("https://www.linkedin.com/feed/update/urn:li:share:1")
    assert text.startswith("Hello")


def test_build_message_includes_article_url():
    text = build_message_text(
        "Read",
        "https://www.linkedin.com/feed/update/urn:li:share:1",
        "https://example.com/x",
        4096,
    )
    assert "https://example.com/x" in text
    assert text.splitlines()[-1] == "https://www.linkedin.com/feed/update/urn:li:share:1"


def test_build_message_truncates_body_keeps_permalink():
    permalink = "https://www.linkedin.com/feed/update/urn:li:share:1"
    body = "x" * 5000
    text = build_message_text(body, permalink, None, max_chars=100)
    assert len(text) <= 100
    assert text.endswith(permalink)

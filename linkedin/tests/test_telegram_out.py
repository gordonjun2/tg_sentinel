from unittest.mock import MagicMock

from linkedin.telegram_out import send_media_group, send_photo, send_text


def test_send_text_ok():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    assert send_text("tok", -1001, "hi", session) is True
    kwargs = session.post.call_args.kwargs
    assert "parse_mode" not in kwargs["json"] or kwargs["json"]["parse_mode"] is None
    assert kwargs["json"]["text"] == "hi"


def test_send_photo_ok():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    assert send_photo("tok", -1001, b"img", "cap", session) is True


def test_send_media_group_ok():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    assert send_media_group("tok", -1001, [b"a", b"b"], "cap", session) is True
    args, kwargs = session.post.call_args
    assert "sendMediaGroup" in args[0]

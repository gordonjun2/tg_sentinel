from unittest.mock import MagicMock

from linkedin.client import LINKEDIN_API_VERSION, LinkedInClient


def test_list_organization_posts_parses_elements():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"elements": [{"id": "urn:li:share:1"}]}
    session.get.return_value = resp
    client = LinkedInClient(session, "tok")
    posts = client.list_organization_posts("123", count=10)
    assert posts == [{"id": "urn:li:share:1"}]
    args, kwargs = session.get.call_args
    assert "https://api.linkedin.com/rest/posts" in args[0]
    assert kwargs["headers"]["LinkedIn-Version"] == LINKEDIN_API_VERSION
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_get_image_download_url():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"downloadUrl": "https://media.example/img.jpg"}
    session.get.return_value = resp
    client = LinkedInClient(session, "tok")
    assert client.get_image_download_url("urn:li:image:ABC") == "https://media.example/img.jpg"


def test_list_posts_retries_once_on_429():
    session = MagicMock()
    bad = MagicMock(status_code=429, text="slow down")
    good = MagicMock(status_code=200)
    good.json.return_value = {"elements": []}
    session.get.side_effect = [bad, good]
    client = LinkedInClient(session, "tok")
    assert client.list_organization_posts("123") == []
    assert session.get.call_count == 2

from linkedin_oauth import build_authorize_url, extract_code


def test_build_authorize_url_contains_scopes():
    url = build_authorize_url("cid", "https://example.com/cb", "st")
    assert "client_id=cid" in url
    assert "r_organization_social" in url


def test_extract_code_from_redirect():
    assert (
        extract_code("https://example.com/cb?code=ABC&state=st") == "ABC"
    )

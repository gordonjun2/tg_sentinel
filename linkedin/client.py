import time
from typing import Any, Dict, List
from urllib.parse import quote

LINKEDIN_API_VERSION = "202511"
API_BASE = "https://api.linkedin.com/rest"


class LinkedInClient:
    def __init__(self, session, access_token: str):
        self.session = session
        self.access_token = access_token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def list_organization_posts(self, org_id: str, count: int = 50) -> List[Dict[str, Any]]:
        author = quote(f"urn:li:organization:{org_id}", safe="")
        url = (
            f"{API_BASE}/posts?author={author}&q=author&count={count}"
            f"&sortBy=CREATED&viewContext=READER"
        )
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 429:
            time.sleep(2)
            resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"list posts failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
        elements = payload.get("elements")
        return elements if isinstance(elements, list) else []

    def get_image_download_url(self, image_urn: str) -> str:
        encoded = quote(image_urn, safe="")
        url = f"{API_BASE}/images/{encoded}"
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"get image failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        download_url = (resp.json() or {}).get("downloadUrl")
        if not download_url:
            raise RuntimeError(f"image {image_urn} missing downloadUrl")
        return str(download_url)

    def download_bytes(self, url: str) -> bytes:
        resp = self.session.get(url, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"download failed HTTP {resp.status_code}")
        return resp.content

    def list_administered_orgs(self) -> List[Dict[str, Any]]:
        url = f"{API_BASE}/organizationAcls?q=roleAssignee"
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"organizationAcls failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        elements = (resp.json() or {}).get("elements")
        return elements if isinstance(elements, list) else []

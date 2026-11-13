# linkedin/tokens.py
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict

TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float


def bundle_from_oauth_response(payload: Dict[str, Any], now: float) -> TokenBundle:
    access = payload.get("access_token")
    if not access:
        raise ValueError("OAuth response missing access_token")
    refresh = payload.get("refresh_token") or ""
    expires_in = float(payload.get("expires_in") or 0)
    refresh_expires_in = float(payload.get("refresh_token_expires_in") or 0)
    return TokenBundle(
        access_token=str(access),
        refresh_token=str(refresh),
        expires_at=now + expires_in,
        refresh_expires_at=now + refresh_expires_in if refresh else 0.0,
    )


def needs_refresh(bundle: TokenBundle, now: float, skew_seconds: float) -> bool:
    return now >= (bundle.expires_at - skew_seconds)


def save_tokens(path: str, bundle: TokenBundle) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(asdict(bundle), f, indent=1)
    os.replace(tmp, path)


def load_tokens(path: str) -> TokenBundle:
    with open(path) as f:
        raw = json.load(f)
    for key in ("access_token", "refresh_token", "expires_at", "refresh_expires_at"):
        if key not in raw:
            raise ValueError(f"token file missing {key}")
    return TokenBundle(
        access_token=str(raw["access_token"]),
        refresh_token=str(raw["refresh_token"]),
        expires_at=float(raw["expires_at"]),
        refresh_expires_at=float(raw["refresh_expires_at"]),
    )


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str, session
) -> dict:
    if not refresh_token:
        raise ValueError("No refresh_token available; re-run linkedin_oauth.py")
    resp = session.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"LinkedIn token refresh failed HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def ensure_access_token(
    path: str,
    client_id: str,
    client_secret: str,
    session,
    now: float,
    skew_seconds: float,
) -> str:
    bundle = load_tokens(path)
    if not needs_refresh(bundle, now, skew_seconds):
        return bundle.access_token
    payload = refresh_access_token(
        client_id, client_secret, bundle.refresh_token, session
    )
    if "refresh_token" not in payload:
        payload = dict(payload)
        payload["refresh_token"] = bundle.refresh_token
        payload.setdefault(
            "refresh_token_expires_in",
            max(0.0, bundle.refresh_expires_at - now),
        )
    new_bundle = bundle_from_oauth_response(payload, now)
    save_tokens(path, new_bundle)
    return new_bundle.access_token

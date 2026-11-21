#!/usr/bin/env python3
"""Two-step non-interactive login for the group_inviter session.

Step 1 (requests the Telegram login code)::

    python inviter_login.py request --phone "+6591234567"

Step 2 (completes login with the code Telegram sent to the user's app)::

    python inviter_login.py confirm --code 12345
    python inviter_login.py confirm --code 12345 --password "2fa-pass"   # if 2FA is on

After step 2 succeeds, ``group_inviter.session`` is fully authorized and
``mass_group_invite.py --send`` runs headlessly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pyrogram import Client
from pyrogram.errors import PhoneCodeExpired, PhoneCodeInvalid, SessionPasswordNeeded

from config import TELEGRAM_API_KEY, TELEGRAM_HASH

SESSION = "group_inviter"
HASH_FILE = Path("/tmp/opencode/inviter_phone_code_hash.txt")


async def request_code(phone: str) -> None:
    app = Client(SESSION, api_id=TELEGRAM_API_KEY, api_hash=TELEGRAM_HASH, no_updates=True)
    await app.connect()
    try:
        sent = await app.send_code(phone)
        HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HASH_FILE.write_text(f"{phone}\n{sent.phone_code_hash}")
        print(f"Code sent to {phone}. Ask the user for the code, then run:")
        print(f'  python inviter_login.py confirm --code <code>')
    finally:
        await app.disconnect()


async def confirm_code(code: str, password: str | None) -> None:
    if not HASH_FILE.exists():
        sys.exit("No pending login — run 'python inviter_login.py request --phone ...' first.")
    phone, phone_code_hash = HASH_FILE.read_text().splitlines()

    app = Client(SESSION, api_id=TELEGRAM_API_KEY, api_hash=TELEGRAM_HASH, no_updates=True)
    await app.connect()
    try:
        try:
            await app.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
        except SessionPasswordNeeded:
            if not password:
                sys.exit("2FA is enabled on this account — re-run with --password <your 2FA password>.")
            await app.check_password(password)
        me = await app.get_me()
        HASH_FILE.unlink(missing_ok=True)
        print(f"Logged in as {getattr(me, 'first_name', '?')} (@{getattr(me, 'username', '?')}).")
        print("Session ready — you can now run mass_group_invite.py --send.")
    except (PhoneCodeInvalid, PhoneCodeExpired):
        sys.exit("Code invalid or expired — run the request step again for a fresh code.")
    finally:
        await app.disconnect()


async def stream_login(phone: str) -> None:
    """Single-connection login: send code, wait for the code to appear in a
    file (written by the agent when the user replies), sign in immediately."""
    code_file = Path("/tmp/opencode/inviter_code.txt")
    code_file.unlink(missing_ok=True)

    app = Client(SESSION, api_id=TELEGRAM_API_KEY, api_hash=TELEGRAM_HASH, no_updates=True)
    await app.connect()
    try:
        sent = await app.send_code(phone)
        print(f"[login] Code sent to {phone} — waiting for {code_file} ...", flush=True)

        code = None
        password = None
        for _ in range(300):  # up to 10 minutes
            await asyncio.sleep(2)
            if code_file.exists():
                lines = code_file.read_text().strip().splitlines()
                if lines and lines[0].strip():
                    code = lines[0].strip()
                    password = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
                    break
        if code is None:
            print("[login] Timed out waiting for code file.", flush=True)
            return

        try:
            await app.sign_in(phone_number=phone, phone_code_hash=sent.phone_code_hash, phone_code=code)
        except SessionPasswordNeeded:
            if not password:
                print("[login] 2FA needed — write the code line + password line into the file.", flush=True)
                return
            await app.check_password(password)

        me = await app.get_me()
        code_file.unlink(missing_ok=True)
        print(f"[login] SUCCESS — logged in as {getattr(me, 'first_name', '?')} "
              f"(@{getattr(me, 'username', '?')}). Session ready.", flush=True)
    except Exception as e:
        print(f"[login] FAILED — {type(e).__name__}: {e}", flush=True)
        raise
    finally:
        await app.disconnect()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    p_req = sub.add_parser("request")
    p_req.add_argument("--phone", required=True, help="Phone number in international format")
    p_conf = sub.add_parser("confirm")
    p_conf.add_argument("--code", required=True, help="Code Telegram sent to the user's app")
    p_conf.add_argument("--password", default=None, help="2FA password (only if enabled)")
    sub.add_parser("stream", help="single-connection login driven via /tmp/opencode/inviter_code.txt")
    args = p.parse_args()

    if args.cmd == "request":
        asyncio.run(request_code(args.phone))
    elif args.cmd == "stream":
        asyncio.run(stream_login(args.phone))
    else:
        asyncio.run(confirm_code(args.code, args.password))


if __name__ == "__main__":
    main()

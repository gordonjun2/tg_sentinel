"""One-shot interactive login creating the user-account session file.

Run once (locally or in tmux on the VPS)::

    python -m partner_message_summarisation.login

Enter the phone number and the code Telegram sends; the resulting session
file (default ``partner_message_summarisation/data/sentinel_listener.session``) is what the
service uses headlessly. Keep it secret — it grants full account access.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .listener import make_user_client

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("partner_message_summarisation.login")


async def main() -> None:
    app = make_user_client()
    async with app:
        me = await app.get_me()
        logger.info(
            "Logged in as %s (@%s) — session ready.",
            getattr(me, "first_name", "?"), getattr(me, "username", "?"),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(0)

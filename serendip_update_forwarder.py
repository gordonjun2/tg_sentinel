"""Forward Telegram updates from tg_sentinel polling to serendip-tg.

Telegram allows one ingress per bot token. This process owns getUpdates.
Serendip receives the same Update JSON at POST /bot/webhook (secret header)
without calling setWebhook.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from telegram import Update
from telegram.ext import Application, ContextTypes

from config import SERENDIP_UPDATE_FORWARD_SECRET, SERENDIP_UPDATE_FORWARD_URL

logger = logging.getLogger(__name__)

_BOT_DATA_CLIENT_KEY = "serendip_forward_client"
_FORWARD_TIMEOUT_SECONDS = 5.0


async def attach_forward_client(application: Application) -> None:
    if not SERENDIP_UPDATE_FORWARD_URL:
        logger.info("Serendip update forward disabled (SERENDIP_UPDATE_FORWARD_URL unset)")
        return
    application.bot_data[_BOT_DATA_CLIENT_KEY] = httpx.AsyncClient(
        timeout=_FORWARD_TIMEOUT_SECONDS
    )
    logger.info(
        "Serendip update forward enabled url=%s",
        SERENDIP_UPDATE_FORWARD_URL,
    )


async def close_forward_client(application: Application) -> None:
    client = application.bot_data.pop(_BOT_DATA_CLIENT_KEY, None)
    if client is not None:
        await client.aclose()


async def forward_update_to_serendip(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not SERENDIP_UPDATE_FORWARD_URL:
        return

    client = context.application.bot_data.get(_BOT_DATA_CLIENT_KEY)
    if client is None:
        logger.error(
            "Serendip forward client missing; skip update_id=%s",
            update.update_id,
        )
        return

    payload: dict[str, Any] = update.to_dict()
    headers = {
        "X-Telegram-Bot-Api-Secret-Token": SERENDIP_UPDATE_FORWARD_SECRET,
        "Content-Type": "application/json",
    }
    try:
        response = await client.post(
            SERENDIP_UPDATE_FORWARD_URL,
            json=payload,
            headers=headers,
        )
    except httpx.HTTPError:
        logger.exception(
            "Serendip update forward failed update_id=%s",
            update.update_id,
        )
        return

    if response.status_code >= 400:
        logger.error(
            "Serendip update forward HTTP %s update_id=%s body=%s",
            response.status_code,
            update.update_id,
            response.text[:500],
        )

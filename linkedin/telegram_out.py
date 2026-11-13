import json
from typing import List

TG_MESSAGE_MAX = 4096
TG_CAPTION_MAX = 1024
API = "https://api.telegram.org/bot{token}/{method}"


def send_text(bot_token: str, chat_id: int, text: str, session) -> bool:
    resp = session.post(
        API.format(token=bot_token, method="sendMessage"),
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    return resp.status_code == 200


def send_photo(
    bot_token: str, chat_id: int, photo_bytes: bytes, caption: str, session
) -> bool:
    resp = session.post(
        API.format(token=bot_token, method="sendPhoto"),
        data={"chat_id": str(chat_id), "caption": caption},
        files={"photo": ("image.jpg", photo_bytes, "image/jpeg")},
        timeout=60,
    )
    return resp.status_code == 200


def send_media_group(
    bot_token: str,
    chat_id: int,
    photos: List[bytes],
    caption: str,
    session,
) -> bool:
    if not photos:
        return False
    media = []
    files = {}
    for i, blob in enumerate(photos):
        name = f"file{i}"
        item = {"type": "photo", "media": f"attach://{name}"}
        if i == 0 and caption:
            item["caption"] = caption
        media.append(item)
        files[name] = (f"{name}.jpg", blob, "image/jpeg")
    resp = session.post(
        API.format(token=bot_token, method="sendMediaGroup"),
        data={"chat_id": str(chat_id), "media": json.dumps(media)},
        files=files,
        timeout=120,
    )
    return resp.status_code == 200

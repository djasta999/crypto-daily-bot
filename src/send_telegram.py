"""Send the daily message through Telegram Bot API."""

from __future__ import annotations

import logging
import os
import time

import requests

LOGGER = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_ATTEMPTS = 3
TIMEOUT = 30


def send_message(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT)
            if resp.status_code == 429:
                wait = 5 * attempt
                try:
                    wait = max(wait, int(resp.json().get("parameters", {}).get("retry_after", wait)))
                except Exception:  # noqa: BLE001
                    pass
                LOGGER.warning("Telegram 429, sleeping %ss", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            if not body.get("ok"):
                raise RuntimeError(f"Telegram API error: {body}")
            LOGGER.info("Telegram message sent")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOGGER.warning("Telegram send failed (attempt %s/%s): %s", attempt, MAX_ATTEMPTS, exc)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to send Telegram message: {last_error}")

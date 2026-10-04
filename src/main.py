"""Daily crypto summary bot: fetch → compute → format → Telegram → JSONL."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

from compute import build_snapshot  # noqa: E402
from fetch_data import fetch_all  # noqa: E402
from format_message import CRYPTO_NEWS_LIMIT, MACRO_NEWS_LIMIT, format_message  # noqa: E402
from send_telegram import send_message  # noqa: E402

DATA_DIR = ROOT / "data"
SNAPSHOTS_PATH = DATA_DIR / "snapshots.jsonl"
SENT_NEWS_PATH = DATA_DIR / "sent_news.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("crypto-daily-bot")


def load_local_env() -> None:
    """python-dotenv is for local testing only, not GitHub Actions."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        LOGGER.info("python-dotenv not installed; relying on process env")
        return
    load_dotenv(ROOT / ".env")


def watchlist_ids() -> list[str]:
    raw = os.environ.get("WATCHLIST", "solana,binancecoin,ripple")
    ids = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return [cid for cid in ids if cid not in {"bitcoin", "ethereum"}]


def load_sent_news(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        df = pd.read_json(path, lines=True)
    except ValueError:
        LOGGER.warning("Could not parse %s — starting fresh sent_news", path)
        return []
    if df.empty:
        return []
    rows: list[dict[str, str]] = []
    for rec in df.to_dict(orient="records"):
        url = str(rec.get("url") or "")
        sent_date = str(rec.get("sent_date") or "")
        if url and sent_date:
            rows.append({"url": url, "sent_date": sent_date[:10]})
    return rows


def filter_new_news(
    items: list[dict[str, str]],
    sent: list[dict[str, str]],
    today: str,
    limit: int,
) -> list[dict[str, str]]:
    cutoff = (datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=7)).isoformat()
    recent_urls = {row["url"] for row in sent if row["sent_date"] >= cutoff}
    chosen: list[dict[str, str]] = []
    for item in items:
        if item["url"] in recent_urls:
            continue
        chosen.append(item)
        recent_urls.add(item["url"])
        if len(chosen) >= limit:
            break
    return chosen


def write_sent_news(
    path: Path,
    existing: list[dict[str, str]],
    newly_sent: list[dict[str, str]],
    today: str,
) -> None:
    cutoff = (datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=30)).isoformat()
    kept = [row for row in existing if row["sent_date"] >= cutoff]
    seen = {row["url"] for row in kept}
    for item in newly_sent:
        if item["url"] in seen:
            continue
        kept.append({"url": item["url"], "sent_date": today})
        seen.add(item["url"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in kept:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_snapshot(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def main() -> int:
    load_local_env()
    wl = watchlist_ids()
    LOGGER.info("Watchlist: %s", wl)

    raw = fetch_all(wl)
    snapshot, extras = build_snapshot(raw, wl, SNAPSHOTS_PATH)
    today = snapshot["date"]

    sent = load_sent_news(SENT_NEWS_PATH)
    crypto_news = filter_new_news(raw.get("crypto_news") or [], sent, today, CRYPTO_NEWS_LIMIT)
    macro_news = filter_new_news(raw.get("macro_news") or [], sent, today, MACRO_NEWS_LIMIT)

    text = format_message(snapshot, extras, crypto_news, macro_news)
    LOGGER.info("Message preview:\n%s", text)

    send_ok = False
    try:
        send_message(text)
        send_ok = True
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Telegram send failed: %s", exc)

    append_snapshot(SNAPSHOTS_PATH, snapshot)
    LOGGER.info("Appended snapshot for %s", today)

    if send_ok:
        write_sent_news(SENT_NEWS_PATH, sent, crypto_news + macro_news, today)
        LOGGER.info("Updated sent_news (%s new URLs)", len(crypto_news) + len(macro_news))
    else:
        LOGGER.warning("Skipping sent_news update because Telegram send failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

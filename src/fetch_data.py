"""Fetch market data and RSS news. One failed source must not abort the run."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import requests

LOGGER = logging.getLogger(__name__)

COINGECKO = "https://api.coingecko.com/api/v3"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"
FNG_URL = "https://api.alternative.me/fng/"

CRYPTO_RSS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
]

# Reuters Business first; BBC Business as a stable fallback (Reuters feeds often 403).
MACRO_RSS = [
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]

DEFAULT_TIMEOUT = 25
MAX_ATTEMPTS = 3
RETRY_SLEEP_SEC = 4

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "crypto-daily-bot/1.0 (github-actions; educational)",
        "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
    }
)


def _coingecko_headers() -> dict[str, str]:
    """Optional free Demo API key (not required). Helps avoid 429 on market_chart."""
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if not key:
        return {}
    return {"x-cg-demo-api-key": key}


def _gecko_chart_pause() -> None:
    time.sleep(1.2 if os.environ.get("COINGECKO_API_KEY", "").strip() else 8.0)


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any | None:
    last_error: Exception | None = None
    headers = _coingecko_headers() if url.startswith(COINGECKO) else {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=DEFAULT_TIMEOUT, headers=headers or None)
            if resp.status_code == 429:
                wait = 20 * attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, int(retry_after))
                LOGGER.warning("429 from %s — retry in %ss (attempt %s)", url, wait, attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — skip source, never fail the whole run
            last_error = exc
            LOGGER.warning("GET %s failed (attempt %s/%s): %s", url, attempt, MAX_ATTEMPTS, exc)
            time.sleep(RETRY_SLEEP_SEC * attempt)
    LOGGER.error("Skipping source after retries: %s (%s)", url, last_error)
    return None


def fetch_fear_greed() -> dict[str, Any] | None:
    payload = _get_json(FNG_URL, params={"limit": 1})
    if not payload or "data" not in payload or not payload["data"]:
        return None
    row = payload["data"][0]
    try:
        return {
            "value": int(row["value"]),
            "label": str(row.get("value_classification") or ""),
        }
    except (KeyError, TypeError, ValueError):
        LOGGER.error("Unexpected Fear & Greed payload: %s", row)
        return None


def fetch_global() -> dict[str, Any] | None:
    payload = _get_json(f"{COINGECKO}/global")
    if not payload or "data" not in payload:
        return None
    data = payload["data"]
    try:
        return {
            "btc_dominance_pct": float(data["market_cap_percentage"]["btc"]),
            "total_market_cap_usd": float(data["total_market_cap"]["usd"]),
            "total_volume_24h_usd": float(data["total_volume"]["usd"]),
        }
    except (KeyError, TypeError, ValueError):
        LOGGER.error("Unexpected CoinGecko global payload")
        return None


def fetch_markets(coin_ids: list[str]) -> list[dict[str, Any]]:
    ids = ",".join(dict.fromkeys(coin_ids))
    payload = _get_json(
        f"{COINGECKO}/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": ids,
            "price_change_percentage": "90d",
        },
    )
    if not isinstance(payload, list):
        return []
    return payload


def fetch_top50() -> list[dict[str, Any]]:
    payload = _get_json(
        f"{COINGECKO}/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 50,
            "page": 1,
            "price_change_percentage": "90d",
        },
    )
    if not isinstance(payload, list):
        return []
    return payload


def fetch_btc_history() -> list[list[float]]:
    payload = _get_json(
        f"{COINGECKO}/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": "max"},
    )
    if payload and isinstance(payload.get("prices"), list) and payload["prices"]:
        return payload["prices"]

    # days=max is often blocked/rate-limited on the keyless public API; stitch yearly ranges.
    LOGGER.info("Falling back to CoinGecko market_chart/range for BTC history")
    points: list[list[float]] = []
    year = 2013
    now = datetime.now(timezone.utc)
    while year <= now.year:
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if end > now:
            end = now
        chunk = _get_json(
            f"{COINGECKO}/coins/bitcoin/market_chart/range",
            params={
                "vs_currency": "usd",
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
            },
        )
        if chunk and isinstance(chunk.get("prices"), list):
            points.extend(chunk["prices"])
        _gecko_chart_pause()
        year += 1
    return points


def pct_change_from_chart(prices: list[list[float]], days: int = 90) -> float | None:
    if not prices:
        return None
    cutoff_ms = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000.0
    start_px = None
    end_px = None
    for point in prices:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            ts_ms = float(point[0])
            px = float(point[1])
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        if ts_ms >= cutoff_ms and start_px is None:
            start_px = px
        end_px = px
    if start_px is None or end_px is None or start_px <= 0:
        return None
    return (end_px / start_px - 1.0) * 100.0


def fetch_coin_chart(coin_id: str, days: int = 90) -> list[list[float]]:
    payload = _get_json(
        f"{COINGECKO}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": str(days)},
    )
    if not payload or "prices" not in payload:
        return []
    prices = payload["prices"]
    return prices if isinstance(prices, list) else []


def attach_90d_changes(top50: list[dict[str, Any]], btc_history: list[list[float]]) -> None:
    """CoinGecko public markets endpoint does not return 90d; fill from market_chart.

    BTC reuses the rainbow `days=max` series. Other coins get `days=90` with a short
    delay so the public rate limit is less likely to abort the whole index.
    """
    if not top50:
        return
    sample = top50[0]
    if sample.get("price_change_percentage_90d_in_currency") is not None:
        LOGGER.info("90d change already present on markets payload")
        return

    btc_90 = pct_change_from_chart(btc_history, days=90)
    for coin in top50:
        cid = coin.get("id")
        if not cid:
            continue
        if cid == "bitcoin":
            coin["price_change_percentage_90d_in_currency"] = btc_90
            continue
        LOGGER.info("Fetching 90d chart for %s", cid)
        chart = fetch_coin_chart(str(cid), days=90)
        coin["price_change_percentage_90d_in_currency"] = pct_change_from_chart(chart, days=90)
        _gecko_chart_pause()


def fetch_funding_rate(symbol: str) -> float | None:
    payload = _get_json(f"{BINANCE_FAPI}/premiumIndex", params={"symbol": symbol})
    if not isinstance(payload, dict):
        return None
    raw = payload.get("lastFundingRate")
    try:
        return float(raw)
    except (TypeError, ValueError):
        LOGGER.error("Unexpected funding rate for %s: %s", symbol, raw)
        return None


def fetch_stablecoin_market_cap() -> float | None:
    payload = _get_json(f"{COINGECKO}/coins/categories")
    if not isinstance(payload, list):
        return None
    for cat in payload:
        cid = str(cat.get("id") or "").lower()
        name = str(cat.get("name") or "").lower()
        if cid == "stablecoins" or name == "stablecoins":
            try:
                return float(cat["market_cap"])
            except (KeyError, TypeError, ValueError):
                LOGGER.error("Stablecoin category missing market_cap")
                return None
    LOGGER.error("Stablecoins category not found in CoinGecko categories")
    return None


def _rss_entries(url: str, limit: int) -> list[dict[str, str]]:
    last_error: Exception | None = None
    parsed = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = _SESSION.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.get("bozo") and not parsed.entries:
                raise RuntimeError(parsed.get("bozo_exception") or "empty RSS")
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOGGER.warning("RSS %s failed (attempt %s/%s): %s", url, attempt, MAX_ATTEMPTS, exc)
            parsed = None
            err = str(exc).lower()
            if "getaddrinfo" in err or "nameresolution" in err or "failed to resolve" in err:
                break
            time.sleep(RETRY_SLEEP_SEC * attempt)
    if parsed is None:
        LOGGER.error("Skipping RSS after retries: %s (%s)", url, last_error)
        return []
    items: list[dict[str, str]] = []
    for entry in parsed.entries[:limit]:
        link = str(entry.get("link") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not link or not title:
            continue
        items.append({"url": link, "title": title})
    return items


def fetch_crypto_news(per_feed: int = 8) -> list[dict[str, str]]:
    news: list[dict[str, str]] = []
    seen: set[str] = set()
    for source, url in CRYPTO_RSS:
        for item in _rss_entries(url, per_feed):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            news.append({**item, "source": source})
    return news


def fetch_macro_news(max_headlines: int = 3) -> list[dict[str, str]]:
    news: list[dict[str, str]] = []
    seen: set[str] = set()
    for source, url in MACRO_RSS:
        for item in _rss_entries(url, max_headlines):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            news.append({**item, "source": source})
            if len(news) >= max_headlines:
                return news
        if news:
            # Prefer the first source that actually returned items (Reuters if it works).
            return news[:max_headlines]
    return news[:max_headlines]


def fetch_all(watchlist_ids: list[str]) -> dict[str, Any]:
    """Collect every source independently. Missing keys are None / empty."""
    market_ids = ["bitcoin", "ethereum", *watchlist_ids]
    LOGGER.info("Fetching Fear & Greed")
    fear_greed = fetch_fear_greed()
    LOGGER.info("Fetching CoinGecko global")
    global_data = fetch_global()
    LOGGER.info("Fetching markets %s", market_ids)
    markets = fetch_markets(market_ids)
    LOGGER.info("Fetching top 50 coins")
    top50 = fetch_top50()
    LOGGER.info("Fetching BTC history for Rainbow Chart")
    btc_history = fetch_btc_history()
    LOGGER.info("Attaching 90d returns for Altcoin Season Index")
    attach_90d_changes(top50, btc_history)
    LOGGER.info("Fetching Binance funding rates")
    funding_btc = fetch_funding_rate("BTCUSDT")
    funding_eth = fetch_funding_rate("ETHUSDT")
    LOGGER.info("Fetching stablecoin category")
    stable_mcap = fetch_stablecoin_market_cap()
    LOGGER.info("Fetching crypto RSS")
    crypto_news = fetch_crypto_news()
    LOGGER.info("Fetching macro RSS")
    macro_news = fetch_macro_news(max_headlines=3)
    return {
        "fear_greed": fear_greed,
        "global": global_data,
        "markets": markets,
        "top50": top50,
        "btc_history": btc_history,
        "funding_btc": funding_btc,
        "funding_eth": funding_eth,
        "stablecoin_market_cap": stable_mcap,
        "crypto_news": crypto_news,
        "macro_news": macro_news,
    }

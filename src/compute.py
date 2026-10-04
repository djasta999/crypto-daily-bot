"""Derived indicators: Altcoin Season Index, BTC Rainbow band, day-over-day deltas.

Rainbow Chart references (there is no single official formula):
- Trolololo (Bitcointalk, 2014-10-22), "Logarithmic (non-linear) regression - Bitcoin
  estimated value": https://bitcointalk.org/index.php?topic=831547
  Original static fit used weeks since 2009-01-09; later superseded.
- Rohmeo / BlockchainCenter merged Trolololo's log regression with azop's rainbow
  bands: https://www.blockchaincenter.net/bitcoin-rainbow-chart/
  Their currently published approach is a *dynamic* power-law fit on daily prices
  (they use history since 2012, not the retired static y = 2.9065 ln(x) - 19.493).
- Band geometry commonly described as 9 log-spaced bands of ~2x each around the
  fitted center line: https://btc.network/rainbow
    log10(price) = a * log10(days_since_genesis) + b
    genesis = 2009-01-03

This module refits a and b every run from CoinGecko daily BTC prices (from 2012-01-01
onward, matching BlockchainCenter's dynamic-fit window). Band labels follow the
classic 9-band BlockchainCenter names. Band width is log10(2) (~2x per band), with
the fitted line at the midpoint of the middle band ("HODL!").
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

BTC_GENESIS = datetime(2009, 1, 3, tzinfo=timezone.utc)
RAINBOW_FIT_START = datetime(2012, 1, 1, tzinfo=timezone.utc)

# Altcoin Season Index (BlockchainCenter convention):
# https://www.blockchaincenter.net/en/altcoin-season-index/
# Altcoin Season if >= 75% of top coins beat BTC over 90d; Bitcoin Season if <= 25%.
ALTSEASON_THRESHOLD = 75
BTC_SEASON_THRESHOLD = 25

# alternative.me Fear & Greed buckets (API also returns value_classification):
# https://alternative.me/crypto/fear-and-greed-index/
FNG_BANDS: list[tuple[int, int, str]] = [
    (0, 24, "Extreme Fear"),
    (25, 49, "Fear"),
    (50, 54, "Neutral"),
    (55, 74, "Greed"),
    (75, 100, "Extreme Greed"),
]

RAINBOW_BANDS = [
    "Basically a Fire Sale",
    "BUY!",
    "Accumulate",
    "Still cheap",
    "HODL!",
    "Is this a bubble?",
    "FOMO intensifies",
    "Sell. Seriously, SELL!",
    "Maximum Bubble Territory",
]


def fng_label(value: int) -> str:
    for lo, hi, name in FNG_BANDS:
        if lo <= value <= hi:
            return name
    return ""


def altseason_label(index: int) -> str:
    if index >= ALTSEASON_THRESHOLD:
        return "Altcoin Season"
    if index <= BTC_SEASON_THRESHOLD:
        return "Bitcoin Season"
    return "Neither (in between)"


def load_last_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        df = pd.read_json(path, lines=True)
    except ValueError as exc:
        LOGGER.warning("Could not parse %s: %s — skipping delta", path, exc)
        return None
    if df.empty:
        return None
    row = df.iloc[-1]
    return row.to_dict()


def compute_altcoin_season_index(top50: list[dict[str, Any]]) -> int | None:
    """Share of top-50 coins (excluding BTC) with 90d return > BTC 90d return."""
    if not top50:
        return None
    btc = next((c for c in top50 if c.get("id") == "bitcoin"), None)
    if btc is None:
        LOGGER.warning("Bitcoin missing from top 50 — cannot compute altseason")
        return None
    btc_90 = btc.get("price_change_percentage_90d_in_currency")
    if btc_90 is None:
        LOGGER.warning("BTC 90d change missing — cannot compute altseason")
        return None
    outperform = 0
    counted = 0
    for coin in top50:
        if coin.get("id") == "bitcoin":
            continue
        chg = coin.get("price_change_percentage_90d_in_currency")
        if chg is None:
            continue
        counted += 1
        if float(chg) > float(btc_90):
            outperform += 1
    if counted == 0:
        return None
    return int(round(100 * outperform / counted))


def compute_rainbow_band(history: list[list[float]], spot_price: float | None) -> str:
    """Fit log10(price) = a * log10(days_since_genesis) + b; map spot to a band."""
    if not history or spot_price is None or spot_price <= 0:
        return ""

    days: list[float] = []
    prices: list[float] = []
    for point in history:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts_ms, price = point[0], point[1]
        try:
            ts = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
            px = float(price)
        except (TypeError, ValueError, OSError):
            continue
        if ts < RAINBOW_FIT_START or px <= 0:
            continue
        age = (ts - BTC_GENESIS).total_seconds() / 86400.0
        if age <= 1:
            continue
        days.append(age)
        prices.append(px)

    if len(days) < 30:
        LOGGER.warning("Not enough BTC history points for rainbow fit (%s)", len(days))
        return ""

    x = np.log10(np.asarray(days, dtype=float))
    y = np.log10(np.asarray(prices, dtype=float))
    slope, intercept = np.polyfit(x, y, 1)

    now = datetime.now(timezone.utc)
    days_now = (now - BTC_GENESIS).total_seconds() / 86400.0
    center_log10 = float(slope * np.log10(days_now) + intercept)

    # 9 bands, each ~2x wide; fitted line is the midpoint of band index 4 ("HODL!").
    band_width = float(np.log10(2.0))
    spot_log10 = float(np.log10(spot_price))
    # Distance from the bottom edge of band 0, in units of band width.
    bottom_edge = center_log10 - 4.5 * band_width
    idx = int(np.floor((spot_log10 - bottom_edge) / band_width))
    idx = max(0, min(len(RAINBOW_BANDS) - 1, idx))
    LOGGER.info(
        "Rainbow fit: log10(price) = %.6f * log10(days) + %.6f; band=%s",
        slope,
        intercept,
        RAINBOW_BANDS[idx],
    )
    return RAINBOW_BANDS[idx]


def _as_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, dict):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_deltas(today: dict[str, Any], yesterday: dict[str, Any] | None) -> dict[str, Any]:
    if not yesterday:
        return {}
    deltas: dict[str, Any] = {}
    numeric_keys = [
        "btc_dominance_pct",
        "altcoin_season_index",
        "btc_price_usd",
        "eth_price_usd",
        "total_market_cap_usd",
        "total_volume_24h_usd",
        "stablecoin_dominance_pct",
    ]
    zero_means_missing = {
        "btc_price_usd",
        "eth_price_usd",
        "total_market_cap_usd",
        "total_volume_24h_usd",
        "btc_dominance_pct",
        "stablecoin_dominance_pct",
    }
    for key in numeric_keys:
        cur = _as_float(today.get(key))
        prev = _as_float(yesterday.get(key))
        if cur is None or prev is None:
            continue
        if key in zero_means_missing and (cur == 0.0 or prev == 0.0):
            continue
        deltas[key] = cur - prev

    today_fg = today.get("fear_greed") or {}
    y_fg = yesterday.get("fear_greed") or {}
    if not isinstance(y_fg, dict):
        try:
            y_fg = json.loads(y_fg) if isinstance(y_fg, str) else {}
        except json.JSONDecodeError:
            y_fg = {}
    cur_v = today_fg.get("value") if isinstance(today_fg, dict) else None
    prev_v = y_fg.get("value") if isinstance(y_fg, dict) else None
    prev_label = y_fg.get("label") if isinstance(y_fg, dict) else ""
    if prev_v == 0 and not prev_label:
        prev_v = None
    if cur_v == 0 and not (today_fg.get("label") if isinstance(today_fg, dict) else ""):
        cur_v = None
    if cur_v is not None and prev_v is not None:
        try:
            deltas["fear_greed"] = int(cur_v) - int(prev_v)
            deltas["fear_greed_prev"] = int(prev_v)
        except (TypeError, ValueError):
            pass

    today_fr = today.get("funding_rate") or {}
    y_fr = yesterday.get("funding_rate") or {}
    if not isinstance(y_fr, dict):
        try:
            y_fr = json.loads(y_fr) if isinstance(y_fr, str) else {}
        except json.JSONDecodeError:
            y_fr = {}
    if isinstance(today_fr, dict) and isinstance(y_fr, dict):
        for asset in ("BTC", "ETH"):
            cur = _as_float(today_fr.get(asset))
            prev = _as_float(y_fr.get(asset))
            if cur is None or prev is None:
                continue
            deltas.setdefault("funding_rate", {})[asset] = cur - prev

    y_watch = yesterday.get("watchlist") or {}
    if not isinstance(y_watch, dict):
        try:
            y_watch = json.loads(y_watch) if isinstance(y_watch, str) else {}
        except json.JSONDecodeError:
            y_watch = {}
    watch_deltas: dict[str, float] = {}
    for coin, price in (today.get("watchlist") or {}).items():
        prev = _as_float(y_watch.get(coin))
        cur = _as_float(price)
        if cur is None or prev is None:
            continue
        watch_deltas[coin] = cur - prev
    if watch_deltas:
        deltas["watchlist"] = watch_deltas
    return deltas


def _market_price(markets: list[dict[str, Any]], coin_id: str) -> float | None:
    row = next((c for c in markets if c.get("id") == coin_id), None)
    if not row:
        return None
    return _as_float(row.get("current_price"))


def build_snapshot(
    raw: dict[str, Any],
    watchlist_ids: list[str],
    snapshots_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    markets = raw.get("markets") or []
    global_data = raw.get("global") or {}
    fear_greed = raw.get("fear_greed")
    btc_price = _market_price(markets, "bitcoin")
    eth_price = _market_price(markets, "ethereum")
    total_mcap = _as_float(global_data.get("total_market_cap_usd")) if global_data else None
    stable_mcap = _as_float(raw.get("stablecoin_market_cap"))
    stable_dom = None
    if total_mcap and stable_mcap is not None and total_mcap > 0:
        stable_dom = 100.0 * stable_mcap / total_mcap

    alt_idx = compute_altcoin_season_index(raw.get("top50") or [])
    rainbow = compute_rainbow_band(raw.get("btc_history") or [], btc_price)

    fg_value = 0
    fg_label = ""
    if fear_greed:
        fg_value = int(fear_greed["value"])
        fg_label = str(fear_greed.get("label") or fng_label(fg_value))

    watchlist: dict[str, float] = {}
    for cid in watchlist_ids:
        px = _market_price(markets, cid)
        if px is not None:
            watchlist[cid] = px

    funding_btc = raw.get("funding_btc")
    funding_eth = raw.get("funding_eth")

    snapshot = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "fear_greed": {"value": fg_value, "label": fg_label},
        "btc_dominance_pct": float(global_data.get("btc_dominance_pct") or 0.0),
        "altcoin_season_index": int(alt_idx) if alt_idx is not None else 0,
        "btc_rainbow_band": rainbow,
        "btc_price_usd": float(btc_price or 0),
        "eth_price_usd": float(eth_price or 0),
        "total_market_cap_usd": float(total_mcap or 0),
        "total_volume_24h_usd": float(global_data.get("total_volume_24h_usd") or 0.0),
        "stablecoin_dominance_pct": float(stable_dom or 0.0),
        "funding_rate": {
            "BTC": float(funding_btc) if funding_btc is not None else 0.0,
            "ETH": float(funding_eth) if funding_eth is not None else 0.0,
        },
        "watchlist": watchlist,
    }

    yesterday = load_last_snapshot(snapshots_path)
    deltas = compute_deltas(snapshot, yesterday)
    extras = {
        "deltas": deltas,
        "altseason_label": altseason_label(snapshot["altcoin_season_index"]) if alt_idx is not None else "",
        "altseason_available": alt_idx is not None,
        "rainbow_available": bool(rainbow),
        "fear_greed_available": fear_greed is not None,
        "global_available": bool(global_data),
        "stable_available": stable_dom is not None,
        "funding_btc_available": funding_btc is not None,
        "funding_eth_available": funding_eth is not None,
        "markets_by_id": {c.get("id"): c for c in markets if c.get("id")},
    }
    return snapshot, extras

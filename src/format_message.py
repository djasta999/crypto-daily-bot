"""Build the daily Telegram text (Indonesian, matching the spec example)."""

from __future__ import annotations

from typing import Any

from compute import FNG_BANDS, fng_label

TELEGRAM_MAX = 4096
CRYPTO_NEWS_LIMIT = 5
MACRO_NEWS_LIMIT = 3


def _fmt_usd(n: float) -> str:
    abs_n = abs(n)
    if abs_n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if abs_n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if abs_n >= 1e6:
        return f"${n / 1e6:.2f}M"
    if abs_n >= 1:
        return f"${n:,.2f}"
    return f"${n:.4f}"


def _fmt_pct(n: float, digits: int = 2) -> str:
    return f"{n:.{digits}f}%"


def _dir_phrase(current: float, previous: float | None, unit: str = "") -> str:
    if previous is None:
        return ""
    if current > previous:
        verb = "naik dari"
    elif current < previous:
        verb = "turun dari"
    else:
        return "tetap"
    if unit == "usd":
        return f"{verb} {_fmt_usd(previous)}"
    if unit == "pp":
        return f"{verb} {previous:.2f}"
    if unit == "int":
        return f"{verb} {int(round(previous))}"
    if unit == "funding":
        return f"{verb} {previous * 100:.4f}%"
    return f"{verb} {previous}"


def _fng_near_next(value: int) -> str:
    """Match the spec example: Greed is described as approaching Extreme Greed."""
    label = fng_label(value)
    if label == "Greed":
        return "mendekati Extreme Greed"
    if label == "Fear":
        return "mendekati Neutral" if value >= 37 else "mendekati Extreme Fear"
    if label == "Neutral":
        return "mendekati Greed" if value >= 52 else "mendekati Fear"
    return ""


def _paren(*parts: str) -> str:
    cleaned = [p for p in parts if p]
    if not cleaned:
        return ""
    return "(" + ", ".join(cleaned) + ")"


def _prev_numeric(deltas: dict[str, Any], key: str, current: float) -> float | None:
    if key not in deltas:
        return None
    return current - float(deltas[key])


def format_message(
    snapshot: dict[str, Any],
    extras: dict[str, Any],
    crypto_news: list[dict[str, str]],
    macro_news: list[dict[str, str]],
) -> str:
    d = extras.get("deltas") or {}
    date = snapshot["date"]
    lines: list[str] = [f"Crypto Daily — {date}", ""]

    fg = snapshot.get("fear_greed") or {}
    fg_val = int(fg.get("value") or 0)
    if extras.get("fear_greed_available"):
        prev = d.get("fear_greed_prev")
        change = _dir_phrase(fg_val, float(prev) if prev is not None else None, "int")
        near = _fng_near_next(fg_val)
        suffix = _paren(change, near)
        lines.append(f"Fear & Greed: {fg_val} {suffix}".rstrip())
    else:
        lines.append("Fear & Greed: n/a")

    if extras.get("global_available"):
        btc_d = float(snapshot["btc_dominance_pct"])
        prev = _prev_numeric(d, "btc_dominance_pct", btc_d)
        extra = _paren(_dir_phrase(btc_d, prev, "pp"))
        lines.append(f"BTC Dominance: {_fmt_pct(btc_d)} {extra}".rstrip())
    else:
        lines.append("BTC Dominance: n/a")

    if extras.get("altseason_available"):
        idx = int(snapshot["altcoin_season_index"])
        prev = _prev_numeric(d, "altcoin_season_index", float(idx))
        extra = _paren(
            _dir_phrase(float(idx), prev, "int"),
            extras.get("altseason_label") or "",
        )
        lines.append(f"Altcoin Season Index: {idx} {extra}".rstrip())
    else:
        lines.append("Altcoin Season Index: n/a")

    rainbow = snapshot.get("btc_rainbow_band") or "n/a"
    lines.append(f"BTC Rainbow Band: {rainbow}")
    lines.append("")

    btc_px = float(snapshot["btc_price_usd"])
    eth_px = float(snapshot["eth_price_usd"])
    btc_prev = _prev_numeric(d, "btc_price_usd", btc_px)
    eth_prev = _prev_numeric(d, "eth_price_usd", eth_px)
    lines.append(
        f"BTC: {_fmt_usd(btc_px)} {_paren(_dir_phrase(btc_px, btc_prev, 'usd'))}".rstrip()
        if btc_px
        else "BTC: n/a"
    )
    lines.append(
        f"ETH: {_fmt_usd(eth_px)} {_paren(_dir_phrase(eth_px, eth_prev, 'usd'))}".rstrip()
        if eth_px
        else "ETH: n/a"
    )

    watch = snapshot.get("watchlist") or {}
    watch_d = d.get("watchlist") or {}
    if watch:
        lines.append("Watchlist:")
        markets = extras.get("markets_by_id") or {}
        for cid, price in watch.items():
            prev = None
            if cid in watch_d:
                prev = float(price) - float(watch_d[cid])
            symbol = (markets.get(cid) or {}).get("symbol") or cid
            symbol = str(symbol).upper()
            extra = _paren(_dir_phrase(float(price), prev, "usd"))
            lines.append(f"  {symbol}: {_fmt_usd(float(price))} {extra}".rstrip())
    lines.append("")

    mcap = float(snapshot["total_market_cap_usd"])
    vol = float(snapshot["total_volume_24h_usd"])
    if extras.get("global_available"):
        mcap_prev = _prev_numeric(d, "total_market_cap_usd", mcap)
        vol_prev = _prev_numeric(d, "total_volume_24h_usd", vol)
        lines.append(
            f"Total Market Cap: {_fmt_usd(mcap)} {_paren(_dir_phrase(mcap, mcap_prev, 'usd'))}".rstrip()
        )
        lines.append(
            f"Volume 24h: {_fmt_usd(vol)} {_paren(_dir_phrase(vol, vol_prev, 'usd'))}".rstrip()
        )
    else:
        lines.append("Total Market Cap: n/a")
        lines.append("Volume 24h: n/a")

    if extras.get("stable_available"):
        sd = float(snapshot["stablecoin_dominance_pct"])
        sd_prev = _prev_numeric(d, "stablecoin_dominance_pct", sd)
        lines.append(
            f"Stablecoin Dominance: {_fmt_pct(sd)} {_paren(_dir_phrase(sd, sd_prev, 'pp'))}".rstrip()
        )
    else:
        lines.append("Stablecoin Dominance: n/a")

    fr = snapshot.get("funding_rate") or {}
    fr_d = d.get("funding_rate") or {}
    parts = []
    for asset, available_key in (("BTC", "funding_btc_available"), ("ETH", "funding_eth_available")):
        if not extras.get(available_key):
            parts.append(f"{asset} n/a")
            continue
        rate = float(fr.get(asset) or 0.0)
        prev = None
        if asset in fr_d:
            prev = rate - float(fr_d[asset])
        extra = _paren(_dir_phrase(rate, prev, "funding"))
        parts.append(f"{asset} {rate * 100:.4f}% {extra}".rstrip())
    lines.append("Funding Rate: " + " | ".join(parts))
    lines.append("")

    if crypto_news:
        lines.append("Crypto news:")
        for item in crypto_news[:CRYPTO_NEWS_LIMIT]:
            lines.append(f"- {item['title']} ({item.get('source', '')})")
            lines.append(f"  {item['url']}")
        lines.append("")

    if macro_news:
        lines.append("Makro:")
        for item in macro_news[:MACRO_NEWS_LIMIT]:
            lines.append(f"- {item['title']} ({item.get('source', '')})")
            lines.append(f"  {item['url']}")

    text = "\n".join(lines).strip() + "\n"
    if len(text) > TELEGRAM_MAX:
        text = text[: TELEGRAM_MAX - 20].rstrip() + "\n…(truncated)\n"
    return text

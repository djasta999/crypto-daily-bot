# crypto-daily-bot

Bot Python harian: ringkasan indikator pasar crypto + headline news, dikirim ke Telegram. Dijalankan oleh GitHub Actions (cron), bukan server yang hidup terus-menerus.

MVRV **tidak** disertakan. Sumber MVRV yang akurat mewajibkan API key berbayar.

## Isi pesan

- Fear & Greed Index (alternative.me)
- BTC Dominance, Total Market Cap, Volume 24h (CoinGecko global)
- Harga BTC / ETH + watchlist
- Altcoin Season Index (top 50 vs BTC, 90 hari)
- BTC Rainbow Band (regresi power-law pada harga historis)
- Stablecoin Dominance (kategori CoinGecko `stablecoins`)
- Funding rate BTC/ETH (Binance Futures public)
- News crypto (CoinDesk, CoinTelegraph, Decrypt)
- 2–3 headline makro (Reuters Business, fallback BBC Business)

## Setup secrets (GitHub Actions)

1. Buat bot lewat [@BotFather](https://t.me/BotFather), salin token.
2. Dapatkan chat id (pesan ke bot, lalu panggil `https://api.telegram.org/bot<TOKEN>/getUpdates`, atau pakai [@userinfobot](https://t.me/userinfobot) untuk chat pribadi).
3. Di repo GitHub: **Settings → Secrets and variables → Actions → New repository secret**, tambahkan:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Workflow `.github/workflows/daily.yml` memakai kedua secret itu dan `contents: write` untuk commit-back file di `data/`.

Jadwal: `cron: "0 23 * * *"` (23:00 UTC = 07:00 WITA), plus `workflow_dispatch`.

## Trigger manual workflow

1. Buka tab **Actions** di GitHub.
2. Pilih workflow **Daily crypto summary**.
3. **Run workflow** → pilih branch utama → **Run workflow**.

Pastikan Actions diizinkan untuk push (default `GITHUB_TOKEN` cukup selama permission `contents: write` aktif dan branch tidak dilindungi tanpa pengecualian untuk GitHub Actions).

## Run lokal (testing)

Python 3.12+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS/Linux
```

Isi `.env`:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
WATCHLIST=solana,binancecoin,ripple
```

`WATCHLIST` memakai id CoinGecko (bukan ticker). BTC dan ETH selalu dikirim terpisah.

Opsional: `COINGECKO_API_KEY` (Demo key gratis dari [CoinGecko](https://www.coingecko.com/en/api)). Public API tanpa key sering 429, terutama untuk `market_chart` (Rainbow + Altcoin Season). Tidak wajib; tanpa key bot tetap jalan dan me-skip sumber yang gagal.

Jalankan dari root repo:

```bash
python src/main.py
```

`python-dotenv` hanya dipakai di luar GitHub Actions. Di Actions, kredensial datang dari `env:` workflow, bukan file `.env`.

File `data/snapshots.jsonl` dan `data/sent_news.jsonl` akan bertambah. News yang URL-nya sudah dikirim dalam 7 hari terakhir di-skip; entri sent-news lebih dari 30 hari dihapus. Commit perubahan `data/` secara otomatis hanya terjadi di GitHub Actions, bukan dari skrip Python.

## Catatan API

CoinGecko public API bisa 429. Setiap call di-retry 2–3 kali; sumber yang gagal di-skip, run tetap lanjut. Rainbow Chart mencoba `market_chart?days=max`, lalu fallback ke `market_chart/range` per tahun. Altcoin Season memakai `market_chart?days=90` per koin top 50 karena endpoint `coins/markets` tidak mengembalikan `price_change_percentage_90d` di API publik. Run GitHub Actions bisa 10+ menit.

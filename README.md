# backtestgrid.py

> Simulate a grid trading bot on any KuCoin spot pair using real historical candle data — no API keys, no paid subscription.

> 💬 **Need a live bot based on this system?** Reach out on Telegram: [@smmgotop](https://t.me/smmgotop)

A pure-Python backtester that replays a configurable grid strategy candle-by-candle, tracking every order fill, fee, realized P&L, and portfolio balance. Results include a full trade log (CSV), open order breakdown, buy-and-hold comparison, and a quarterly performance split.

---

## Why this exists

Live grid bots are a black box: you set parameters and hope for the best. This tool lets you test parameter combinations on real historical data before risking capital — surfacing how sell spacing, re-entry thresholds, and order caps interact across different market conditions.

---

## Architecture

```
KuCoin API  (public, no auth)
    │
    └── fetch_ohlcv()  →  paginated candle fetch (up to 1500/request)
              │
              └── Backtester.step()  ─── per-candle simulation loop
                        │
                        ├── Candle snapshot  (prevents same-candle ping-pong)
                        ├── Fill SELL  →  check vs. candle HIGH
                        ├── Fill BUY   →  check vs. candle LOW
                        ├── Grid window check  (cancel BUY if spread too wide)
                        ├── Stale BUY cleanup  (no SELL remaining)
                        ├── Re-entry logic     (triggered by candle LOW)
                        └── Initial entry      (close price, first candle only)
                                  │
                                  ├── SQLite-free in-memory state
                                  ├── LIFO realized P&L tracking
                                  └── Optional CSV trade log  (--log)
                                            │
                                            └── Terminal output
                                                  (color-coded, quarterly split)
```

---

## Example output

```
  Fetching PRCL/USDT [1h]  2025-01-01 00:00 -> 2025-04-01 00:00
  Fetched: 2160 candles  up to 2025-04-01 00:00  [100%]
  Total: 2160 candles  2025-01-01 00:00 -> 2025-04-01 00:00

  Running 2160 candles...

──────────────────────────────────────────────────────────
  BACKTEST  PRCL/USDT  [1h]  2160 candles
  2025-01-01 00:00 -> 2025-04-01 00:00
──────────────────────────────────────────────────────────
  Params: sell+1.0%  buy-2.0%  win12.0%  re2.0%  sz$0.15  max=0
──────────────────────────────────────────────────────────
  Trades: 318  (159↑/159↓)  fee: $0.0421
  Realized: +1.2847$  (LIFO closed pairs)
  Price:    0.184200 -> 0.103700  buy&hold: -43.7%
  Start: $100.0000  Final: $101.3214
  Profit: +1.3214$   ROI: +1.32%
──────────────────────────────────────────────────────────

  Open orders at end of backtest:
    SELL 0.81521400 PRCL @ 0.10473700  (~$0.08540... at fill  now ~$0.08453...)
    BUY: none
    ─────
    SELL total: 1 orders  0.81521400 PRCL  ~$0.08453...
    BUY  total: 0 orders  $0.0000 USDT
    Free: $98.9872 USDT  + 0.0000 PRCL
    Total assets: $99.0717

  Quarterly breakdown (equal time segments):
    Q1 2025-01-01 00:00 -> 2025-01-23 06:00  82 trades (41↑/41↓)  realized:+0.4821$  fee:$0.0109
    Q2 2025-01-23 06:00 -> 2025-02-14 12:00  74 trades (37↑/37↓)  realized:+0.3912$  fee:$0.0098
    Q3 2025-02-14 12:00 -> 2025-03-08 18:00  96 trades (48↑/48↓)  realized:+0.2741$  fee:$0.0127
    Q4 2025-03-08 18:00 -> 2025-04-01 00:00  66 trades (33↑/33↓)  realized:+0.1373$  fee:$0.0087
```

Color coding: **Green** = positive · **Red** = negative

---

## Features

- **No API keys** — uses KuCoin public OHLCV endpoints via [ccxt](https://github.com/ccxt/ccxt)
- **Automatic pagination** — fetches as many 1500-candle batches as needed; deduplicates and sorts automatically
- **Snapshot-based fills** — orders placed during a candle cannot fill in the same candle, eliminating unrealistic ping-pong
- **Full fee accounting** — 0.1% taker fee applied at both buy and sell sides; fee reserved at order placement
- **LIFO realized P&L** — each sell is matched against the most recent buy cost basis
- **Re-entry logic** — re-enters when price drops N% below the lowest open SELL target
- **Grid window guard** — cancels stale BUY orders when the SELL-BUY spread exceeds the window threshold
- **Order cap** — optional maximum on simultaneous open orders
- **CSV trade log** — full per-trade export with running balances and realized P&L (via `--log`)
- **Quarterly breakdown** — equal time-segment split with per-quarter trade count, realized P&L, and fees
- **Buy-and-hold comparison** — displayed alongside bot ROI

---

## Requirements

| Dependency | Notes |
|---|---|
| Python ≥ 3.8 | |
| ccxt | Exchange connectivity |
| colorama | Terminal color output (optional — degrades gracefully) |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/goodgamefinder/kucoinbacktestgrid.git
cd kucoinbacktestgrid
```

### 2. Create and activate a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate.bat       # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Usage

```
python backtestgrid.py --coin TICKER [options]
```

### Required

| Argument | Description |
|---|---|
| `--coin` | Coin ticker without `/USDT` (e.g. `BTC`, `PRCL`, `ALLO`) |

### Date range

| Flag | Default | Description |
|---|---|---|
| `--days` | `90` | Number of days back from today |
| `--from` | — | Start date `YYYY-MM-DD` (overrides `--days`) |
| `--to` | today | End date `YYYY-MM-DD` |
| `--tf` | `1h` | Candle timeframe: `1m` `3m` `5m` `15m` `30m` `1h` `2h` `4h` `6h` `8h` `12h` `1d` `1w` |

### Grid parameters

| Flag | Default | Description |
|---|---|---|
| `--sell-pct` | `1.0` | Place SELL N% above the buy price |
| `--buy-drop` | `2.0` | Place BUY N% below the sell price after a fill |
| `--window-pct` | `12.0` | Cancel BUY if SELL-BUY spread exceeds N% |
| `--reentry-pct` | `2.0` | Re-enter when price drops N% below the lowest open SELL |
| `--size` | `0.15` | USDT amount per entry and re-entry |
| `--max-orders` | `0` | Max simultaneous open orders (`0` = unlimited) |

### Output

| Flag | Description |
|---|---|
| `--log FILE` | Save all trades to a CSV file (e.g. `--log trades.csv`) |

### Examples

```bash
# Quick run — last 90 days, 1h candles, default parameters
python backtestgrid.py --coin PRCL

# Custom date range, 15-minute candles
python backtestgrid.py --coin ALLO --tf 15m --from 2025-01-01 --to 2025-04-01

# Tighter grid, smaller position size, order cap, with CSV export
python backtestgrid.py --coin PRCL --sell-pct 0.5 --buy-drop 1.0 --size 0.10 --max-orders 10 --log trades.csv

# 4-hour candles over 6 months
python backtestgrid.py --coin DOGE --tf 4h --days 180
```

---

## How it works

1. **Candle fetch** — `fetch_ohlcv` is called in paginated batches of 1500, using `endAt` to avoid duplicates at batch boundaries. All batches are merged, deduplicated, and sorted by timestamp.
2. **Initial entry** — on the first candle with no open orders, the script buys `size / close` coins at the candle close price and immediately places a SELL `sell_pct%` above entry.
3. **Per-candle simulation (`step`):**
   - A snapshot of open orders is taken at candle open — only these are eligible to fill.
   - SELL orders are checked against the candle HIGH. On fill, a new BUY is placed `buy_drop%` below the fill price.
   - BUY orders are checked against the candle LOW. On fill, a new SELL is placed `sell_pct%` above the fill price.
   - Grid window: if the distance between the lowest SELL and the highest BUY exceeds `window_pct%`, all BUY orders are cancelled.
   - Stale BUY cleanup: if all SELLs have filled and a BUY is still open, the BUY is cancelled to allow a fresh entry.
   - Re-entry: if SELLs exist but no BUY, and the candle LOW touches `reentry_pct%` below the lowest SELL, a new position is opened at the threshold price.
4. **P&L accounting** — all fees are 0.1% taker. USDT is reserved (including fee) when a BUY is placed. Realized P&L uses LIFO matching against buy cost bases.
5. **Result** — final portfolio value = free USDT + reserved BUY USDT + (free coins + coins in SELL orders) × last price.

---

## API limits note

KuCoin returns a maximum of **1500 candles per request**. Approximate single-request coverage:

| Timeframe | Coverage |
|---|---|
| 1m | ~1 day |
| 15m | ~15 days |
| 1h | ~62 days |
| 4h | ~250 days |
| 1d | ~4 years |

For periods longer than a single batch, the script paginates automatically. For very long periods, use a larger timeframe or specify `--from`/`--to` explicitly.

---

## License

MIT

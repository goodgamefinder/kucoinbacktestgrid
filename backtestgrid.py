#!/usr/bin/env python3
"""
GRID BOT BACKTESTER — KuCoin public API, no private keys required.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE

  python3 backtestgrid.py --coin PRCL
  python3 backtestgrid.py --coin ALLO --tf 15m --days 90
  python3 backtestgrid.py --coin PRCL --from 2025-01-01 --to 2025-06-01
  python3 backtestgrid.py --coin PRCL --sell-pct 1.0 --buy-drop 1.0 --size 0.15 --max-orders 20

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PARAMETERS

  --coin        Coin ticker without /USDT (required)
  --tf          Timeframe: 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 1w   [1h]
  --days        Period in days from today (used when --from is not set)  [90]
  --from        Start date YYYY-MM-DD
  --to          End date   YYYY-MM-DD (defaults to today)
  --sell-pct    SELL at +N% above the buy price                          [1.0]
  --buy-drop    BUY  at -N% below the sell price                         [2.0]
  --window-pct  Cancel BUY if the SELL-BUY spread widens beyond N%      [12.0]
  --reentry-pct Re-enter if close drops N% below the lowest SELL        [2.0]
  --size        USDT per initial entry and each re-entry                 [0.15]
  --max-orders  Max simultaneous SELL+BUY orders (0 = unlimited)        [0]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API LIMITS

  KuCoin returns at most 1500 candles per request.
  The script paginates automatically and stitches batches together.

  Approximate coverage per request (1500 candles):
    1m  = ~1 day        15m = ~15 days      1h  = ~62 days
    4h  = ~250 days     1d  = ~4 years

  For long periods use --from/--to or a larger --tf.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SIMULATION LOGIC

  At the start of each candle a snapshot of open orders is taken;
  only those snapshot orders are eligible to fill — not orders placed
  during the same candle. This prevents unrealistic same-candle ping-pong.

  Fill order: SELL orders are checked against the candle HIGH first,
  then BUY orders against the candle LOW.

  Re-entries and initial entries use the candle CLOSE price.
  Fee: 0.1% taker applied to every trade.
"""

import ccxt, argparse, sys, time as _time
from datetime import datetime, timezone

# ── COLORS ───────────────────────────────────────────────────────────────────
try:
    import colorama; colorama.init()
    G = colorama.Fore.GREEN;  R = colorama.Fore.RED
    Y = colorama.Fore.YELLOW; C = colorama.Fore.CYAN
    W = colorama.Fore.WHITE;  B = colorama.Style.BRIGHT
    RST = colorama.Style.RESET_ALL
except ImportError:
    G = R = Y = C = W = B = RST = ""

def col(val, v):
    if   v > 0: return f"{G}{B}{val}{RST}"
    elif v < 0: return f"{R}{B}{val}{RST}"
    return f"{W}{val}{RST}"
def cy(s):   return f"{C}{s}{RST}"
def bold(s): return f"{B}{s}{RST}"

# ── ARGUMENTS ────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    p.add_argument("--coin",        type=str.upper, required=True)
    p.add_argument("--tf",          default="1h")
    p.add_argument("--days",        type=int, default=90)
    p.add_argument("--from",        dest="date_from", default=None, metavar="YYYY-MM-DD")
    p.add_argument("--to",          dest="date_to",   default=None, metavar="YYYY-MM-DD")
    p.add_argument("--sell-pct",    type=float, default=1.0)
    p.add_argument("--buy-drop",    type=float, default=2.0)
    p.add_argument("--window-pct",  type=float, default=12.0)
    p.add_argument("--reentry-pct", type=float, default=2.0)
    p.add_argument("--size",        type=float, default=0.15)
    p.add_argument("--max-orders",  type=int,   default=0)
    p.add_argument("--log",         type=str,   default=None, metavar="FILE",
                   help="Write all trades to a CSV file (e.g. --log trades.csv)")
    return p.parse_args()

# ── HELPERS ──────────────────────────────────────────────────────────────────
def _tf_ms(tf):
    """Convert a timeframe string (e.g. '1h', '15m') to milliseconds."""
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    return int(tf[:-1]) * units.get(tf[-1], 3_600_000)

def _ts(ms):
    """Format a Unix-ms timestamp as a human-readable UTC string."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

def _date_ms(s):
    """Parse a YYYY-MM-DD string into a Unix-ms timestamp (UTC midnight)."""
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

# ── CANDLE FETCHING ───────────────────────────────────────────────────────────
def fetch_candles(ex, symbol, tf, since_ms, until_ms):
    """
    Fetch OHLCV candles for the given symbol and timeframe, paginating
    automatically to work around the KuCoin 1500-candle per request limit.
    Deduplicates and sorts the result before returning.
    """
    tf_ms = _tf_ms(tf)
    all_c = []
    cursor = since_ms
    print(f"  Fetching {symbol} [{tf}]  {_ts(since_ms)} -> {_ts(until_ms)}")
    while cursor < until_ms:
        end = min(cursor + tf_ms * 1500, until_ms)
        try:
            batch = ex.fetch_ohlcv(symbol, tf, since=cursor, limit=1500,
                                   params={"endAt": end // 1000})
        except Exception as e:
            print(f"\n  WARN: {e}"); break
        if not batch:
            break
        all_c.extend(batch)
        last_ts = batch[-1][0]
        pct = min(100, (last_ts - since_ms) / max(1, until_ms - since_ms) * 100)
        print(f"\r  Fetched: {len(all_c)} candles  up to {_ts(last_ts)}  [{pct:.0f}%]",
              end="", flush=True)
        if last_ts >= until_ms - tf_ms:
            break
        cursor = last_ts + tf_ms
        _time.sleep(0.25)
    print()
    # Deduplicate and keep only candles within the requested range
    seen = set(); uniq = []
    for c in sorted(all_c, key=lambda x: x[0]):
        if c[0] not in seen and since_ms <= c[0] <= until_ms:
            seen.add(c[0]); uniq.append(c)
    print(f"  Total: {len(uniq)} candles  {_ts(uniq[0][0])} -> {_ts(uniq[-1][0])}")
    return uniq

# ── ROUNDING ──────────────────────────────────────────────────────────────────
def rd(v, prec):
    """
    Floor-round a value to the exchange's precision, matching KuCoin behavior.
    prec can be an int (decimal places) or a float step (e.g. 0.01).
    """
    if prec is None: return v
    if isinstance(prec, float) and prec < 1:
        f = round(1.0 / prec)
        return int(v * f) / f
    if isinstance(prec, int):
        f = 10 ** prec
        return int(v * f) / f
    return v

# ── SIMULATION ────────────────────────────────────────────────────────────────
class Order:
    _n = 0
    def __init__(self, side, price, amount):
        Order._n += 1
        self.id = Order._n
        self.side = side
        self.price = price
        self.amount = amount

class Backtester:
    # KuCoin taker fee applied in USDT for both sides:
    # BUY:  we pay bp * amt * (1 + fee)  → receive full amt coins
    # SELL: we receive ep * amt * (1 - fee) → coins leave without additional loss
    FEE = 0.001

    def __init__(self, cfg, ap, pp):
        self.sp   = cfg.sell_pct      # sell target: +sp% above buy price
        self.bd   = cfg.buy_drop      # buy target:  -bd% below sell price
        self.wp   = cfg.window_pct    # max allowed spread between SELL and BUY
        self.rp_  = cfg.reentry_pct   # re-entry threshold below lowest SELL
        self.sz   = cfg.size          # USDT per entry / re-entry
        self.mo   = cfg.max_orders    # max simultaneous orders (0 = unlimited)
        self.ap   = ap                # amount precision
        self.pp   = pp                # price precision

        self.usdt      = 100.0        # starting USDT balance
        self.start     = 100.0
        self.coins     = 0.0          # free coin balance
        self.locked    = False        # True when order cap is reached
        self.orders    = []           # list of open Order objects
        self.trades    = []           # (ts, side, price, amount, cost, fee)
        self.fee_total = 0.0
        self.buy_stack = []           # LIFO stack for realized P&L tracking
        self.realized  = 0.0

    def rp(self, v): return rd(v, self.pp)
    def ra(self, v): return rd(v, self.ap)
    def so(self): return [o for o in self.orders if o.side == "sell"]
    def bo(self): return [o for o in self.orders if o.side == "buy"]

    def _place_sell(self, amt, prc):
        """Place a limit SELL order; deducts coins from free balance immediately."""
        amt = self.ra(amt); prc = self.rp(prc)
        if amt <= 0 or prc <= 0: return
        if self.mo and len(self.orders) >= self.mo: return
        if self.coins < amt: return
        self.coins -= amt
        self.orders.append(Order("sell", prc, amt))

    def _place_buy(self, amt, prc):
        """
        Place a limit BUY order; reserves USDT (including fee) immediately.
        Fee is NOT added to fee_total here — it is recorded when the order fills.
        """
        amt = self.ra(amt); prc = self.rp(prc)
        if amt <= 0 or prc <= 0: return
        if self.mo and len(self.orders) >= self.mo: return
        cost = amt * prc * (1 + self.FEE)
        if self.usdt < cost: return
        self.usdt -= cost
        self.orders.append(Order("buy", prc, amt))

    def _cancel_buys(self):
        """Cancel all open BUY orders and return reserved USDT (including fee)."""
        for o in list(self.bo()):
            self.usdt += o.amount * o.price * (1 + self.FEE)
            self.orders.remove(o)

    def _record(self, ts, side, price, amount):
        """Record a filled trade and update the LIFO realized P&L stack."""
        cost = price * amount
        fee  = cost * self.FEE
        self.fee_total += fee
        self.trades.append((ts, side, price, amount, cost, fee))
        if side == "buy":
            # cpp = true cost basis including fee
            self.buy_stack.append({"cpp": price * (1 + self.FEE), "amt": amount})
        elif side == "sell" and self.buy_stack:
            need = amount; basis = 0.0
            while need > 1e-9 and self.buy_stack:
                top = self.buy_stack[-1]
                take = min(need, top["amt"])
                basis += take * top["cpp"]
                top["amt"] -= take
                if top["amt"] < 1e-9:
                    self.buy_stack.pop()
                need -= take
            self.realized += cost - basis - fee

    def step(self, ts, o_p, h, l, c_p):
        # Take a snapshot of open orders at candle start to prevent same-candle ping-pong
        snap = list(self.orders)

        # ── Fill SELL orders against the candle HIGH ──────────────────────────
        for order in snap:
            if order.side != "sell": continue
            if h < order.price: continue
            if order not in self.orders: continue

            ep  = order.price
            amt = order.amount
            # Fee paid in USDT; coins leave the position fully
            sell_fee = ep * amt * self.FEE
            proceeds = ep * amt - sell_fee   # net USDT received
            self.usdt += proceeds
            self.orders.remove(order)
            self._record(ts, "sell", ep, amt)

            if not self.locked:
                bp = self.rp(ep * (1 - self.bd / 100))
                # How many coins can we rebuy with the proceeds (fee included)?
                # cost = ba * bp * (1 + fee) = proceeds  →  ba = proceeds / (bp * (1+fee))
                ba = self.ra(proceeds / (bp * (1 + self.FEE)))
                self._place_buy(ba, bp)

        # ── Fill BUY orders against the candle LOW ────────────────────────────
        for order in snap:
            if order.side != "buy": continue
            if l > order.price: continue
            if order not in self.orders: continue

            ep  = order.price
            amt = order.amount
            # USDT was already reserved in _place_buy (fee included)
            # We receive the full coin amount
            got = amt
            self.coins += got
            self.orders.remove(order)
            self._record(ts, "buy", ep, amt)
            self._place_sell(got, self.rp(ep * (1 + self.sp / 100)))

        # ── Grid window check: cancel BUY if SELL-BUY spread is too wide ─────
        so = self.so(); bo = self.bo()
        if so and bo:
            win = (min(o.price for o in so) - max(o.price for o in bo)) \
                   / max(o.price for o in bo) * 100
            if win >= self.wp:
                self._cancel_buys()

        # ── No SELL + open BUYs → cancel BUYs and re-enter ───────────────────
        # Mirrors live bot behavior: if all SELLs filled and a BUY is hanging
        # far below the market, cancel the stale BUY and start a fresh entry.
        so = self.so(); bo = self.bo()
        if not so and bo and not self.locked:
            for o in list(bo):
                self.usdt += o.amount * o.price * (1 + self.FEE)
                self.fee_total -= o.amount * o.price * self.FEE
                self.orders.remove(o)

        # ── Re-entry: open SELLs but no BUY ──────────────────────────────────
        # One re-entry per candle (mirrors one decision cycle per bot run).
        # Trigger: candle LOW touches the re-entry threshold.
        # Execution price: the threshold itself (conservative estimate).
        so = self.so(); bo = self.bo()
        if so and not bo and not self.locked:
            min_sell = min(o.price for o in so)
            threshold = min_sell * (1 - self.rp_ / 100)
            if l <= threshold:
                exec_p = threshold
                ba = self.ra(self.sz / exec_p)
                total_cost = ba * exec_p * (1 + self.FEE)
                if ba > 0 and self.usdt >= total_cost:
                    fee = ba * exec_p * self.FEE
                    self.usdt -= total_cost
                    self.fee_total += fee
                    self.coins += ba
                    self.trades.append((ts, "buy", exec_p, ba, ba*exec_p, fee))
                    self.buy_stack.append({"cpp": exec_p, "amt": ba})
                    self._place_sell(ba, self.rp(exec_p * (1 + self.sp / 100)))

        # ── Initial entry: no orders at all ───────────────────────────────────
        so = self.so(); bo = self.bo()
        if not so and not bo and not self.locked:
            cost_needed = self.sz * (1 + self.FEE)
            if self.usdt >= cost_needed:
                ba = self.ra(self.sz / c_p)
                if ba > 0:
                    fee = ba * c_p * self.FEE
                    self.usdt -= ba * c_p + fee
                    self.fee_total += fee
                    self.coins += ba
                    self.trades.append((ts, "buy", c_p, ba, ba*c_p, fee))
                    self.buy_stack.append({"cpp": c_p, "amt": ba})
                    self._place_sell(ba, self.rp(c_p * (1 + self.sp / 100)))

        # ── Order cap enforcement ─────────────────────────────────────────────
        if self.mo:
            if len(self.orders) >= self.mo:
                if self.bo():
                    self._cancel_buys()
                if not self.locked:
                    self.locked = True
            elif self.locked:
                self.locked = False

    def result(self, last_price):
        so = self.so(); bo = self.bo()
        coins_sell = sum(o.amount for o in so)
        # BUY reserved balance includes fee (returned on cancellation)
        usdt_buy   = sum(o.amount * o.price * (1 + self.FEE) for o in bo)
        pos_val    = (coins_sell + self.coins) * last_price
        final      = self.usdt + usdt_buy + pos_val
        profit     = final - self.start
        fp = next((t[2] for t in self.trades if t[1] == "buy"), None)
        return {
            "profit":      profit,
            "roi":         profit / self.start * 100,
            "final":       final,
            "n_buy":       sum(1 for t in self.trades if t[1] == "buy"),
            "n_sell":      sum(1 for t in self.trades if t[1] == "sell"),
            "fee":         self.fee_total,
            "realized":    self.realized,
            "bh":          ((last_price - fp) / fp * 100) if fp else 0.0,
            "first_p":     fp,
            "last_p":      last_price,
            "o_sell_n":    len(so),
            "o_sell_amt":  coins_sell,
            "o_sell_val":  coins_sell * last_price,
            "o_buy_n":     len(bo),
            "o_buy_usdt":  usdt_buy,
            "free_usdt":   self.usdt,
            "free_coins":  self.coins,
        }


# ── TIME-BASED QUARTERS ───────────────────────────────────────────────────────
def quarterly_by_time(trades, candles):
    """
    Splits the backtest period into 4 equal time segments.
    Boundaries are derived from candle timestamps, so empty quarters
    (no trades) still display their time range correctly.
    """
    if not candles: return []
    t0   = candles[0][0]
    t1   = candles[-1][0]
    span = (t1 - t0) / 4
    quarters = []
    for q in range(4):
        q_start = t0 + q * span
        q_end   = t0 + (q + 1) * span
        qt = [t for t in trades if q_start <= t[0] < q_end]
        # Include the last candle timestamp in the final quarter
        if q == 3:
            qt = [t for t in trades if q_start <= t[0] <= q_end]
        quarters.append((int(q_start), int(q_end), qt))
    return quarters

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    cfg    = parse_args()
    symbol = f"{cfg.coin}/USDT"
    ex = ccxt.kucoin({"enableRateLimit": True})
    ex.load_markets()
    if symbol not in ex.markets:
        print(f"ERR: {symbol} not found on KuCoin"); sys.exit(1)

    m  = ex.markets[symbol]
    # KuCoin stores amounts and prices up to 8 decimal places.
    # The precision field in the markets API is ambiguous (int = decimal places
    # or float = step size), so we fix 8 decimal places for reliability.
    ap = 8
    pp = 8

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if cfg.date_from:
        since_ms = _date_ms(cfg.date_from)
        until_ms = _date_ms(cfg.date_to) if cfg.date_to else now_ms
    else:
        until_ms = now_ms
        since_ms = until_ms - cfg.days * 86_400_000

    candles = fetch_candles(ex, symbol, cfg.tf, since_ms, until_ms)
    if len(candles) < 5:
        print("ERR: not enough candles to run backtest"); sys.exit(1)

    bt = Backtester(cfg, ap, pp)
    print(f"\n  Running {len(candles)} candles...")
    for ts, o, h, l, c, vol in candles:
        bt.step(ts, o, h, l, c)

    last_price = candles[-1][4]
    r = bt.result(last_price)

    # ── CSV TRADE LOG ─────────────────────────────────────────────────────────
    if cfg.log:
        import csv as _csv
        buy_stack_log = []
        usdt_log  = 100.0
        coins_log = 0.0
        realized_log = 0.0
        with open(cfg.log, "w", newline="", encoding="utf-8") as _f:
            _w = _csv.writer(_f)
            _w.writerow(["N", "datetime", "side", "price", "amount",
                         "cost", "fee", "trade_realized",
                         "cumul_realized", "usdt_balance", "coins_balance"])
            for idx, (ts, side, price, amount, cost, fee) in enumerate(bt.trades):
                trade_realized = 0.0
                if side == "buy":
                    buy_stack_log.append({"cpp": price * (1 + bt.FEE), "amt": amount})
                    usdt_log  -= cost + fee
                    coins_log += amount
                elif side == "sell":
                    usdt_log  += cost - fee
                    coins_log -= amount
                    need = amount; basis = 0.0
                    while need > 1e-9 and buy_stack_log:
                        top = buy_stack_log[-1]
                        take = min(need, top["amt"])
                        basis += take * top["cpp"]
                        top["amt"] -= take
                        if top["amt"] < 1e-9: buy_stack_log.pop()
                        need -= take
                    trade_realized = (cost - fee) - basis
                    realized_log += trade_realized
                _w.writerow([
                    idx+1, _ts(ts), side.upper(),
                    f"{price:.8f}", f"{amount:.8f}",
                    f"{cost:.8f}", f"{fee:.8f}",
                    f"{trade_realized:.8f}", f"{realized_log:.8f}",
                    f"{usdt_log:.8f}", f"{coins_log:.8f}",
                ])
        print(f"  Log saved: {cfg.log}  ({len(bt.trades)} trades)")

    # ── RESULTS OUTPUT ────────────────────────────────────────────────────────
    SEP = cy("─" * 58)
    print(f"\n{SEP}")
    print(f"  {bold('BACKTEST')}  {symbol}  [{cfg.tf}]  {len(candles)} candles")
    print(f"  {_ts(candles[0][0])} -> {_ts(candles[-1][0])}")
    print(SEP)
    print(f"  Params: sell+{cfg.sell_pct}%  buy-{cfg.buy_drop}%  "
          f"win{cfg.window_pct}%  re{cfg.reentry_pct}%  "
          f"sz${cfg.size}  max={cfg.max_orders or 'inf'}")
    print(SEP)

    pr_s  = col(f"{r['profit']:+.4f}$", r['profit'])
    roi_s = col(f"{r['roi']:+.2f}%",    r['profit'])
    re_s  = col(f"{r['realized']:+.4f}$", r['realized'])
    bh_s  = col(f"{r['bh']:+.2f}%",     r['bh'])

    print(f"  Trades: {r['n_buy']+r['n_sell']}  "
          f"({r['n_buy']}↑/{r['n_sell']}↓)  fee: ${r['fee']:.4f}")
    print(f"  Realized: {re_s}  (LIFO closed pairs)")
    print(f"  Price:    {r['first_p']:.6f} -> {r['last_p']:.6f}  "
          f"buy&hold: {bh_s}")
    print(f"  Start: $100.0000  Final: ${r['final']:.4f}")
    print(f"  Profit: {pr_s}   ROI: {roi_s}")
    print(SEP)

    # Open orders at end of backtest
    print(f"\n  {cy('Open orders at end of backtest:')}")
    so_end = bt.so(); bo_end = bt.bo()
    # SELL orders — sorted by price descending
    if so_end:
        so_sorted = sorted(so_end, key=lambda o: o.price, reverse=True)
        for o in so_sorted:
            val = o.amount * last_price
            print(f"    SELL {o.amount:.8f} {cfg.coin} @ {o.price:.8f}"
                  f"  (~${o.amount*o.price:.8f} at fill  now ~${val:.8f})")
    else:
        print(f"    SELL: none")
    # BUY orders — sorted by price descending
    if bo_end:
        bo_sorted = sorted(bo_end, key=lambda o: o.price, reverse=True)
        for o in bo_sorted:
            cost = o.amount * o.price
            print(f"    BUY  {o.amount:.8f} {cfg.coin} @ {o.price:.8f}"
                  f"  (${cost:.8f} reserved)")
    else:
        print(f"    BUY:  none")
    print(f"    ─────")
    print(f"    SELL total: {r['o_sell_n']} orders  {r['o_sell_amt']:.8f} {cfg.coin}"
          f"  ~${r['o_sell_val']:.8f}")
    print(f"    BUY  total: {r['o_buy_n']} orders  ${r['o_buy_usdt']:.4f} USDT")
    print(f"    Free: ${r['free_usdt']:.4f} USDT"
          f"  + {r['free_coins']:.4f} {cfg.coin}")
    # Total portfolio value
    total_val = r['o_sell_val'] + r['o_buy_usdt'] + r['free_usdt'] + r['free_coins']*last_price
    print(f"    Total assets: ${total_val:.4f}")

    # Quarterly breakdown
    quarters = quarterly_by_time(bt.trades, candles)
    if quarters:
        print(f"\n  {cy('Quarterly breakdown (equal time segments):')}")
        bsq = []
        for q, (q_ts0, q_ts1, qt) in enumerate(quarters):
            qr = 0.0
            for t in qt:
                if t[1] == "buy":
                    bsq.append({"cpp": t[2], "amt": t[3]})
                elif t[1] == "sell" and bsq:
                    need = t[3]; basis = 0.0
                    while need > 1e-9 and bsq:
                        top = bsq[-1]; take = min(need, top["amt"])
                        basis += take * top["cpp"]; top["amt"] -= take
                        if top["amt"] < 1e-9: bsq.pop()
                        need -= take
                    qr += t[4] - basis - t[5]
            nb  = sum(1 for t in qt if t[1] == "buy")
            ns  = sum(1 for t in qt if t[1] == "sell")
            qf  = sum(t[5] for t in qt)
            r_s = col(f"{qr:+.4f}$", qr)
            # Quarter boundaries are derived from candle time, not trade time
            print(f"    Q{q+1} {_ts(q_ts0)} -> {_ts(q_ts1)}"
                  f"  {len(qt)} trades ({nb}↑/{ns}↓)"
                  f"  realized:{r_s}"
                  f"  fee:${qf:.4f}")
    print(f"\n{SEP}\n")

if __name__ == "__main__":
    main()

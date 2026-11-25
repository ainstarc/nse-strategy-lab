"""Replay the daily scanner and long opening-range breakout on NSE minute data."""

import argparse
import csv
import datetime
import heapq
import json
import math
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trader-scanner"))

import nifty50_import
import scan


DATA = ROOT / "trader" / "nifty50" / "1m"
OUTPUT = Path(__file__).resolve().parent / "results"
OPEN_MINUTE = 9 * 60 + 15
SESSION_MINUTES = 375
ENTRY_CUTOFF = 13 * 60
FLAT_MINUTE = 14 * 60 + 45
POSITION_LIMIT = 100000.0
LONG_CAPITAL = 500000.0
IDLE_CAPITAL = 500000.0
TRADE_FIELDS = ["date", "symbol", "setups", "signal_time", "entry_time", "entry",
                "stop", "target", "quantity", "exit_time", "exit", "outcome",
                "pnl", "r_multiple"]
EQUITY_FIELDS = ["date", "equity", "long_cash", "daily_pnl", "drawdown_pct"]


class WilderRSI:
    def __init__(self):
        self.previous = None
        self.gains = []
        self.losses = []
        self.avg_gain = None
        self.avg_loss = None
        self.value = None

    def add(self, close):
        if self.previous is not None:
            change = close - self.previous
            gain, loss = max(0.0, change), max(0.0, -change)
            if self.avg_gain is None:
                self.gains.append(gain)
                self.losses.append(loss)
                if len(self.gains) == 14:
                    self.avg_gain = sum(self.gains) / 14
                    self.avg_loss = sum(self.losses) / 14
            else:
                self.avg_gain = (self.avg_gain * 13 + gain) / 14
                self.avg_loss = (self.avg_loss * 13 + loss) / 14
            if self.avg_gain is not None:
                self.value = (100 - 100 / (1 + self.avg_gain / self.avg_loss)
                              if self.avg_loss else 100.0)
        self.previous = close


def clock(day, minute):
    return datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(minutes=minute)


def prices(opening_high, opening_low, entry):
    stop = ((Decimal(str(opening_high)) + Decimal(str(opening_low))) / 2).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN)
    entry_decimal = Decimal(str(entry))
    if stop >= entry_decimal or entry_decimal - stop < entry_decimal * Decimal("0.0005"):
        return None
    target = (entry_decimal + 2 * (entry_decimal - stop)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(stop), float(target)


def replay_day(day, symbol, bars, setups, rank):
    """Return one candidate; minute timestamps label bar starts."""
    high = max(bars["high"][:15])
    low = min(bars["low"][:15])
    last_signal_index = ENTRY_CUTOFF - OPEN_MINUTE - 1
    trigger = next((i for i in range(19, last_signal_index + 1, 5)
                    if bars["close"][i] > high), None)
    if trigger is None:
        return None
    entry_index = trigger + 1
    entry = float(bars["open"][entry_index])
    levels = prices(high, low, entry)
    if levels is None:
        return None
    stop, target = levels
    last_index = FLAT_MINUTE - OPEN_MINUTE
    outcome, exit_price, exit_index = "flat", float(bars["open"][last_index]), last_index
    for i in range(entry_index, last_index):
        bar_open = float(bars["open"][i])
        if bar_open <= stop:
            outcome, exit_price, exit_index = "stop", bar_open, i + 1
            break
        if bars["low"][i] <= stop:
            outcome, exit_price, exit_index = "stop", stop, i + 1
            break
        if bars["high"][i] >= target:
            outcome, exit_price, exit_index = "target", target, i + 1
            break
    return {
        "date": day.isoformat(), "symbol": symbol, "setups": ",".join(setups),
        "signal_time": clock(day, OPEN_MINUTE + entry_index),
        "entry_time": clock(day, OPEN_MINUTE + entry_index),
        "entry": entry, "stop": stop, "target": target,
        "exit_time": clock(day, OPEN_MINUTE + exit_index),
        "exit": exit_price, "outcome": outcome, "rank": rank,
    }


def load_symbol(path, symbol, start, end, bar_time="start"):
    frame = pd.read_parquet(path, columns=["date", "open", "high", "low", "close", "volume"])
    if frame.empty:
        raise ValueError("%s has no rows" % path)
    if not isinstance(frame["date"].dtype, pd.DatetimeTZDtype):
        raise ValueError("%s timestamps must have a timezone" % path)
    frame["date"] = frame["date"].dt.tz_convert("Asia/Kolkata")
    if frame["date"].isna().any():
        raise ValueError("%s has null timestamps" % path)
    if bar_time == "end":
        frame["date"] = frame["date"] - pd.Timedelta(minutes=1)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.sort_values("date")
    minutes = frame["date"].dt.hour * 60 + frame["date"].dt.minute
    outside = int(((minutes < OPEN_MINUTE) | (minutes >= OPEN_MINUTE + SESSION_MINUTES)).sum())
    frame = frame.loc[(minutes >= OPEN_MINUTE) & (minutes < OPEN_MINUTE + SESSION_MINUTES)].copy()
    frame["day"] = frame["date"].dt.date
    off_grid = ((frame["date"].dt.second != 0) | (frame["date"].dt.microsecond != 0)
                | (frame["date"].dt.nanosecond != 0))
    off_grid_days = set(frame.loc[off_grid, "day"])
    duplicate_days = set(frame.loc[frame["date"].duplicated(keep=False), "day"])
    invalid = (~np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all(axis=1)
               | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
               | (frame["volume"] < 0)
               | (frame["high"] < frame[["open", "low", "close"]].max(axis=1))
               | (frame["low"] > frame[["open", "high", "close"]].min(axis=1)))
    invalid_days = set(frame.loc[invalid, "day"])
    history, rsi, previous_rsi = [], WilderRSI(), None
    candidates, dates = [], set()
    complete, incomplete, bad_grid, duplicates, bad_values, warmup = 0, 0, 0, 0, 0, 0
    for day, group in frame.groupby("day", sort=True):
        if end and day > end:
            break
        if day in off_grid_days:
            bad_grid += 1
            continue
        if day in duplicate_days:
            duplicates += 1
            continue
        if day in invalid_days:
            bad_values += 1
            continue
        if len(group) != SESSION_MINUTES:
            incomplete += 1
            continue
        complete += 1
        in_range = (start is None or day >= start)
        if in_range:
            dates.add(day)
            if len(history) >= scan.MIN_DAILY_BARS:
                tags, _ = scan.find_setups_with_rsi(history, rsi.value, previous_rsi)
                if tags:
                    bars = {col: group[col].to_numpy() for col in ("open", "high", "low", "close")}
                    candidate = replay_day(day, symbol, bars, tags, history[-1] * previous_volume)
                    if candidate:
                        candidates.append(candidate)
            else:
                warmup += 1
        close = float(group["close"].iloc[-1])
        previous_volume = float(group["volume"].sum())
        history.append(close)
        previous_rsi = rsi.value
        rsi.add(close)
    return candidates, dates, {"symbol": symbol, "complete_sessions": complete,
                               "incomplete_sessions": incomplete, "warmup_sessions": warmup,
                               "off_grid_sessions": bad_grid,
                               "duplicate_sessions": duplicates, "invalid_ohlcv_sessions": bad_values,
                               "outside_session_rows": outside}


def portfolio(candidates, dates):
    by_day = {}
    for candidate in candidates:
        by_day.setdefault(candidate["date"], []).append(candidate)
    cash, peak = LONG_CAPITAL, LONG_CAPITAL + IDLE_CAPITAL
    trades, equity, rejected = [], [], 0
    for day in sorted(dates):
        day_text = day.isoformat()
        start_cash = cash
        pending = []
        ordered = sorted(by_day.get(day_text, []), key=lambda x: (x["entry_time"], -x["rank"], x["symbol"]))
        for candidate in ordered:
            while pending and pending[0][0] <= candidate["entry_time"]:
                _, _, trade = heapq.heappop(pending)
                cash += trade["quantity"] * trade["exit"]
            qty = math.floor(min(POSITION_LIMIT, cash) / candidate["entry"])
            if qty < 1:
                rejected += 1
                continue
            trade = {key: candidate[key] for key in TRADE_FIELDS if key in candidate}
            trade["quantity"] = qty
            trade["pnl"] = round(qty * (trade["exit"] - trade["entry"]), 2)
            trade["r_multiple"] = round((trade["exit"] - trade["entry"]) / (trade["entry"] - trade["stop"]), 4)
            cash -= qty * trade["entry"]
            heapq.heappush(pending, (trade["exit_time"], trade["symbol"], trade))
            trades.append(trade)
        while pending:
            _, _, trade = heapq.heappop(pending)
            cash += trade["quantity"] * trade["exit"]
        total = cash + IDLE_CAPITAL
        peak = max(peak, total)
        equity.append({"date": day_text, "equity": round(total, 2), "long_cash": round(cash, 2),
                       "daily_pnl": round(cash - start_cash, 2),
                       "drawdown_pct": round(100 * (total / peak - 1), 4)})
    return trades, equity, rejected


def write_csv(path, fields, rows):
    with path.open("x", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(data, output, start, end, selected, bar_time="start"):
    symbols = nifty50_import.read_constituents(data / "constituents.csv")
    if selected:
        unknown = set(selected) - set(symbols)
        if unknown:
            raise ValueError("symbols not in constituent snapshot: " + ", ".join(sorted(unknown)))
        symbols = [symbol for symbol in symbols if symbol in selected]
    missing = [symbol for symbol in symbols if not (data / (symbol + ".parquet")).is_file()]
    if missing:
        raise ValueError("missing Parquet files: " + ", ".join(missing))
    if start and end and start > end:
        raise ValueError("--start must not be after --end")
    if output.exists() and any(output.iterdir()):
        raise ValueError("output directory must be empty: %s" % output)
    candidates, dates, quality = [], set(), []
    for symbol in symbols:
        print("Processing %s..." % symbol, flush=True)
        found, days, info = load_symbol(data / (symbol + ".parquet"), symbol, start, end, bar_time)
        candidates.extend(found)
        dates.update(days)
        quality.append(info)
    if not dates:
        raise ValueError("no complete sessions in the requested date range")
    trades, equity, rejected = portfolio(candidates, dates)
    pnl = round(equity[-1]["equity"] - LONG_CAPITAL - IDLE_CAPITAL, 2)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    yearly = {}
    for row in equity:
        year = row["date"][:4]
        yearly[year] = round(yearly.get(year, 0) + row["daily_pnl"], 2)
    summary = {
        "start": min(dates).isoformat(), "end": max(dates).isoformat(),
        "symbols": symbols, "initial_equity": LONG_CAPITAL + IDLE_CAPITAL,
        "initial_long_capital": LONG_CAPITAL, "idle_short_capital": IDLE_CAPITAL,
        "max_position_value": POSITION_LIMIT, "max_loss_per_trade": None,
        "gross_only": True, "transaction_costs": 0, "timestamp_convention": "bar " + bar_time,
        "entry_cutoff": "13:00", "flat_time": "14:45", "target_r": 2,
        "ending_equity": equity[-1]["equity"], "gross_pnl": pnl,
        "gross_return_pct": round(100 * (equity[-1]["equity"] / (LONG_CAPITAL + IDLE_CAPITAL) - 1), 4),
        "max_end_of_day_drawdown_pct": min(row["drawdown_pct"] for row in equity),
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 2) if trades else None,
        "average_r": round(sum(t["r_multiple"] for t in trades) / len(trades), 4) if trades else None,
        "gross_pnl_by_year": yearly,
        "candidates_rejected_for_cash": rejected, "data_quality": quality,
        "limitations": [
            "Current constituents only: survivorship bias; not historical NIFTY 50 membership.",
            "Adjusted prices, no raw prices: position values, share quantities and P&L are approximate.",
            "Gross only: no brokerage, taxes, fees, slippage or spread.",
            "Assumes 09:15-15:30 regular sessions; no historical NSE calendar or special sessions.",
            "Incomplete, off-grid, duplicate or invalid symbol-days are excluded using full-day data, introducing selection bias.",
            "Ambiguous minute bars hit the stop first; minute-bar exits release cash at bar end.",
            "No news filter, Time Machine gate, short positions or max-loss-per-trade cap.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "trades.csv", TRADE_FIELDS, trades)
    write_csv(output / "equity.csv", EQUITY_FIELDS, equity)
    with (output / "summary.json").open("x", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, allow_nan=False)
        fh.write("\n")
    print("Gross P&L: INR %.2f; trades: %d; results: %s" % (pnl, len(trades), output))
    print("Research only: adjusted prices, current constituents, and no transaction costs.")


def parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA, help="directory with Parquet files and constituents.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="empty results directory")
    parser.add_argument("--start", type=parse_date, help="first trading date, YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, help="last trading date, YYYY-MM-DD")
    parser.add_argument("--symbols", help="comma-separated subset of the constituent snapshot")
    parser.add_argument("--bar-time", choices=["start", "end"], default="start",
                        help="whether timestamps mark the start or end of each minute")
    args = parser.parse_args(argv)
    selected = None if args.symbols is None else {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    if args.symbols is not None and not selected:
        parser.error("--symbols must contain at least one symbol")
    try:
        run(args.data, args.output, args.start, args.end, selected, args.bar_time)
    except (OSError, ValueError, ImportError) as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

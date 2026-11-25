#!/usr/bin/env python3
"""Generate ORB candidate trades from current NIFTY 50 minute-bar data.

The fixed-notional sizing and report are produced by the adjacent
``nifty50_fixed_notional_report.py`` script. This runner only generates signals
and candidate fills; it does not apply portfolio cash constraints or charges.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE = Path(__file__).resolve().with_name("nifty50_universe.json")
IST = "Asia/Kolkata"
SESSION_START = "09:15"
SESSION_END = "15:30"


def parse_args():
    p = argparse.ArgumentParser(description="NIFTY 50 15m ORB / 5m backtester")
    p.add_argument("--data", type=Path, default=ROOT / "data" / "1m",
                   help="Parquet file or directory (default: data/1m)")
    p.add_argument("--output", type=Path,
                   default=ROOT / "backtest" / "results" / "nifty50_candidates",
                   help="Candidate output directory")
    p.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE,
                   help="NIFTY 50 constituent JSON")
    p.add_argument("--rr", type=float, default=2.0,
                   help="Reward:risk target (default 2.0 = 1:2)")
    p.add_argument("--max-trades", type=int, default=2,
                   help="Maximum entries per symbol per day")
    p.add_argument("--body-ratio", type=float, default=0.60,
                   help="Minimum breakout candle body / candle range, 0..1")
    p.add_argument("--stop-buffer-pct", type=float, default=0.05,
                   help="Stop buffer as percent of breakout candle range (0.05 = 5%%)")
    p.add_argument("--risk-amount", type=float, default=1000.0,
                   help="Candidate sizing risk; the report resizes to fixed notional")
    p.add_argument("--no-short", action="store_true",
                   help="Disable short signals")
    return p.parse_args()


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Find a timestamp column if the parquet index isn't already a datetime index.
    if not isinstance(df.index, pd.DatetimeIndex):
        candidates = ["timestamp", "datetime", "date_time", "date", "time"]
        ts_col = next((c for c in candidates if c in df.columns), None)
        if ts_col is None:
            # Common exported format: date + time
            if "date" in df.columns and "time" in df.columns:
                df["timestamp"] = pd.to_datetime(
                    df["date"].astype(str) + " " + df["time"].astype(str),
                    errors="coerce"
                )
                ts_col = "timestamp"
            else:
                raise ValueError("No datetime index or timestamp/datetime/date/time column found")
        df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
        df = df.set_index(ts_col)

    if df.index.tz is None:
        df.index = df.index.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
    else:
        df.index = df.index.tz_convert(IST)

    aliases = {
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        "adj close": "close"
    }
    df.columns = [aliases.get(column, column) for column in df.columns]

    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLC columns: {missing}")

    for c in required + (["volume"] if "volume" in df.columns else []):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    valid_rows = ~df.index.isna() & ~df.index.duplicated(keep="last")
    df = df.loc[valid_rows]
    df = df.dropna(subset=required)
    df = df.sort_index()
    return df


def input_files(path: Path) -> list[Path]:
    files = [path] if path.is_file() else sorted(path.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found at {path}")
    return files


def load_universe(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as source:
        universe = json.load(source)
    if not isinstance(universe, dict) or not isinstance(universe.get("symbols"), list):
        raise ValueError(f"{path} must be a JSON object with a symbols list")

    symbols = [str(symbol).strip().upper() for symbol in universe["symbols"]]
    if len(symbols) != 50 or len(set(symbols)) != 50:
        raise ValueError("The NIFTY 50 universe must contain 50 unique symbols")
    return symbols


def find_input_symbols(files: list[Path], allowed_symbols: set[str]) -> set[str]:
    """Find NIFTY 50 symbols available in the Parquet inputs."""
    found: set[str] = set()
    for file in files:
        columns = pq.read_schema(file).names
        symbol_col = next(
            (column for column in columns
             if column.strip().lower() in {"symbol", "ticker", "stock"}),
            None,
        )
        if symbol_col is None:
            symbol = file.stem.strip().upper()
            if symbol in allowed_symbols:
                found.add(symbol)
        else:
            values = pd.read_parquet(file, columns=[symbol_col])[symbol_col]
            found.update(
                str(value).strip().upper()
                for value in values.dropna().unique()
                if str(value).strip().upper() in allowed_symbols
            )
    return found


def load_input(
    files: list[Path], allowed_symbols: set[str]
) -> Iterator[tuple[str, pd.DataFrame]]:
    """Yield only requested symbols, one normalized dataframe at a time."""
    yielded_symbols: set[str] = set()
    for file in files:
        columns = pq.read_schema(file).names
        symbol_col = next(
            (column for column in columns
             if column.strip().lower() in {"symbol", "ticker", "stock"}),
            None,
        )

        if symbol_col is None:
            symbol = file.stem.strip().upper()
            if symbol not in allowed_symbols:
                continue
            if symbol in yielded_symbols:
                raise ValueError(
                    f"Multiple Parquet inputs contain {symbol}; combine its data first"
                )
            yielded_symbols.add(symbol)
            raw = pd.read_parquet(file)
            normalized = normalize_columns(raw)
            yield symbol, normalized
            del normalized, raw
            continue

        raw = pd.read_parquet(file)
        raw.columns = [str(column).strip().lower() for column in raw.columns]
        symbol_col = symbol_col.strip().lower()
        if symbol_col not in raw.columns:
            raise ValueError(
                f"Symbol column {symbol_col!r} is missing from {file}"
            )
        for symbol, group in raw.groupby(symbol_col):
            symbol = str(symbol).strip().upper()
            if symbol not in allowed_symbols:
                continue
            if symbol in yielded_symbols:
                raise ValueError(
                    f"Multiple Parquet inputs contain {symbol}; combine its data first"
                )
            yielded_symbols.add(symbol)
            group = group.drop(columns=[symbol_col])
            normalized = normalize_columns(group)
            yield symbol, normalized
            del normalized, group
        del raw


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    kwargs = dict(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    if "volume" in df.columns:
        kwargs["volume"] = ("volume", "sum")

    # Intraday candles are aligned to the Indian session start (09:15).
    out = df.resample(
        rule, origin="start_day", offset="15h15min",
        label="left", closed="left"
    ).agg(**kwargs)
    return out.dropna(subset=["open", "high", "low", "close"])


def session_data(df: pd.DataFrame) -> pd.DataFrame:
    local_time = df.index.strftime("%H:%M")
    mask = (local_time >= SESSION_START) & (local_time < SESSION_END)
    return df.loc[mask].copy()


def count_session_days(df: pd.DataFrame) -> int:
    local_time = df.index.strftime("%H:%M")
    mask = (local_time >= SESSION_START) & (local_time < SESSION_END)
    return int(pd.Index(df.index[mask].date).nunique())


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def estimate_remaining(
    elapsed: float, completed: int, remaining: float
) -> Optional[float]:
    if completed <= 0:
        return None
    return elapsed * remaining / completed


def backtest_symbol(
    symbol: str,
    minute: pd.DataFrame,
    rr: float,
    max_trades: int,
    body_ratio: float,
    stop_buffer_pct: float,
    risk_amount: float,
    allow_short: bool,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> list[dict]:
    trades = []
    minute = session_data(minute)
    if minute.empty:
        return trades

    session_days = minute.index.date
    total_days = int(pd.Index(session_days).nunique())
    progress_interval = max(25, (total_days + 1) // 2)
    for day_number, (day, day_df) in enumerate(minute.groupby(session_days), start=1):
        if progress_callback and (
            day_number % progress_interval == 0 or day_number == total_days
        ):
            progress_callback(symbol, day_number, total_days)

        day_df = day_df.sort_index()
        # Opening range must contain the complete 09:15-09:30 period.
        opening = day_df.between_time("09:15", "09:29", inclusive="both")
        if len(opening) < 15:
            continue

        range_high = float(opening["high"].max())
        range_low = float(opening["low"].min())
        if not np.isfinite(range_high + range_low) or range_high <= range_low:
            continue

        five = resample_ohlc(day_df, "5min")
        # Keep only candles beginning at/after 09:30, whose entire interval
        # is outside the opening range.
        five = five[five.index.strftime("%H:%M") >= "09:30"]

        entries_today = 0
        last_exit_time = None

        for candle_time, candle in five.iterrows():
            if entries_today >= max_trades:
                break
            if candle_time.strftime("%H:%M") < "09:30":
                continue

            candle_open = float(candle["open"])
            candle_high = float(candle["high"])
            candle_low = float(candle["low"])
            candle_close = float(candle["close"])
            candle_range = candle_high - candle_low
            body = abs(candle_close - candle_open)
            ratio = body / candle_range if candle_range > 0 else 0.0
            if ratio < body_ratio:
                continue

            side = None
            if candle_close > range_high and candle_close > candle_open:
                side = "LONG"
            elif allow_short and candle_close < range_low and candle_close < candle_open:
                side = "SHORT"
            if side is None:
                continue

            # Execute at first 1m bar starting at/after 5m candle completion.
            entry_time = candle_time + pd.Timedelta(minutes=5)
            later = day_df[day_df.index >= entry_time]
            if later.empty:
                continue
            entry_time = later.index[0]
            # Avoid overlapping trades for the same symbol.
            if last_exit_time is not None and entry_time <= last_exit_time:
                continue
            entry_price = float(later.iloc[0]["open"])

            buffer = max(candle_range * stop_buffer_pct, entry_price * 0.0001)
            if side == "LONG":
                stop = candle_low - buffer
                risk_per_share = entry_price - stop
                target = entry_price + rr * risk_per_share
            else:
                stop = candle_high + buffer
                risk_per_share = stop - entry_price
                target = entry_price - rr * risk_per_share

            if risk_per_share <= 0:
                continue

            qty = max(1, int(risk_amount // risk_per_share))
            exit_time = None
            exit_price = None
            exit_reason = None

            # Scan subsequent 1m bars. If both levels are hit, stop wins.
            for bar_time, bar in later.iterrows():
                hi, lo, op = float(bar["high"]), float(bar["low"]), float(bar["open"])
                # If the market gaps through a level, use the bar open as fill.
                if side == "LONG":
                    if op <= stop:
                        exit_time, exit_price, exit_reason = bar_time, op, "STOP_GAP"
                        break
                    if op >= target:
                        exit_time, exit_price, exit_reason = bar_time, op, "TARGET_GAP"
                        break
                    stop_hit = lo <= stop
                    target_hit = hi >= target
                else:
                    if op >= stop:
                        exit_time, exit_price, exit_reason = bar_time, op, "STOP_GAP"
                        break
                    if op <= target:
                        exit_time, exit_price, exit_reason = bar_time, op, "TARGET_GAP"
                        break
                    stop_hit = hi >= stop
                    target_hit = lo <= target

                if stop_hit:
                    exit_time, exit_price, exit_reason = bar_time, stop, "STOP"
                    break
                if target_hit:
                    exit_time, exit_price, exit_reason = bar_time, target, "TARGET"
                    break

            if exit_time is None:
                exit_time = later.index[-1]
                exit_price = float(later.iloc[-1]["close"])
                exit_reason = "EOD"

            pnl_per_share = (exit_price - entry_price) if side == "LONG" else (entry_price - exit_price)
            pnl = pnl_per_share * qty

            trades.append({
                "date": str(day), "symbol": symbol, "side": side,
                "range_high": range_high, "range_low": range_low,
                "signal_time": candle_time, "entry_time": entry_time,
                "entry": entry_price, "stop": stop, "target": target,
                "exit_time": exit_time, "exit": exit_price,
                "exit_reason": exit_reason, "qty": qty,
                "risk_cash": risk_per_share * qty, "pnl": pnl,
                "r_multiple": pnl / (risk_per_share * qty),
            })
            entries_today += 1
            last_exit_time = exit_time

            # New entries are permitted only after the prior trade exits.

    return trades


def main():
    run_started_at = time.monotonic()
    args = parse_args()
    if not math.isfinite(args.risk_amount) or args.risk_amount <= 0:
        raise ValueError("--risk-amount must be finite and positive")
    if args.rr <= 0:
        raise ValueError("--rr must be positive")
    if not 0 <= args.body_ratio <= 1:
        raise ValueError("--body-ratio must be between 0 and 1")
    if args.max_trades < 1:
        raise ValueError("--max-trades must be at least 1")

    symbols = load_universe(args.universe)
    allowed_symbols = set(symbols)
    print(f"Scanning Parquet inputs in {args.data}...", flush=True)
    files = input_files(args.data)
    available_symbols = find_input_symbols(files, allowed_symbols)
    missing_symbols = sorted(allowed_symbols - available_symbols)
    if missing_symbols:
        raise FileNotFoundError(
            "Missing NIFTY 50 Parquet data for: " + ", ".join(missing_symbols)
        )
    symbol_count = len(symbols)
    data = load_input(files, allowed_symbols)
    all_trades = []

    print(
        f"Found all {symbol_count} NIFTY 50 symbols across "
        f"{len(files)} Parquet file(s); "
        "loading and backtesting one symbol at a time.",
        flush=True,
    )

    started_at = time.monotonic()
    completed_sessions = 0
    for symbol_number, (symbol, df) in enumerate(data, start=1):
        symbol_sessions = count_session_days(df)
        print(
            f"[{symbol_number}/{symbol_count}] Starting {symbol}: "
            f"{len(df):,} rows across {symbol_sessions:,} sessions.",
            flush=True,
        )

        def report_progress(
            current_symbol: str, day_number: int, symbol_day_count: int
        ) -> None:
            sessions_done = completed_sessions + day_number - 1
            elapsed = time.monotonic() - started_at
            completed_symbols = symbol_number - 1
            average_symbol_days = (
                completed_sessions / completed_symbols
                if completed_symbols
                else symbol_day_count
            )
            remaining_sessions = (
                symbol_day_count - day_number + 1
                + max(0, symbol_count - symbol_number) * average_symbol_days
            )
            eta = estimate_remaining(elapsed, sessions_done, remaining_sessions)
            eta_text = format_duration(eta) if eta is not None else "estimating"
            print(
                f"  Progress: {completed_symbols:,}/{symbol_count:,} symbols "
                f"completed; {sessions_done:,} symbol-days processed; "
                f"processing {current_symbol} day {day_number:,}/"
                f"{symbol_day_count:,}; elapsed {format_duration(elapsed)}; "
                f"approx. ETA {eta_text}.",
                flush=True,
            )

        symbol_trades = backtest_symbol(
            symbol=symbol,
            minute=df,
            rr=args.rr,
            max_trades=args.max_trades,
            body_ratio=args.body_ratio,
            stop_buffer_pct=args.stop_buffer_pct,
            risk_amount=args.risk_amount,
            allow_short=not args.no_short,
            progress_callback=report_progress,
        )
        all_trades.extend(symbol_trades)
        completed_sessions += symbol_sessions
        elapsed = time.monotonic() - started_at
        average_symbol_days = completed_sessions / symbol_number
        remaining_sessions = (
            max(0, symbol_count - symbol_number) * average_symbol_days
        )
        eta = estimate_remaining(elapsed, completed_sessions, remaining_sessions)
        eta_text = format_duration(eta) if eta is not None else "estimating"
        print(
            f"[{symbol_number}/{symbol_count}] Finished {symbol}: "
            f"{len(symbol_trades):,} trades; elapsed "
            f"{format_duration(elapsed)}; approx. ETA {eta_text}.",
            flush=True,
        )
        del df

    if not all_trades:
        raise ValueError("No NIFTY 50 trades generated; check input data and parameters")

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame(all_trades)
    trades_path = out_dir / "orb_trades.csv"
    trades_df = trades_df.sort_values(["entry_time", "symbol"]).reset_index(drop=True)
    trades_df.to_csv(trades_path, index=False)

    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] < 0]
    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss else np.inf
    candidate_summary = {
        "symbols": symbol_count,
        "candidate_trades": len(trades_df),
        "candidate_gross_pnl": trades_df["pnl"].sum(),
        "candidate_win_rate_pct": 100 * len(wins) / len(trades_df),
        "candidate_profit_factor": profit_factor,
        "candidate_avg_r": trades_df["r_multiple"].mean(),
        "candidate_risk_amount": args.risk_amount,
        "sizing_note": (
            "Candidate qty uses fixed risk; the adjacent report recalculates "
            "qty and P&L at fixed notional without an aggregate cash limit."
        ),
        "universe_file": str(args.universe),
        "rr_target": args.rr,
        "body_ratio_min": args.body_ratio,
        "max_trades_per_symbol_day": args.max_trades,
    }
    pd.Series(candidate_summary, name="value").to_csv(
        out_dir / "orb_candidate_summary.csv", header=True
    )

    print("\n===== NIFTY 50 ORB CANDIDATE SUMMARY =====")
    for key, value in candidate_summary.items():
        print(f"{key:36s}: {value}")
    print(f"\nTotal runtime: {format_duration(time.monotonic() - run_started_at)}")
    print(f"Trade candidates: {trades_path}")
    print(
        "Run nifty50_fixed_notional_report.py with this candidate file "
        "to produce fixed-notional results."
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Recalculate saved ORB trades for a fixed notional per NIFTY 50 entry."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT / "backtest" / "results" / "nifty50_candidates" / "orb_trades.csv"
)
DEFAULT_UNIVERSE = Path(__file__).resolve().with_name("nifty50_universe.json")
DEFAULT_OUTPUT = ROOT / "backtest" / "results" / "nifty50_fixed_notional_replay"
TRADE_COLUMNS = [
    "date",
    "symbol",
    "side",
    "entry_time",
    "entry",
    "exit_time",
    "exit",
    "exit_reason",
    "quantity",
    "entry_notional",
    "gross_pnl",
    "return_pct_on_entry_notional",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report NIFTY 50 ORB trades at fixed notional per entry."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Saved ORB trades CSV")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE,
                        help="JSON file containing the constituent symbols")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="New or empty report output directory")
    parser.add_argument("--notional", type=float, default=100_000.0,
                        help="Fixed INR notional per entry (default: 100000)")
    return parser.parse_args()


def load_universe(path: Path) -> tuple[list[str], dict[str, Any]]:
    with path.open("r", encoding="utf-8") as source:
        universe = json.load(source)
    if not isinstance(universe, dict) or not isinstance(universe.get("symbols"), list):
        raise ValueError(f"{path} must be a JSON object with a symbols list")

    symbols = [str(symbol).strip().upper() for symbol in universe["symbols"]]
    if not symbols or any(not symbol for symbol in symbols):
        raise ValueError("The universe must contain non-empty symbols")
    if len(set(symbols)) != len(symbols):
        raise ValueError("The universe contains duplicate symbols")
    return symbols, universe


def finite_number(row: dict[str, str], column: str, line_number: int) -> float:
    try:
        value = float(row[column])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid {column} value on CSV line {line_number}"
        ) from exc
    if not math.isfinite(value):
        raise ValueError(f"{column} must be finite on CSV line {line_number}")
    return value


def timestamp_key(value: str, line_number: int, column: str) -> int:
    try:
        timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"Invalid {column} timestamp on CSV line {line_number}: {value!r}"
        ) from exc
    if timestamp.tzinfo is None:
        raise ValueError(
            f"{column} timestamp on CSV line {line_number} must include a timezone"
        )
    utc = timestamp.astimezone(dt.timezone.utc)
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    delta = utc - epoch
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def create_database(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_us INTEGER NOT NULL,
            entry REAL NOT NULL,
            exit_time TEXT NOT NULL,
            exit_us INTEGER NOT NULL,
            exit REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            entry_notional REAL NOT NULL,
            pnl REAL NOT NULL,
            exit_year INTEGER NOT NULL
        )
    """)


def import_trades(
    connection: sqlite3.Connection,
    input_path: Path,
    symbols: set[str],
    notional: float,
) -> tuple[int, int, set[str], int]:
    imported = 0
    skipped_no_share = 0
    symbols_with_candidates: set[str] = set()
    csv_rows = 0
    batch = []

    with input_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {
            "date", "symbol", "side", "entry_time", "entry", "exit_time",
            "exit", "exit_reason",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "Trades CSV is missing required columns: "
                + ", ".join(sorted(missing))
            )

        for line_number, row in enumerate(reader, start=2):
            csv_rows += 1
            symbol = (row["symbol"] or "").strip().upper()
            if symbol not in symbols:
                continue
            symbols_with_candidates.add(symbol)

            side = (row["side"] or "").strip().upper()
            if side not in {"LONG", "SHORT"}:
                raise ValueError(f"Invalid side on CSV line {line_number}: {side!r}")

            entry = finite_number(row, "entry", line_number)
            exit_price = finite_number(row, "exit", line_number)
            if entry <= 0 or exit_price <= 0:
                raise ValueError(
                    f"Prices must be positive on CSV line {line_number}"
                )

            entry_us = timestamp_key(row["entry_time"], line_number, "entry_time")
            exit_us = timestamp_key(row["exit_time"], line_number, "exit_time")
            if exit_us < entry_us:
                raise ValueError(
                    f"exit_time precedes entry_time on CSV line {line_number}"
                )

            quantity = math.floor(notional / entry)
            if quantity < 1:
                skipped_no_share += 1
                continue

            pnl_per_share = exit_price - entry if side == "LONG" else entry - exit_price
            entry_notional = quantity * entry
            exit_timestamp = dt.datetime.fromisoformat(
                row["exit_time"].replace("Z", "+00:00")
            )
            batch.append((
                imported + 1,
                row["date"],
                symbol,
                side,
                row["entry_time"],
                entry_us,
                entry,
                row["exit_time"],
                exit_us,
                exit_price,
                row["exit_reason"],
                quantity,
                entry_notional,
                quantity * pnl_per_share,
                exit_timestamp.year,
            ))
            imported += 1

            if len(batch) >= 20_000:
                connection.executemany(
                    "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                batch.clear()

            if csv_rows % 250_000 == 0:
                print(f"Scanned {csv_rows:,} source trades; selected {imported:,}.",
                      flush=True)

    if batch:
        connection.executemany(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
    connection.commit()
    if imported == 0:
        raise ValueError("No NIFTY 50 trades could be sized from the input CSV")
    connection.execute("CREATE INDEX trades_exit_order ON trades(exit_us, id)")
    connection.commit()
    return imported, skipped_no_share, symbols_with_candidates, csv_rows


def profit_factor(gross_profit: float, gross_loss: float) -> float | None:
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def aggregate_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("""
        SELECT
            COUNT(*),
            SUM(pnl),
            SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
            SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END),
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END),
            AVG(pnl),
            MIN(pnl),
            MAX(pnl),
            AVG(100.0 * pnl / entry_notional),
            MIN(entry_time),
            MAX(exit_time)
        FROM trades
    """).fetchone()
    count = int(row[0])
    gross_profit = float(row[2] or 0.0)
    gross_loss = float(row[3] or 0.0)
    return {
        "trade_count": count,
        "net_pnl": float(row[1] or 0.0),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor(gross_profit, gross_loss),
        "winning_trades": int(row[4] or 0),
        "losing_trades": int(row[5] or 0),
        "breakeven_trades": int(row[6] or 0),
        "win_rate_pct": 100.0 * int(row[4] or 0) / count if count else 0.0,
        "average_pnl_per_trade": float(row[7] or 0.0),
        "best_trade_pnl": float(row[9] or 0.0),
        "worst_trade_pnl": float(row[8] or 0.0),
        "average_trade_return_pct": float(row[10] or 0.0),
        "first_entry_time": row[11],
        "last_exit_time": row[12],
    }


def write_trade_log(connection: sqlite3.Connection, output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=TRADE_COLUMNS)
        writer.writeheader()
        cursor = connection.execute(
            "SELECT date, symbol, side, entry_time, entry, exit_time, exit, "
            "exit_reason, quantity, entry_notional, pnl FROM trades "
            "ORDER BY exit_us, id"
        )
        for row in cursor:
            (date, symbol, side, entry_time, entry, exit_time, exit_price,
             exit_reason, quantity, entry_notional, pnl) = row
            writer.writerow({
                "date": date,
                "symbol": symbol,
                "side": side,
                "entry_time": entry_time,
                "entry": entry,
                "exit_time": exit_time,
                "exit": exit_price,
                "exit_reason": exit_reason,
                "quantity": quantity,
                "entry_notional": entry_notional,
                "gross_pnl": pnl,
                "return_pct_on_entry_notional": 100.0 * pnl / entry_notional,
            })


def write_equity_curve(
    connection: sqlite3.Connection, output_path: Path, reference_capital: float
) -> tuple[float, float]:
    peak_equity = reference_capital
    equity = reference_capital
    max_drawdown_cash = 0.0
    max_drawdown_pct = 0.0

    with output_path.open("w", newline="", encoding="utf-8") as destination:
        fields = [
            "timestamp", "closed_trades", "realized_pnl", "equity",
            "peak_equity", "drawdown_cash", "drawdown_pct",
        ]
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        cursor = connection.execute("""
            SELECT exit_us, exit_time, COUNT(*), SUM(pnl)
            FROM trades
            GROUP BY exit_us, exit_time
            ORDER BY exit_us
        """)
        for _, timestamp, count, realized_pnl in cursor:
            equity += float(realized_pnl)
            peak_equity = max(peak_equity, equity)
            drawdown_cash = equity - peak_equity
            drawdown_pct = 100.0 * drawdown_cash / peak_equity if peak_equity else 0.0
            max_drawdown_cash = min(max_drawdown_cash, drawdown_cash)
            max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)
            writer.writerow({
                "timestamp": timestamp,
                "closed_trades": count,
                "realized_pnl": realized_pnl,
                "equity": equity,
                "peak_equity": peak_equity,
                "drawdown_cash": drawdown_cash,
                "drawdown_pct": drawdown_pct,
            })
    return max_drawdown_cash, max_drawdown_pct


def write_grouped_report(
    connection: sqlite3.Connection,
    symbols: list[str],
    output: Path,
    reference_capital: float,
) -> None:
    stock_data = {
        row[0]: row[1:]
        for row in connection.execute("""
            SELECT
                symbol,
                COUNT(*),
                SUM(pnl),
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END),
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                AVG(pnl)
            FROM trades
            GROUP BY symbol
        """)
    }
    with (output / "per_stock.csv").open("w", newline="", encoding="utf-8") as destination:
        fields = [
            "symbol", "trades", "net_pnl", "profit_factor", "win_rate_pct",
            "average_pnl_per_trade", "return_pct_on_fixed_notional",
        ]
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        for symbol in sorted(symbols):
            values = stock_data.get(symbol, (0, 0.0, 0.0, 0.0, 0, 0.0))
            count, net_pnl, gross_profit, gross_loss, wins, average_pnl = values
            count = int(count)
            net_pnl = float(net_pnl or 0.0)
            gross_profit = float(gross_profit or 0.0)
            gross_loss = float(gross_loss or 0.0)
            wins = int(wins or 0)
            writer.writerow({
                "symbol": symbol,
                "trades": count,
                "net_pnl": net_pnl,
                "profit_factor": profit_factor(gross_profit, gross_loss),
                "win_rate_pct": 100.0 * wins / count if count else 0.0,
                "average_pnl_per_trade": float(average_pnl or 0.0),
                "return_pct_on_fixed_notional": 100.0 * net_pnl / (reference_capital / len(symbols)),
            })

    with (output / "yearly.csv").open("w", newline="", encoding="utf-8") as destination:
        fields = [
            "exit_year", "trades", "net_pnl", "profit_factor", "win_rate_pct",
            "return_pct_on_reference_capital",
        ]
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        for year, count, net, wins, gross_profit, gross_loss in connection.execute("""
            SELECT
                exit_year,
                COUNT(*),
                SUM(pnl),
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END)
            FROM trades
            GROUP BY exit_year
            ORDER BY exit_year
        """):
            writer.writerow({
                "exit_year": year,
                "trades": count,
                "net_pnl": net,
                "profit_factor": profit_factor(float(gross_profit or 0.0),
                                               float(gross_loss or 0.0)),
                "win_rate_pct": 100.0 * wins / count if count else 0.0,
                "return_pct_on_reference_capital": 100.0 * net / reference_capital,
            })


def write_markdown_report(
    output_path: Path,
    summary: dict[str, Any],
    snapshot_date: str,
) -> None:
    factor = summary["profit_factor"]
    factor_text = "N/A" if factor is None else f'{factor:.4f}'
    report = f"""# NIFTY 50 ORB Fixed-Notional Report

- **Universe snapshot:** {snapshot_date} ({summary["constituent_count"]} symbols)
- **Trade period:** {summary["first_entry_time"]} to {summary["last_exit_time"]}
- **Sizing:** INR {summary["fixed_notional_per_entry"]:,.0f} per entry; whole shares,
  rounded down
- **Aggregate cash constraint:** None; no compounding
- **Nominal reference capital:** INR {summary["nominal_reference_capital"]:,.0f}
  (INR {summary["fixed_notional_per_entry"]:,.0f} x {summary["constituent_count"]})

## Summary

| Metric | Result |
| --- | ---: |
| Trades | {summary["trade_count"]:,} |
| Win rate | {summary["win_rate_pct"]:.2f}% |
| Wins / losses / breakeven | {summary["winning_trades"]:,} / {summary["losing_trades"]:,} / {summary["breakeven_trades"]:,} |
| Gross profit | INR {summary["gross_profit"]:,.2f} |
| Gross loss | INR {summary["gross_loss"]:,.2f} |
| Net gross P&L | INR {summary["net_pnl"]:,.2f} |
| Profit factor | {factor_text} |
| Return vs nominal reference capital | {summary["return_pct_on_reference_capital"]:.2f}% |
| Max realized-only drawdown | INR {abs(summary["max_drawdown_cash"]):,.2f}
  ({abs(summary["max_drawdown_pct"]):.2f}%) |

## Files

- [Full NIFTY 50 trade log](trades.csv)
- [Per-stock summary](per_stock.csv)
- [Yearly summary](yearly.csv)
- [Realized equity curve](equity_curve.csv)
- [Machine-readable summary](summary.json)

## Interpretation limits

This replay recalculates quantity and gross P&L from the saved candidate
trades; it does not rerun signal generation. It uses current constituents
retrospectively, so historical results have survivorship bias. Brokerage,
taxes, slippage, dividends, and short margin requirements are not modeled.
Drawdown is based on realized exits only, not intraday mark-to-market equity.
The nominal reference-capital return is a normalization, not a claim that
aggregate cash was constrained to this amount.
"""
    output_path.write_text(report, encoding="utf-8")


def run_report(args: argparse.Namespace) -> dict[str, Any]:
    if not math.isfinite(args.notional) or args.notional <= 0:
        raise ValueError("--notional must be a finite positive amount")
    symbols, universe = load_universe(args.universe)
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    reference_capital = args.notional * len(symbols)
    with tempfile.TemporaryDirectory(prefix="nifty50-orb-report-") as temp_dir:
        connection = sqlite3.connect(Path(temp_dir) / "trades.sqlite")
        try:
            create_database(connection)
            imported, skipped, symbols_with_candidates, csv_rows = import_trades(
                connection, args.input, set(symbols), args.notional
            )
            metrics = aggregate_metrics(connection)
            max_drawdown_cash, max_drawdown_pct = write_equity_curve(
                connection, args.output / "equity_curve.csv", reference_capital
            )
            write_trade_log(connection, args.output / "trades.csv")
            write_grouped_report(connection, symbols, args.output, reference_capital)
        finally:
            connection.close()

    summary = {
        "input_csv": str(args.input),
        "universe_file": str(args.universe),
        "universe_source": universe.get("source"),
        "universe_snapshot_date": universe.get("snapshot_date"),
        "constituent_count": len(symbols),
        "symbols_without_candidates": sorted(set(symbols) - symbols_with_candidates),
        "source_csv_rows_scanned": csv_rows,
        "sized_trade_count": imported,
        "skipped_trades_because_one_share_exceeds_notional": skipped,
        "fixed_notional_per_entry": args.notional,
        "sizing": "floor(fixed_notional / entry_price) whole shares",
        "aggregate_cash_constraint": None,
        "compounding": False,
        "nominal_reference_capital": reference_capital,
        "nominal_reference_capital_note": (
            "INR 1 lakh per constituent, used only to normalize return and "
            "realized drawdown; aggregate cash is not constrained."
        ),
        **metrics,
        "return_pct_on_reference_capital": (
            100.0 * metrics["net_pnl"] / reference_capital
        ),
        "ending_realized_equity_on_reference_capital": (
            reference_capital + metrics["net_pnl"]
        ),
        "max_drawdown_cash": max_drawdown_cash,
        "max_drawdown_pct": max_drawdown_pct,
        "drawdown_method": "realized exits only; no intraday mark-to-market",
        "costs": "not modeled; results are gross",
        "survivorship_bias": (
            "Current NIFTY 50 constituents are applied retrospectively to all "
            "available historical trades."
        ),
    }
    with (args.output / "summary.json").open("w", encoding="utf-8") as destination:
        json.dump(summary, destination, indent=2)
        destination.write("\n")
    write_markdown_report(
        args.output / "report.md", summary, str(universe.get("snapshot_date", "unknown"))
    )
    print(
        f"Report complete: {metrics['trade_count']:,} trades, "
        f"net gross P&L INR {metrics['net_pnl']:,.2f}; "
        f"outputs: {args.output}",
        flush=True,
    )
    return summary


def main() -> None:
    run_report(parse_args())


if __name__ == "__main__":
    main()

# Strategy Review: Opening-Range Breakout

## Setup and rules

- **Source:** [Strategy video](https://www.youtube.com/watch?v=vZktd88MtTI) and
  [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
- **Test:** Current NIFTY 50 constituent snapshot (not historical membership);
  2015-02-02 to 2026-07-31; daily scanner, 5-minute signals, 1-minute execution
- **Market data:** 1-minute historical candles sourced from Zerodha Kite
  Connect; the local backtest input is Parquet
- **Capital / costs:** INR 10 lakh; 0% costs, so results are gross

The source rule breaks above the first 15 minutes' high on a completed
5-minute close. The tested adaptation uses five daily close-based setups and
prior-session history; it enters at the next minute's open after a qualifying
close, no later than 13:00. The opening-range midpoint is the stop, the target
is 2R, and remaining positions exit at the 14:45 bar's open. A stop wins if a
minute touches both stop and target; gaps through the stop fill at the open.

INR 5 lakh is allocated to longs and INR 5 lakh remains idle. Positions are
capped at INR 1 lakh and available long cash. There is no loss cap, short
trading, news filter, or Time Machine gate. The source offers simulations but
no numerical claim verified by this study.

## Results

| Metric | Strategy | NIFTY 50 benchmark |
| --- | ---: | ---: |
| CAGR / total return | -1.28% / -13.75% | Not measured |
| Maximum drawdown | -26.60% (end-of-day) | Not measured |
| Sharpe ratio | -0.27 | Not measured |
| Win rate / trades | 39.10% / 13,306 | N/A |

Results use the full account, including idle capital. CAGR uses calendar
days; Sharpe uses daily equity returns, a 0% risk-free rate, sample standard
deviation, and 252 sessions per year. No benchmark comparison was performed.

- [Summary and data-quality details](results/summary.json)
- [Trade log](results/trades.csv)
- [Daily equity](results/equity.csv)

**Verdict:** Unprofitable in this gross backtest: total loss was INR 137,480.12.
The best reported year was 2024, with INR 52,035.22 gross profit. This does
not establish after-cost or benchmark-relative performance.

## Reproduce

The runner is **not standalone**: it imports `scan.py` and `nifty50_import.py`
from a sibling `trader-scanner` directory. The workspace version of `scan.py`
must provide `find_setups_with_rsi`; those files and market data are not
included here. Market data is stated to come from Zerodha Kite Connect; record
the Kite instrument tokens, download dates, and constituent snapshot date
alongside the data when available. The backtest runner reads local Parquet and
does not call the Kite API. See the [upstream MIT license](https://github.com/Jakeschincariol/day-trader-skill/blob/main/LICENSE)
when distributing upstream code.

Expected files: `trader-scanner\scan.py`, `trader-scanner\nifty50_import.py`,
`trader\nifty50\1m\constituents.csv`, and one `SYMBOL.parquet` per constituent.
Parquet files need timezone-aware `date` plus `open`, `high`, `low`, `close`,
and `volume` columns. The CSV needs a `Symbol` column with exactly 50 unique
symbols. Convert timestamps to timezone-aware values and ensure the bars use
the expected start-time convention (or pass `--bar-time end` for end-labeled
bars). Do not publish Kite data unless its redistribution is permitted by the
applicable Zerodha terms.

From the workspace root, install dependencies and write results to a new,
empty directory:

```powershell
python -m pip install -r Jakeschincariol-day-trader-skill\requirements.txt
python Jakeschincariol-day-trader-skill\nse_backtest.py --output Jakeschincariol-day-trader-skill\results-new
```

Default data is `trader\nifty50\1m`; `--data PATH` selects another directory.
The default output is `Jakeschincariol-day-trader-skill\results`; non-empty
output directories are rejected. Optional arguments: `--start YYYY-MM-DD`,
`--end YYYY-MM-DD`, `--symbols RELIANCE,INFY`, and `--bar-time end`.
Earlier data is retained for scanner warmup.

## Limitations

No fees, taxes, spread, or slippage are included. Adjusted prices without raw
prices or adjustment factors make quantities and rupee P&L approximate.
Current constituents create survivorship bias. The runner assumes normal
09:15-15:30 sessions without a historical NSE calendar; it excludes
incomplete, off-grid, duplicate, or invalid symbol-days using full-day data,
which can introduce selection bias. Drawdown is end-of-day only.

For educational and quantitative research only; this is not financial advice.

[Back to the study index](../README.md)

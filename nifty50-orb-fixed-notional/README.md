# Strategy Review: NIFTY 50 ORB, Fixed Notional

## Study details

- **Strategy:** 15-minute opening range breakout with 5-minute confirmation
- **Source:** [Strategy video](https://www.youtube.com/watch?v=yKtI-VEMwC0)
- **Universe / period:** Official NIFTY 50 constituents as of 2026-11-25,
  applied retrospectively; 2015-02-02 to 2026-11-23
- **Market data:** 1-minute Parquet bars sourced from Zerodha Kite Connect;
  neither the bars nor the source trade log are distributed here
- **Sizing:** INR 100,000 notional per entry, rounded down to whole shares;
  no compounding and no aggregate cash constraint
- **Charges:** Estimated using Zerodha's current NSE equity-intraday schedule,
  applied uniformly to historical trades
- **Code:** [NIFTY 50 signal generator](orb_15m_range_5m_breakout_backtest.py)
  and [fixed-notional report](nifty50_fixed_notional_report.py)

## Rules tested

The opening range is the high and low from 09:15 through 09:29 IST. After
09:30, a completed 5-minute candle must have a body at least 60% of its range.
A bullish candle closing above the opening-range high signals a long; a
bearish candle closing below the opening-range low signals a short. Entry is
at the next available 1-minute candle open.

The stop is beyond the signal candle's low/high by 5% of its range (with a
minimum buffer of 0.01% of entry price); the target is 2R. The test allows at
most two trades per symbol per day and does not overlap positions in the same
symbol. If stop and target are both touched in one minute, the stop is assumed
first. Gaps fill at the candle open; any remaining trade exits at the last
available session bar's close.

The signal generator runs the strategy only for the 50 symbols in the frozen
universe file. The report recalculates each candidate trade's quantity as
`floor(INR 100,000 / entry price)`. Candidate quantities and P&L from the
signal-generation risk sizing are not reused.

## Results

The INR 50 lakh amount below is a reference basis (INR 1 lakh per constituent)
for gross return only. It is not a portfolio cash limit.

| Metric | Gross | After estimated charges |
| --- | ---: | ---: |
| Trades | 232,049 | 232,049 |
| Win rate | 36.15% | 35.11% |
| Profit factor | 1.0139 | 0.7230 |
| Aggregate P&L | +INR 749,386.02 | -INR 18,382,159.66 |
| Maximum drawdown | -INR 296,236.76 (5.40%) | Not calculated |

Gross maximum drawdown is based on realized exits only; it excludes
mark-to-market losses on open positions. The estimated charges total
INR 19,131,545.67:

| Charge | Estimated amount |
| --- | ---: |
| Brokerage | INR 9,281,954.21 |
| STT | INR 5,762,795.38 |
| NSE transaction charges | INR 1,415,319.54 |
| SEBI charges | INR 46,101.61 |
| Stamp duty | INR 691,512.96 |
| GST | INR 1,933,815.86 |
| IPFT | INR 46.10 |

These are estimates, not Zerodha contract-note amounts. The [current Zerodha
charges page](https://zerodha.com/charges/) rates were applied to every
historical trade; rates, rounding, and actual execution turnover can differ
by date and execution.

- [Machine-readable summary](results/summary.json)
- [Gross per-stock results](results/per_stock_gross.csv)
- [Gross yearly results](results/yearly_gross.csv)
- [Frozen constituent snapshot](nifty50_universe.json)
- [Fixed-notional replay script](nifty50_fixed_notional_report.py)

The full trade log, equity curve, and underlying market data are omitted:
they are large, derived from licensed data, and not required to understand
these aggregate results.

## Reproduce

From the repository root, install the backtest dependencies, then generate
NIFTY 50 candidates from local 1-minute Parquet files and create the
fixed-notional report:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backtest\requirements.txt

.\.venv\Scripts\python.exe nifty50-orb-fixed-notional\orb_15m_range_5m_breakout_backtest.py `
  --data data\1m `
  --output backtest\results\nifty50_candidates

.\.venv\Scripts\python.exe nifty50-orb-fixed-notional\nifty50_fixed_notional_report.py `
  --input backtest\results\nifty50_candidates\orb_trades.csv `
  --output backtest\results\nifty50_fixed_notional-new
```

The minute bars are not included. Do not publish Zerodha Kite data or derived
trade-level exports unless the applicable terms permit redistribution.

## Limitations

Current constituents are used for the entire historical period, creating
survivorship bias. The results omit market impact and other execution
differences; the charge estimate applies current rates retrospectively.
Intraday drawdown is not measured, and short margin requirements are not
modeled. No NIFTY 50 benchmark comparison was calculated. This is educational
research, not investment advice.

[Back to the study index](../README.md)

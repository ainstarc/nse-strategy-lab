# NSE Strategy Lab

An educational collection of backtests and evidence-based reviews of trading
strategies applied to Indian equities. Each study should include its rules,
data assumptions, reproducible code where possible, saved results, and
limitations. Backtests are research—not investment advice.

## Studies

| Study | Strategy | Period | Gross result | Review |
| --- | --- | --- | --- | --- |
| 1 | Opening-range breakout, adapted from [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | 2015-02-02 to 2026-07-31 | -INR 137,480.12 (-13.75%) | [Open study](Jakeschincariol-day-trader-skill/README.md) |

This is the only completed study currently included. Its tested universe is a
snapshot of current NIFTY 50 constituents; the saved run does **not** contain a
NIFTY 50 benchmark comparison. See the study review for methodology and
caveats before interpreting its results.

## Repository layout

```text
.
├── README.md
├── BACKTEST_README_TEMPLATE.md
└── Jakeschincariol-day-trader-skill/
    ├── README.md
    ├── nse_backtest.py
    ├── requirements.txt
    └── results/
        ├── equity.csv
        ├── summary.json
        └── trades.csv
```

The backtest runner also imports `scan.py` and `nifty50_import.py` from a
sibling `trader-scanner` directory, and reads local minute-bar data. Neither
the sibling source files nor the market data are included here. The study's
README documents the expected layout and run instructions.

## Review and testing principles

Studies should document, rather than assume, the following:

- The original source and the exact rules implemented, including any
  adaptations.
- Data source, universe, dates, time zone, price adjustments, and data-quality
  exclusions.
- Signal timing and fill assumptions, to make look-ahead risks visible.
- Brokerage, taxes, spread, and slippage assumptions. If these are omitted,
  label results as gross.
- Metrics and benchmark results only when they were actually calculated.
- Limitations such as survivorship bias, incomplete market calendars, and
  missing data.

Use [BACKTEST_README_TEMPLATE.md](BACKTEST_README_TEMPLATE.md) when adding a
study. Do not compare results across studies unless their assumptions and
measurement methods are compatible.

## Disclaimer

For educational and quantitative research purposes only. This repository does
not provide financial, investment, or trading advice. The author is not a
SEBI-registered investment advisor. Past or simulated performance does not
guarantee future results. Consult a qualified financial advisor before making
investment decisions.

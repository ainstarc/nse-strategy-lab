# NSE Strategy Lab

An educational collection of backtests and evidence-based reviews of trading
strategies applied to Indian equities. Each study should include its rules,
data assumptions, reproducible code where possible, saved results, and
limitations. Backtests are research—not investment advice.

## Studies

| Study | Strategy | Period | Headline result | Review |
| --- | --- | --- | --- | --- |
| 1 | Opening-range breakout, adapted from [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | 2015-02-02 to 2026-07-31 | -INR 137,480.12 (-13.75%) gross | [Open study](Jakeschincariol-day-trader-skill/README.md) |
| 2 | 15-minute opening range / 5-minute breakout, fixed-notional NIFTY 50 ([source](https://www.youtube.com/watch?v=yKtI-VEMwC0)) | 2015-02-02 to 2026-11-23 | +INR 749,386 gross; -INR 18,382,160 after estimated Zerodha charges | [Open study](nifty50-orb-fixed-notional/README.md) |

The studies implement different rules and use different sizing assumptions;
their results are not directly comparable. The second study filters a saved
candidate-trade log using the current NIFTY 50 snapshot and estimates
current-rate Zerodha intraday charges. See each review for methodology and
caveats before interpreting results.

## Repository layout

```text
.
├── README.md
├── BACKTEST_README_TEMPLATE.md
├── nifty50-orb-fixed-notional/
│   ├── README.md
│   ├── orb_15m_range_5m_breakout_backtest.py
│   ├── nifty50_fixed_notional_report.py
│   ├── nifty50_universe.json
│   └── results/
│       ├── summary.json
│       ├── per_stock_gross.csv
│       └── yearly_gross.csv
├── backtest/
│   ├── README.md
│   ├── requirements.txt
│   ├── orb_15m_range_5m_breakout_backtest.py
│   ├── orb_portfolio_replay.py
│   └── nifty50_fixed_notional_report.py (compatibility launcher)
├── data/
│   └── nifty50.json
└── Jakeschincariol-day-trader-skill/
    ├── README.md
    ├── nse_backtest.py
    ├── requirements.txt
    └── results/
        ├── equity.csv
        ├── summary.json
        └── trades.csv
```

The first study's runner also imports `scan.py` and `nifty50_import.py` from a
sibling `trader-scanner` directory, and reads local minute-bar data. Neither
the sibling source files nor the market data are included here. Its README
documents the expected layout and run instructions.

The separate ORB research runner and a fixed-notional NIFTY 50 report workflow
are documented in [backtest/README.md](backtest/README.md). Local market data
and generated backtest results are excluded from version control; the public
NIFTY 50 constituent snapshot is versioned for reproducibility.

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

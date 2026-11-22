# Backtest baseline and signal-release gate

**Baseline run:** GitHub Actions run [44836202579](https://github.com/andertrading/Trading-bot/actions/runs/44836202579)  
**Backtest commit:** `1854dc30cd14da3c2ac5670d8f932ddf83ae8c58`  
**Artifact:** `resultados-backtest-v3`, downloaded from the run above.  
**Strategy source SHA:** `strategies.py` = `0156ae5ff8555a4e93cd095a59682e14534c1798` (same as the current main baseline inspected on 2026-11-21).

## Baseline results

| Timeframe | Trades | Win rate | Total result | Average result | Profit factor |
|---|---:|---:|---:|---:|---:|
| 15m | 537 | 28.68% | -95.554 R | -0.1779 R | 0.760 |
| 1d | 62 | 27.42% | -8.774 R | -0.1415 R | 0.813 |
| **Total** | **599** | **28.55%** | **-104.328 R** | **-0.1742 R** | **0.766** |

These results are gross backtest outcomes. The exported operations explicitly report `costs_included=False` and `position_sizing_included=False`. They are not live performance and do not include the full spread, slippage, commissions, funding or broker-specific execution effects.

## Chronological stability check

A simple chronological split at the median signal timestamp gives:

| Timeframe | Earlier half | Later half |
|---|---:|---:|
| 15m | 269 trades; -35.06 R; -0.130 R/trade; PF 0.820 | 268 trades; -60.50 R; -0.226 R/trade; PF 0.703 |
| 1d | 31 trades; +2.15 R; +0.069 R/trade; PF 1.108 | 31 trades; -10.93 R; -0.353 R/trade; PF 0.595 |

This is a diagnostic split, not a complete walk-forward study or proof of statistical significance. It does show that the baseline did not demonstrate stable positive performance across the sample.

## Historical coverage and data provenance

The artifact contains 22 accepted asset/timeframe combinations out of 24:

- Intraday equities and ETFs: approximately 2026-08-28 to 2026-11-20 (about 60 days).
- Intraday crypto: approximately 2026-09-22 to 2026-11-20 (about 60 days).
- Daily equities: approximately 2024-11-21 to 2026-11-19 (about two years).
- Daily crypto: approximately 2024-11-20 to 2026-11-19 (about two years).

The artifact manifest identifies Yahoo Finance as the source for all 22 accepted combinations. The backtest commit used an older data-engine revision (`data_engine.py` SHA `0feb616f86740f7d4ad4f6ba76a664083e8c9ca2`). The current main data engine has changed provider routing: Twelve Data for the ten non-ETF assets, Yahoo for ETF intraday, and EODHD for CSPX/CNDX daily. Therefore the old backtest cannot be treated as a provider-parity validation of the current scanner.

The CSPX and CNDX daily histories were rejected because each contained one incoherent OHLC candle. Those two combinations are absent from the accepted results.

## Release decision

**NO-GO for trade-ready alerts.** The strategy may be used for internal research and diagnostics only until it passes:

1. Reproducible backtests using the current data-provider routes and verified candle adjustments.
2. Sufficiently long historical coverage, including different market regimes.
3. Chronological out-of-sample and walk-forward validation.
4. Realistic costs, spread, slippage, funding and gap handling.
5. Positive net expectancy with acceptable drawdown and stability across assets/timeframes.
6. Probability calibration on unseen data before any success probability is shown.

Do not lower thresholds or select asset-specific settings solely because they performed well in this same sample. No automatic trading is permitted. All user decisions remain manual.

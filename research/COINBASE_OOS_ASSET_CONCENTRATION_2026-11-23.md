# Coinbase OOS asset concentration and overlap sensitivity — 2026-11-23

## Scope and safeguards

This is a research-only diagnostic over the retained Coinbase OOS artifact (workflow run [45244766224](https://github.com/andertrading/Trading-bot/actions/runs/45244766224), artifact ID `14852244870`, SHA-256 `9e1d33001b7359e13e9540bd88a23057c394290f1eaa75134d788c5de406ffb6`). It includes only `oos_fold=test`: 1,252 candidate trades across 10 series and 21 test folds. No provider was queried and no production code or configuration was changed.

All performance figures below are **gross, equal-weight candidate-trade R**. They are not a position-sized portfolio backtest. Costs are incomplete, Coinbase OHLC is not proven equivalent to eToro executable bid/ask, and trade candidates may not all have been accepted by the portfolio allocator.

## Concentration by asset

| Asset | Trades | Gross total R | Mean R/trade | Win rate | Gross PF |
|---|---:|---:|---:|---:|---:|
| BTC | 297 | +25.69 | +0.087 | 38.7% | 1.145 |
| ETH | 253 | +29.02 | +0.115 | 40.3% | 1.195 |
| SOL | 256 | -2.15 | -0.008 | 35.2% | 0.987 |
| XLM | 194 | -27.85 | -0.144 | 29.4% | 0.794 |
| XRP | 252 | -19.84 | -0.079 | 32.1% | 0.880 |

BTC+ETH: 550 trades, +54.71R, +0.0995R/trade, gross PF 1.168. SOL+XLM+XRP: 702 trades, -49.84R, -0.0710R/trade, gross PF 0.892. The aggregate M15 stream is only +6.86R over 1,247 trades (mean +0.0055R, gross PF 1.01). The daily stream has only five trades and is not evaluable (−1.99R).

**Interpretation:** the slight aggregate gross edge is not broad-based across the five assets. BTC and ETH contribute more than the total net positive gross R while the other three assets offset them. This is a concentration warning, not a reason to remove assets or optimize the universe after seeing OOS results.

## Dependence and effective sample size

The preceding temporal-overlap audit found 19 same-asset M15/daily overlapping pairs involving 20 trades, and 1,707 cross-asset overlapping pairs involving 1,022 trades. For M15, 26 distinct UTC exit-weeks contain trades; the daily sample is just five trades over four exit-weeks.

A defensible effective sample size is **not estimated**. The artifact does not contain a validated, synchronized, position-sized portfolio return series; OOS test windows are discontinuous; and cross-asset exposure is material. Treating 1,252 trades as IID, or applying a naïve trade bootstrap, would overstate precision. The 26 active M15 exit-weeks are a clustering descriptor, not an ESS.

## Ex-post same-asset cross-timeframe exclusion sensitivity

| Candidate stream diagnostic | Trades remaining | Gross total R | Mean R/trade | Gross PF |
|---|---:|---:|---:|---:|
| Baseline | 1,252 | +4.87 | +0.0039 | 1.009 |
| Remove M15 member of each same-asset M15/daily overlap | 1,233 | +0.75 | +0.0006 | 1.001 |
| Remove daily member of each same-asset M15/daily overlap | 1,251 | +5.87 | +0.0047 | 1.007 |

These are **ex-post candidate-trade filters**, not simulated portfolio outcomes. They do not replay cash, sizing, risk reservations, event ordering, or replacement opportunities. They must not be interpreted as an implementable trading rule or as proof that one timeframe should be preferred.

## Decision

- No strategy or asset-universe change.
- No effective sample-size claim.
- No strategy approval: gross expectancy is marginal, concentrated, and not net of complete verified costs.
- Keep all production, sizing, automation, alerts, and eToro execution gates unchanged.

## Reproduction

Extract the retained artifact and run:

`python research/oos_asset_concentration_audit.py --input-dir /path/to/extracted/artifact --output asset_concentration_audit.json`

The script validates the source using the existing strict OOS artifact loader before producing the breakdown and sensitivity diagnostics.

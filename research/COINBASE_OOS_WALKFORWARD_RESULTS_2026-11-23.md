# Coinbase segmented crypto OOS walk-forward results — 2026-11-23

## Decision

**No strategy is approved for production.** The chronological out-of-sample run completed for all ten Coinbase-only series. The 15-minute aggregate test result is only marginally positive before costs (1.009 profit factor, +6.864 R across 1,247 trades), with large dispersion by asset. This is not evidence of a robust, tradable edge. Daily evidence is far too sparse (five test trades total across all five assets).

All results are gross, in-sample-independent research metrics using Coinbase Exchange spot OHLC. They are not eToro executable prices, and verified net performance remains unavailable.

## Method

- Source: retained Coinbase Exchange-only artifact from capture run `45233527066`; no provider downloads or paid data credits were used.
- Assets: BTC, ETH, SOL, XLM, XRP.
- Timeframes: M15 and daily.
- Each natural contiguous timestamp segment was split chronologically into 60% training-only history, 20% validation, and 20% test.
- Each fold received only the historical prefix ending at its own final timestamp. Indicator calculations therefore had no future rows available.
- Candidate signals were allowed only within the fold. The existing engine's maximum-hold buffer kept simulated exits within that fold.
- Gaps were not filled, interpolated, or repaired from another provider.
- Ten output JSON files were generated. All trades passed signal/entry/exit fold-boundary assertions. All were tagged Coinbase-only and `etoro_price_equivalence=not_established`.
- Network/provider requests: zero.
- Verified cost-adjusted results: null for every series.

## M15 results

Aggregated trades are gross R, before any verified spread, fees, slippage, or financing.

| Asset | Validation trades | Validation total R | Validation PF | Test trades | Test total R | Test PF |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 351 | -15.296 | 0.933 | 296 | +26.693 | 1.151 |
| ETH | 292 | -20.419 | 0.896 | 251 | +31.015 | 1.211 |
| SOL | 353 | +6.457 | 1.029 | 254 | -3.158 | 0.980 |
| XLM | 301 | +3.731 | 1.019 | 194 | -27.849 | 0.794 |
| XRP | 312 | +16.914 | 1.086 | 252 | -19.837 | 0.880 |
| **Combined** | **1,609** | **-8.613** | **0.992** | **1,247** | **+6.864** | **1.009** |

The combined test profit factor is only marginally above 1.0 before costs. BTC and ETH are positive in this test partition, while SOL is approximately flat and XLM/XRP are negative. Validation and test results are not consistently aligned across assets. The modest aggregate test result is therefore not sufficient to justify strategy promotion.

## Daily results

| Asset | Validation trades | Test trades | Test total R | Test PF |
|---|---:|---:|---:|---:|
| BTC | 0 | 1 | -1.000 | 0.000 |
| ETH | 0 | 2 | -2.000 | 0.000 |
| SOL | 0 | 2 | +1.009 | 2.009 |
| XLM | 0 | 0 | 0.000 | Not defined |
| XRP | 0 | 0 | 0.000 | Not defined |
| **Combined** | **0** | **5** | **-1.991** | **0.334** |

Five daily test trades are not a statistically useful sample. No daily conclusion should be drawn from this result.

## Limitations and next gates

1. **Execution-price equivalence:** Coinbase spot OHLC is not eToro bid/ask or fill history. eToro price adjustment and executable basis remain unverified.
2. **Transaction costs:** no complete verified per-trade cost model was available. All net R, net PF, and cost-adjusted expectancy remain null. The small M15 gross PF advantage could be erased by costs.
3. **Robustness:** the 60/20/20 split is one chronological partition per contiguous segment, not repeated rolling walk-forward optimization. No parameter tuning or regime-conditioned selection was performed.
4. **Sample dependence:** trade counts are high for M15, but multiple trades share market regimes and are not independent observations. Confidence intervals and block/bootstrap analysis are still needed.
5. **Portfolio interactions:** these are independent per-asset engine backtests, not a portfolio-level simulation with simultaneous exposure, correlated risk, and capital constraints.

## Production status

No changes to production strategy selection, asset routing, risk limits, sizing, alerts, paper portfolio, or execution. Automatic execution remains disabled. All strategy and production eligibility gates remain closed.

## Reproducibility

- Workflow run: [45245218562](https://github.com/andertrading/Trading-bot/actions/runs/45244766224)
- Artifact: [coinbase-oos-walkforward-45244766224](https://github.com/andertrading/Trading-bot/actions/runs/45244766224/artifacts/14852528494)
- Artifact SHA-256: `01d097d31b547d3bc75e42da35f8f602b4b1e65315428a573e800e899f6990ae`


## Exploratory uncertainty check — M15 test partition

A calendar-week block bootstrap was applied to the 1,247 M15 test trades. Weekly P&L/trade-count blocks were sampled with replacement **within each of the 16 asset/contiguous-segment test samples**, preserving each segment's number of calendar weeks and retaining zero-trade weeks. The fixed seed was `20261123`; 20,000 replicates were used.

- Observed combined test result: **+6.864 gross R**, 1,247 trades, **+0.00550 R/trade**.
- One-week block bootstrap 95% interval for total R: **−74.45 to +90.59 R**.
- One-week block bootstrap 95% interval for mean R/trade: **−0.06435 to +0.06559 R/trade**.
- Two-week circular-block sensitivity: mean R/trade 95% interval **−0.04878 to +0.06235 R/trade**.
- Four-week circular-block sensitivity: mean R/trade 95% interval **−0.04129 to +0.05465 R/trade**.
- In the one-week bootstrap, 55.7% of replicates had positive aggregate gross R.

All mean-return intervals include zero. This reinforces that the small positive aggregate test result is not a demonstrated edge. The bootstrap is exploratory rather than a formal inferential guarantee: it uses a single historical test partition, weekly blocks, and independently resamples each segment; it does not model cross-asset dependence, execution costs, or portfolio capital constraints. No daily bootstrap is reported because the daily test sample has only five trades.

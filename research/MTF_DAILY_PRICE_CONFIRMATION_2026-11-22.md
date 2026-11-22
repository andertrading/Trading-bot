# M15 Daily-Confirmed Pullback — Research Results (2026-11-22)

## Scope and reproducibility

- Research-only branch; no production strategy, alert, eToro connection, or execution setting changed.
- All 12 configured assets and both timeframes were present: 24/24 snapshot series.
- Snapshot quality audit: 24/24 series passed the internal-gap checks.
- Provider freshness probe: FRESH for all six representative provider/timeframe probes.
- Full 12-series common M15 window is only about 59 days because the two ETF intraday histories are shorter.
- The more informative core universe excludes CSPX/CNDX and consists of five US stocks plus five crypto assets, with a shared M15 window from 2024-12-23 to 2026-11-20. Its synchronized OOS segment is 2025-12-07 to 2026-11-20.
- Historical candles are not eToro bid/ask execution quotes. Actual instrument execution type, price basis, spreads, slippage, account fee tier and FX costs remain unverified.

## Fixed hypothesis

The candidate `mtf_daily_price_confirmed_pullback` retains the existing M15 trend-pullback rules and requires both:
1. The last completed daily EMA50 is above daily EMA200.
2. The last completed daily close is above daily EMA200.

Daily values are made available only from the following UTC date. No intraday row can see an incomplete daily candle. This is one fixed filter test, not a parameter search.

## Synchronized core-universe OOS results

| Strategy | Trades | Wins | Total R | Average R | Profit factor (R) | Max drawdown (R) | Gross portfolio PnL |
|---|---:|---:|---:|---:|---:|---:|---:|
| Existing daily-trend MTF pullback | 145 | 61 | +8.5486 | +0.0590 | 1.1096 | 12.8803 | +$29.85 |
| Daily-trend + daily-close-above-EMA200 | 140 | 61 | +13.5486 | +0.0968 | 1.1855 | 9.8803 | +$40.62 |

The additional filter improves this particular gross OOS comparison: +5.0R, five fewer trades, higher PF and lower measured drawdown. This is a research improvement, not a validation.

### Walk-forward caution

The per-series chronological folds for the new candidate aggregate to:
- Fold 1: 23 trades, +0.585R.
- Fold 2: 23 trades, +13.166R.
- Fold 3: 96 trades, -1.738R.

The latest fold remains negative; the first fold is close to flat and both early folds have small samples. Fold totals are assembled from per-series date splits, while the synchronized portfolio uses a common universe window, so their trade totals can differ slightly. The evidence is not sufficiently stable to approve the strategy.

## Cost sensitivity

On the synchronized core M15 portfolio, with zero additional spread:
- eToro standard-fee scenario: approximately -$473.92 net, PF 0.177.
- Hybrid eToro stocks/ETFs + Bitvavo crypto maker scenario: approximately -$149.79 net, PF 0.576.

These are hypothetical fee-model simulations, not realized returns. They exclude actual bid/ask spreads and slippage, and the hybrid case excludes EUR/USDC conversion, deposit/withdrawal and network costs. The eToro model's stock fee is being expanded to include both published $1 and $2 per-side bounds in stacked PR #104. The crypto 1% per-side assumption represents the standard Bronze/Silver/Gold schedule; Club-tier reductions can apply.

## Asset concentration diagnostic

For the new candidate's 15m OOS trades, the stock subset generated 72 trades and +12.426R gross, while the five-crypto subset generated 68 trades and +1.123R gross. AAPL contributed +14.149R across 27 trades, so the stock result is concentrated and must not be mistaken for broad asset-class robustness. The ETF subset has only five recent OOS trades and is not statistically meaningful.

## Decision

- Keep the candidate research-only.
- Do not merge into production or enable alerts.
- Do not enable position sizing or execution.
- Continue with conservative stock-fee bounds and realistic spread sensitivity.
- Investigate whether a stock-only subset survives portfolio-level costs without selecting assets solely because they performed well in this same OOS sample.
- Build and test a separate daily swing hypothesis with completed higher-timeframe context; do not repurpose this M15-only candidate as a daily strategy.

## Fee source

Official eToro fee schedule: https://www.etoro.com/es/trading/fees/

The page states that stock commissions may be $1 or $2 when opening and closing depending on residence and exchange; ETFs are commission-free; standard real-crypto fees are 1% per position, with reduced rates for higher Club tiers.

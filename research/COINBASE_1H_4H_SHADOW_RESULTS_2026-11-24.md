# Coinbase 1h/4h shadow walk-forward results — 2026-11-24

## Executive decision

**Do not promote either timeframe to production. Keep both research-only.** The 1h strategy has a small positive gross result across the pooled test folds, but the edge is far too small relative to the published crypto fee scenario. The 4h strategy is negative in the pooled test fold. No verified eToro execution costs or price equivalence exist.

This is an evaluation of the manually dispatched shadow workflow, not a strategy optimization. No production strategy, thresholds, universe, risk, sizing, routing, alerts or execution were changed.

## Reproducibility and source integrity

- Workflow run: [45503847689](https://github.com/andertrading/Trading-bot/actions/runs/45503847689), completed successfully on 2026-11-24.
- Commit: `5504ac4fe2ba3d9332ff5fac0dd928dd05d79989`.
- Output artifact: `14973161732`, `coinbase-resampled-1h-4h-shadow-45503847689`.
- Artifact SHA-256: `a0a829d40dbf543d87c4c4bd010f4d58c0846def98618b48255c4ba67fff179a`.
- Ten output JSON files: BTC, ETH, SOL, XLM and XRP, each at 1h and 4h.
- All ten files report `network_requests = 0`, `research_only = true`, `production_changes = false`, `all_trades_inside_oos_fold = true`, and `verified_cost_adjusted_total_R = null`.
- Total: 826 OOS trades: 1h has 666 (423 validation, 243 test); 4h has 160 (110 validation, 50 test).
- Coinbase Exchange OHLC is not established as eToro executable bid/ask. No verified round-trip cost is available.

## Resampling and data quality

The workflow aggregated retained Coinbase M15 candles in UTC:
- Open = first, High = maximum, Low = minimum, Close = last, Volume = sum.
- Only complete target buckets were retained.
- Partial buckets were discarded; missing bars split contiguous segments.
- No fill, interpolation, cross-gap aggregation or synthetic bars were used.

Per asset, 1h produced 16,785–16,786 complete target rows, with 13–14 missing target bars and 3 segments (ETH has 4). The 4h series produced 4,193–4,194 rows, with 5–6 missing bars and the same 3–4 segment structure. Five to seven partial buckets per asset/interval were discarded. This is adequate for exploratory replay, not a claim of continuous market history.

## Aggregate results

| Timeframe | OOS fold | Trades | Gross total | Mean gross R/trade | Profit factor | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| 1h | Validation | 423 | +24.9553R | +0.0590R | 1.0954 | 37.12% |
| 1h | Test | 243 | +6.0938R | +0.0251R | 1.0399 | 34.57% |
| 4h | Validation | 110 | +4.9328R | +0.0448R | 1.0702 | 35.45% |
| 4h | Test | 50 | -6.0341R | -0.1207R | 0.8238 | 30.00% |

The 1h test is positive only before costs and its gross expectancy is very small. The 4h test is negative. These are per-trade R sums across independent asset series and segmented OOS windows, not a shared-capital portfolio P&L.

## Economic feasibility: published crypto-fee scenario

The retained trade records apply a diagnostic base crypto commission assumption of 1% per side. The user's actual eToro Club tier is not confirmed. Spread, slippage and historical eToro execution-price mismatch are not priced, so this is not a verified net result.

| Timeframe | OOS fold | Mean commission burden | Mean R after assumed commission | Sum R after assumed commission |
|---|---:|---:|---:|---:|
| 1h | Validation | 1.7310R/trade | -1.6720R/trade | -707.27R |
| 1h | Test | 1.9700R/trade | -1.9449R/trade | -472.61R |
| 4h | Validation | 0.8348R/trade | -0.7900R/trade | -86.90R |
| 4h | Test | 0.9350R/trade | -1.0557R/trade | -52.79R |

At zero spread and zero slippage, the pooled 1h test's gross result would cover only about **0.0127% per side** of commission; validation covers about 0.0341% per side. These are mathematical break-even sensitivities obtained by scaling the 1%-per-side scenario, not actual fee quotes. The required fee ceiling is far below the assumed published base rate. For 4h, test is already gross-negative.

## Asset-level observations

### 1h

| Asset | Validation trades | Validation gross R | Test trades | Test gross R |
|---|---:|---:|---:|---:|
| BTC | 97 | -2.243R | 65 | +1.737R |
| ETH | 74 | -10.325R | 48 | +5.320R |
| SOL | 94 | +19.306R | 54 | +10.942R |
| XLM | 79 | +2.401R | 34 | -5.469R |
| XRP | 79 | +15.817R | 42 | -6.436R |

SOL is the only 1h asset with positive gross R in both pooled validation and test. However, the corresponding assumed commission burden is 1.326R/trade in validation and 1.518R/trade in test. Even its test gross result would cover only about 0.1335% per side under the same zero-spread break-even calculation. This is a research lead, not an eligible signal lane; the test has now been examined and must not be repeatedly optimized against.

### 4h

| Asset | Validation trades | Validation gross R | Test trades | Test gross R |
|---|---:|---:|---:|---:|
| BTC | 24 | +3.710R | 15 | +3.006R |
| ETH | 21 | -7.541R | 9 | +0.013R |
| SOL | 24 | +0.062R | 15 | -1.807R |
| XLM | 15 | +4.572R | 5 | -5.000R |
| XRP | 26 | +4.130R | 6 | -2.245R |

BTC is positive in both folds, but only 15 test trades exist and the test timeframe aggregate is negative. This is insufficient evidence for asset selection or production use.

## Decision and next work

1. Keep M15, 1h and 4h crypto strategies research-only. Do not lower gates or change sizing to manufacture positive results.
2. Do not activate SOL 1h or BTC 4h from these already-inspected OOS results. A new, untouched forward sample and verified instrument-specific eToro round-trip costs would be required.
3. Prioritize cost resolution and lower-cost instrument research. The current eToro registry still has no assets with complete verified round-trip costs and execution-price basis.
4. Keep the existing hard risk limits and manual-only signal policy. No real account, holdings, orders or personal portfolio data were accessed.

**Bottom line:** 1h is the best gross research result so far, but it is not economically viable under the available published crypto fee assumption. 4h does not pass its test fold. There is no validated production strategy from this experiment.

# Coinbase segmented crypto backtest — 2026-11-23

## Execution and provenance

The research-only runner executed the existing backtest engine independently on each contiguous segment from the retained Coinbase Exchange-only artifact.

- Workflow run: [45241224463](https://github.com/andertrading/Trading-bot/actions/runs/45241224463)
- Results artifact: [14851472820](https://github.com/andertrading/Trading-bot/actions/runs/45241224463/artifacts/14851472820)
- Capture artifact: [14848692811](https://github.com/andertrading/Trading-bot/actions/runs/45233527066/artifacts/14848692811)
- Ten JSON result files (five assets × M15/daily), 90-day retention.
- All ten series completed. Each result reports `network_requests=0`.
- Every simulated trade is labelled `data_source=coinbase_exchange_only`, venue `Coinbase Exchange`, with price basis explicitly marked as not eToro executable bid/ask.
- Every trade includes a segment ID and its segment start/end. The runner passed each segment independently to the existing backtest engine, so indicator calculation and the existing 220-bar minimum restart at every segment boundary.
- All ten outputs passed post-run assertions: trade segment integrity true; no invalid OHLC exclusions; no provider download errors; zero trades with verified transaction costs.

This is an in-sample research run on the retained historical capture. It is not out-of-sample validation, not a paper/live performance claim, and not strategy approval.

## Segment coverage and warm-up

M15 windows contain 242 missing expected bars across the five assets. Missing candles were never filled or interpolated.

| Asset | M15 segments | Segment lengths (bars) | Warm-up rows excluded | Eligible rows |
|---|---:|---|---:|---:|
| BTC | 3 | 29,366 / 18,642 / 19,144 | 657 | 66,495 |
| ETH | 4 | 11,767 / 17,597 / 18,642 / 19,144 | 876 | 66,274 |
| SOL | 3 | 29,366 / 18,642 / 19,144 | 657 | 66,495 |
| XLM | 3 | 29,366 / 18,642 / 19,144 | 657 | 66,495 |
| XRP | 3 | 29,366 / 18,642 / 19,144 | 657 | 66,495 |

All five daily series have one continuous segment of 730 bars. The same 220-bar minimum applies at the beginning of each segment; no indicators are carried across missing-data boundaries.

## Preliminary strategy metrics

Metrics below are the existing engine's unadjusted R results before any verified transaction-cost adjustment. Profit factor is calculated from gross R wins/losses.

| Asset | M15 trades | M15 win rate | M15 total R | M15 profit factor | Daily trades | Daily win rate | Daily total R | Daily profit factor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 1,521 | 35.37% | -12.7094 | 0.9867 | 6 | 16.67% | -2.9991 | 0.4002 |
| ETH | 1,437 | 35.70% | +16.5859 | 1.0183 | 8 | 37.50% | +1.0050 | 1.2010 |
| SOL | 1,420 | 34.65% | -8.0286 | 0.9911 | 3 | 33.33% | +0.0087 | 1.0044 |
| XLM | 1,158 | 33.42% | -19.4254 | 0.9745 | 2 | 0.00% | -2.0000 | 0.0000 |
| XRP | 1,303 | 34.15% | -17.3708 | 0.9793 | 5 | 60.00% | +4.0078 | 3.0039 |

### Interpretation

- M15 profit factors range from 0.9745 to 1.0183. They cluster around 1.0 and do not demonstrate a robust edge before verified costs.
- Daily trade counts are only 2–8 per asset. These samples are far too small to support a conclusion.
- These are in-sample results on the captured period. They need independent out-of-sample and walk-forward validation before any strategy decision.
- No results from different assets or timeframes should be combined into a single headline performance figure.

## Transaction-cost and execution-price limitations

**Verified transaction-cost coverage is zero for every asset and timeframe.** The result JSON records:
- `trades_with_verified_costs = 0`
- `trades_with_complete_costs = 0`
- `trades_with_unpriced_costs = all simulated trades`
- `verified_cost_adjusted_total_R = null`
- `verified_cost_adjusted_average_R = null`

The existing engine also calculates a known-fee scenario using its eToro assumptions. Those values are not verified Coinbase trading costs and must not be presented as net performance. The execution type, fee tier, historical spread treatment, and executable price basis remain unverified.

Coinbase Exchange spot OHLC is not eToro bid/ask or historical fill-price data. The separate Coinbase/eToro reconciliation found material daily close differences, and eToro price-adjustment status remains unspecified. No Coinbase result can be substituted for eToro execution performance.

## Decision

1. The gap-aware research pipeline is now executable end-to-end and correctly preserves Coinbase-only provenance.
2. The M15 dataset can be explored only as independent contiguous segments with indicator reset and per-segment warm-up. It must never be treated as one continuous 700-day series.
3. These metrics do not establish a tradable edge; the M15 gross results are near break-even, and daily samples are too small.
4. No strategy is approved from this run. No provider routing, production strategy, sizing, risk, alert, paper-portfolio or execution behavior changed.
5. Keep all production gates closed pending out-of-sample validation, verified bid/ask/spread, price-basis reconciliation and complete transaction-cost evidence.

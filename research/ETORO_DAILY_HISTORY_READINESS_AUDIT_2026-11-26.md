# eToro daily history readiness audit — 2026-11-26

## Decision

**Do not run or approve an eToro daily strategy backtest from this retained capture yet.** The seven equity/ETF series have sufficient raw row counts, but none is continuous. When each missing expected exchange session is treated as a segment boundary, no segment reaches the strategy's 220-bar minimum. In addition, the capture does not establish split/dividend adjustment policy or executable bid/ask price basis.

This is a data-readiness decision, not a strategy-performance result. No performance backtest was run and no strategy, risk, sizing, universe, signal, or production setting was changed.

## Source and reproducibility

- Source: retained eToro public historical chart-candle artifact from Actions run [45486134324](https://github.com/andertrading/Trading-bot/actions/runs/45486134324), artifact `14963623696`.
- Capture time: `2026-11-24T20:57:56.242501+00:00`.
- Interval: daily; 1,000 candles requested per instrument.
- Artifact ZIP SHA-256: `06a374cd59db04b91afb085af3afaa62b84406a18b91d48b9a06828a64d38eb6`.
- The archive contains `etoro_historical_candles.json`.
- `account_data_accessed=false`; `order_actions_performed=false`.
- No new eToro, Yahoo, Twelve Data, EODHD, or other provider request was made for this audit. All calculations used the retained artifact locally.

## Seven equity/ETF series

The expected-session audit was independently recalculated with the configured exchange calendars (XLON for CSPX/CNDX and XNYS for the five US stocks). Recalculated missing-session dates match the collector's recorded list exactly.

| Asset | Bars | First date | Last date | Missing expected sessions | Longest uninterrupted segment | Segments >= 220 bars |
|---|---:|---|---|---:|---:|---:|
| CSPX | 513 | 2024-10-31 | 2026-11-24 | 10 | 158 | 0 |
| CNDX | 1,000 | 2022-11-02 | 2026-11-24 | 27 | 145 | 0 |
| TSLA | 1,000 | 2022-10-26 | 2026-11-24 | 28 | 204 | 0 |
| NVDA | 1,000 | 2022-10-26 | 2026-11-24 | 28 | 204 | 0 |
| AAPL | 1,000 | 2022-10-26 | 2026-11-24 | 28 | 204 | 0 |
| MSFT | 1,000 | 2022-10-26 | 2026-11-24 | 28 | 204 | 0 |
| TTWO | 1,000 | 2022-10-26 | 2026-11-24 | 28 | 204 | 0 |

All seven are marked `continuous_history_usable=false` by the collector. The longest-segment calculation splits at every missing expected session; it does not fill or interpolate missing bars. Since the current strategy requires 220 bars before signal evaluation, a strict segmented replay would have zero eligible segments for all seven assets.

This does not prove that every possible research treatment is invalid. It does mean that a continuous/segmented replay cannot honestly be presented as complete using this capture without either obtaining better source history or explicitly changing and separately validating the missing-session policy. Do not silently relax the 220-bar warm-up or bridge gaps.

## Volume quality

| Asset group | Missing volume values |
|---|---:|
| CSPX | 0 / 513 |
| CNDX | 138 / 1,000 (13.8%) |
| TSLA, NVDA, AAPL, MSFT, TTWO | 143 / 1,000 each (14.3%) |

The artifact does not carry the full volume-provenance metadata required by `strategies.evaluate_signal_at` to award the relative-volume bonus. Therefore the volume bonus must remain disabled for this evidence set, including CSPX. Non-null chart volume alone is not sufficient provenance.

## Price-basis and replay compatibility blockers

Every instrument records:

- `price_adjustment_status=UNSPECIFIED`.
- `execution_price_basis_confirmed=false`.
- Historical source scope: `HISTORICAL_CHART_OHLC; BID_ASK_AND_EXECUTION_BASIS_UNSPECIFIED`.
- Adjustment scope: `UNSPECIFIED; NOT_ASSUMED_SPLIT_OR_DIVIDEND_ADJUSTED`.

The production snapshot loader in `backtest_v3.py` requires a verified adjustment policy and rejects an unverified daily snapshot. The normal data engine also rejects missing expected daily sessions in its active history window. This retained eToro capture therefore cannot be treated as a production-compatible snapshot or as executable-price evidence.

The NVDA close return recorded for 2026-11-23 is approximately +31.22%. It was not removed as an apparent outlier: independent market-history sources also report a roughly +31.48% move on that date. See [Yahoo Finance historical prices](https://finance.yahoo.com/quote/NVDA/history/) and [Investopedia's 2026-11-23 market recap](https://www.investopedia.com/stock-market-today-dow-jones-s-and-p-500-nasdaq-11232026-12274147). Large moves must be investigated, not automatically winsorized or repaired.

## What this audit does and does not establish

Supported:

1. Seven eToro equity/ETF series have at least 513 raw daily bars and no conflicting duplicate timestamps.
2. All seven have missing expected exchange sessions; none has a 220-bar uninterrupted segment under strict segmentation.
3. Six of seven have missing volume values; the artifact lacks the strategy's required volume-provenance metadata.
4. Historical adjustment policy and executable bid/ask basis remain unconfirmed.

Not established:

1. Split- or dividend-adjustment correctness.
2. Historical bid/ask, midpoint, or executable fills.
3. Complete round-trip commissions, spread, slippage, FX, or financing costs.
4. Net strategy expectancy, statistical robustness, or production eligibility.

## Next gate

Do not tune the strategy or publish performance metrics from this capture. A future research run needs a source whose adjustment policy is documented and whose expected-session coverage is complete enough for the chosen walk-forward method. Even then, eToro execution-price equivalence and complete round-trip costs remain separate gates.

No paid-provider workflow, historical backfill, manual eToro capture, alert, or account endpoint was invoked.
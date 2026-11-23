# Provider coverage audit — 2026-11-23

## Status

Research-only documentation. This audit reuses existing GitHub Actions evidence and repository code. It makes **no new market-data API requests**, consumes no new provider credits, changes no provider routing, and does not alter strategy, risk, alerts, sizing, paper ledgers, or trading behavior.

## Twelve Data — historical coverage evidence

Source workflow runs:

- [Asset coverage test — 2026-11-19](https://github.com/andertrading/Trading-bot/actions/runs/44428548868)
- [API connection test — 2026-11-19](https://github.com/andertrading/Trading-bot/actions/runs/44428970941)
- [ETF listing search — 2026-11-19](https://github.com/andertrading/Trading-bot/actions/runs/44430571838)
- [ETF history diagnostic — 2026-11-20](https://github.com/andertrading/Trading-bot/actions/runs/44839728243)

The 24-request coverage run returned **20/24 successful series**:

| Universe | Daily | 15-minute | Result |
|---|---:|---:|---|
| 5 US equities (TSLA, NVDA, AAPL, MSFT, TTWO) | 5/5 | 5/5 | Available |
| 5 crypto assets (BTC, ETH, SOL, XLM, XRP) | 5/5 | 5/5 | Available |
| CSPX and CNDX (LSE, USD) | 0/2 | 0/2 | Plan restriction |

### Observed depth in the successful coverage run

The request used `outputsize=5000`.

- US equities: 5,000 M15 bars per instrument, from 2026-02-13 14:00 through 2026-11-18 15:45 as returned (the coverage request did not explicitly set a timezone); daily results ranged from 4,124 to 5,000 bars, with the oldest returned dates between 2007-01-05 and 2010-06-29.
- Crypto: 5,000 M15 bars per instrument, from 2026-09-27 22:30 through 2026-11-19 00:15 as returned (the coverage request did not explicitly set a timezone); daily results ranged from 2,292 to 3,371 bars, with the oldest returned dates between 2017-08-28 and 2020-08-11.

These are provider availability observations from that run, not proof that every returned bar is complete, continuous, correctly adjusted, or equivalent to eToro's executable prices.

### ETF restriction

The listing search identified exact USD LSE listings for both ETFs:

- CSPX — LSE / MIC XLON, USD, Grow access.
- CNDX — LSE / MIC XLON, USD, Grow access.

The Basic-plan history requests returned HTTP 404 with the provider message that the symbol is available from the Grow or Venture plan. The failure is a plan entitlement restriction, not a ticker-resolution failure. Do not retry alternative symbol spellings or repeatedly query these endpoints on Basic.

### Existing provider policy

The current data engine already routes the five US equities and five crypto assets to Twelve Data. Daily bars request split adjustment; intraday bars request unadjusted prices. The engine validates metadata, currency, candle normalization, completeness, market sessions, and gaps before returning a usable frame.

The extended M15 research backfill is opt-in and uses contiguous windows (180 days for US equities and 50 days for crypto, with a 700-calendar-day default). A fresh full backfill is estimated in the repository at approximately 90 Twelve Data requests for the ten supported non-ETF instruments. **The existing 700-day M15 research snapshots were already produced and are available in the retained GitHub artifacts listed below; do not re-download them merely to repeat the same research.**

The existing documentation records a published Twelve Data Basic allowance of 800 credits/day and 8 credits/minute. This is plan documentation, **not a live account balance**. Remaining credits and current account entitlement were not available in the historical workflow logs.

## Recovered immutable snapshot artifacts — 2026-11-21

The 2026-11-22 research run [#44978022120](https://github.com/andertrading/Trading-bot/actions/runs/44978022120) downloaded two earlier GitHub Actions artifacts rather than rebuilding the M15 series:

- [extended-intraday-backtest-2026-11-21, artifact 14690539346](https://github.com/andertrading/Trading-bot/actions/runs/44862820157)
- [us-equity-m15-filtered-backfill-2026-11-21, artifact 14704177789](https://github.com/andertrading/Trading-bot/actions/runs/44896519696)

The first artifact contains the original 2-year daily core snapshots, 4 crypto M15 series, and 2 ETF M15 series. The second contains the 5 corrected US-equity M15 series. The research workflow added a 700-day SOL M15 series from Coinbase during the run, yielding 24 audited asset/timeframe combinations.

The resulting [snapshot audit](https://github.com/andertrading/Trading-bot/actions/runs/44978022120) reported 24/24 gap-free series. The common calendar bounds were:

| Universe | Timeframe | Common window |
|---|---|---|
| 10 core stocks/crypto | M15 | 2024-12-23 to 2026-11-20 (~697 days) |
| All 12 assets | M15 | 2026-09-22 to 2026-11-20 (~59 days) |
| 10 core stocks/crypto | Daily | 2021-11-22 to 2026-11-20 (~1,824 days) |
| All 12 assets | Daily | 2025-11-21 to 2026-11-20 (~364 days) |

Thus, the full 12-asset universe is not synchronized over the full 700-day M15 or five-year daily core windows; the ETF histories constrain the common windows.

**ETF daily-depth discrepancy:** the source manifest and snapshot-quality report list 253 CSPX and 255 CNDX daily bars from 2025-11-21 to 2026-11-20, approximately one year. This conflicts with the research report field name `retained_two_year_etf_snapshots` and the PR #111 description of retained two-year ETF history. Treat the actual manifest dates/row counts as authoritative; the two-year label needs correction or investigation.

**Raw-snapshot retention limitation:** the 2026-11-22 strategy-research artifact uploaded metrics and QA reports, but did not include the merged `historicos_full_merged/` OHLC files. The original M15 and 2-year daily files remain available in the two source artifacts above. The five-year daily extension for the ten core assets was generated during the research run (1,255–1,256 equity rows and 1,826 crypto rows), but its raw CSVs were not included in the uploaded research artifact; only the extension summary and derived research outputs remain. Reproducing the exact five-year daily run therefore requires either recovering a separately retained raw artifact or requesting those histories again. Do not assume the five-year daily CSVs can be replayed from the strategy metrics artifact.

## Crypto volume-data defect found in the raw snapshots

A direct audit of the downloaded CSVs found that Twelve Data's crypto OHLCV snapshots do not contain usable volume:

- BTC, ETH, XLM and XRP: all 67,200 M15 rows per asset have `Volume = 0`; all 731 daily rows per asset also have `Volume = 0`.
- SOL daily: all 731 rows have `Volume = 0`.
- The five corrected US-equity M15 series and the two ETF M15 series contain no zero-volume rows in these artifacts.
- SOL M15 in the later research run was recovered from Coinbase and must be audited separately; its volume must not be inferred from the Twelve Data daily series.

This means relative-volume filters are not meaningful on the affected crypto snapshots. A volume-confirmed strategy's lack of crypto trades cannot be interpreted as evidence that the strategy found no crypto setups: the required volume input is absent/zero. Do not replace zero volume with estimates or silently splice Coinbase volume into Twelve Data OHLC. Any crypto volume-based research requires a consistent, explicitly validated OHLCV source and a fresh source-parity review.

## EODHD — ETF daily evidence and credit guard

Source workflow runs:

- [EODHD daily diagnostic — 2026-11-21](https://github.com/andertrading/Trading-bot/actions/runs/44843123274)
- [EODHD daily diagnostic — 2026-11-21](https://github.com/andertrading/Trading-bot/actions/runs/44843210286)
- [Manual-only safeguard — commit #146](https://github.com/andertrading/Trading-bot/commit/5f619b6d0846ded6b28a8361fc6563a8a737a36c)

The diagnostics succeeded for CSPX and CNDX:

- Exact LSE/USD listings resolved by ISIN.
- 253 CSPX and 255 CNDX completed daily bars.
- Date range: 2025-11-21 through 2026-11-20.
- Zero malformed OHLC bars.
- Both histories passed the then-current LSE freshness check.

The EODHD adapter additionally requests split events and fails closed if the split response is missing or malformed. The free-tier history evidence is approximately one year; it does not solve a multi-year daily history requirement and does not provide the M15 backfill used by this project.

**Credit safeguard:** commit #146 explicitly removed the scheduled EODHD capture because requests may consume paid API credits. Keep EODHD capture manual-only unless the user explicitly authorizes a run. The remaining credit balance was not established by the two successful diagnostics.

## What this evidence does and does not establish

Established:

1. Twelve Data returned daily and M15 history for the five configured US equities and five crypto instruments in the 2026-11-19 coverage run, but the recovered crypto snapshots have zero volume.
2. Twelve Data Basic did not provide history for the two LSE ETF listings in that run.
3. EODHD returned valid, fresh one-year daily history for CSPX and CNDX in the 2026-11-21 diagnostics.
4. The 700-day core M15 research snapshots already exist in retained source artifacts; the five-year daily raw extension was not retained in the final research artifact.
5. Existing code already uses Twelve Data for the ten non-ETF assets and has a separate opt-in extended-history path.

Not established:

1. Historical OHLC parity between Twelve Data and eToro.
2. Complete continuity of all 15-minute or daily bars across the full research windows.
3. Corporate-action adjustment parity for every asset and provider.
4. Historical bid/ask, slippage, or executable-price equivalence.
5. Current provider dashboard balances, remaining credits, or permission to consume further credits.

## Next research sequence

1. Reuse the retained M15 and 2-year daily source artifacts; audit timestamps, session calendars, missing bars, duplicates, OHLC coherence, split events, and volume without new market-data API calls.
2. Recover or re-create a durable raw artifact for the five-year daily core data only after checking provider quota and obtaining explicit authorization; ensure the next workflow uploads the merged OHLC snapshots and manifest.
3. Investigate the ETF daily-depth mismatch (253/255 rows versus the two-year label) before treating the full 12-asset daily window as two years.
4. Add a diagnostic-only overlap comparison between Twelve Data and eToro for the ten non-ETF instruments. Do not repair one source with the other or silently combine their bars.
5. For crypto volume-based hypotheses, test a consistent OHLCV source (Coinbase is a research candidate) and verify price/volume provenance and eToro parity before using it.
6. Before any fresh provider backfill or further EODHD capture, check the provider dashboard for current remaining credits and obtain explicit authorization for the planned request count.
7. Do not relax gap guards, invent candles, change provider routing, or approve assets from availability evidence alone.

## Production and safety state

- 12 configured assets remain in the registry.
- The economic registry remains 0/12 eligible.
- Strategy validation remains closed.
- Position sizing and operational alerts remain disabled.
- The virtual research capital remains separate from the user's real eToro account.
- No real account holdings, balances, positions, PnL, or order data were used in this audit.

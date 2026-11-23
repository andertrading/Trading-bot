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

The extended M15 research backfill is opt-in and uses contiguous windows:

- US equities: 180-day windows.
- Crypto: 50-day windows.
- Default research lookback: 700 calendar days.
- The repository's estimate is approximately 90 additional Twelve Data requests for all ten supported non-ETF instruments, plus normal daily history calls.

The existing documentation records a published Twelve Data Basic allowance of 800 credits/day and 8 credits/minute. This is plan documentation, **not a live account balance**. Remaining credits and current account entitlement were not available in the historical workflow logs.

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

1. Twelve Data successfully returned daily and M15 history for the five configured US equities and five crypto instruments in the 2026-11-19 coverage run.
2. Twelve Data Basic did not provide history for the two LSE ETF listings in that run.
3. EODHD returned valid, fresh one-year daily history for CSPX and CNDX in the 2026-11-21 diagnostics.
4. Existing code already uses Twelve Data for the ten non-ETF assets and has a separate opt-in extended-history path.

Not established:

1. Historical OHLC parity between Twelve Data and eToro.
2. Complete continuity of all 15-minute or daily bars across the full research windows.
3. Corporate-action adjustment parity for every asset and provider.
4. Historical bid/ask, slippage, or executable-price equivalence.
5. Current provider dashboard balances, remaining credits, or permission to consume further credits.

## Next research sequence

1. Reuse the existing immutable Twelve Data snapshots where available; audit timestamps, session calendars, missing bars, duplicates, OHLC coherence, split events, and volume without additional API calls.
2. Add a diagnostic-only overlap comparison between Twelve Data and eToro for the ten non-ETF instruments. Do not repair one source with the other or silently combine their bars.
3. Keep CSPX/CNDX daily provider comparison separate: EODHD live history, Yahoo extended research history, and eToro chart history have different documented scopes.
4. Before any extended 700-day M15 backfill or further EODHD capture, check the provider dashboard for current remaining credits and obtain explicit authorization for the planned request count.
5. Do not relax gap guards, invent candles, change provider routing, or approve assets from availability evidence alone.

## Production and safety state

- 12 configured assets remain in the registry.
- The economic registry remains 0/12 eligible.
- Strategy validation remains closed.
- Position sizing and operational alerts remain disabled.
- The virtual research capital remains separate from the user's real eToro account.
- No real account holdings, balances, positions, PnL, or order data were used in this audit.

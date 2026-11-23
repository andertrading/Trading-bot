# eToro market-data audit and integration status

**Current status:** the manual audit validates instrument discovery, exchange metadata and market quotes only. It does not access the real-account portfolio, balances, positions, pending orders, account-specific eligibility or account risk. It never submits, modifies or cancels orders.

## Current audit scope

The workflow `.github/workflows/etoro_read_only_audit.yml` runs `etoro_read_only_audit.py` manually using the repository's `ETORO_API_KEY` and `ETORO_USER_KEY` secrets.

The active audit path makes these read-only market-data requests:

1. `GET /api/v1/market-data/search` — resolve each configured symbol to one exact eToro instrument and verify its instrument class.
2. `GET /api/v1/market-data/instruments` — retrieve instrument display metadata, including `instrumentID`, `exchangeID`, `symbolFull` and `priceSource`.
3. `GET /api/v1/market-data/exchanges` — resolve the exchange IDs to exchange descriptions.
4. `GET /api/v1/market-data/instruments/rates` — validate a complete bid/ask quote set and timestamps for the resolved instruments.

The audit does **not** call the account PnL, position-breakdown, eligibility or what-if-cost endpoints. The client contains legacy read-only helpers for some of those endpoints, but they are outside the active audit path and must not be used as inputs or constraints for Trading Bot 3.0. In particular, the user's real-money portfolio is not part of the bot's virtual paper portfolio or its $1,000 reference capital.

## Exchange-calendar mapping

The calendar is derived from the exchange description returned by eToro, not from `priceSource`:

- `LSE` / London Stock Exchange → `XLON`.
- `Nasdaq` and `NYSE` → `XNYS`, the supported common US-equity session calendar. In `exchange_calendars`, `NASDAQ` is an alias for `XNYS`.
- `Digital Currency` for a configured crypto instrument → `24/7`.
- Unknown or ambiguous exchange descriptions are rejected; the price feed name is not used as a fallback to guess a listing venue.

The configured calendar must match the eToro-derived calendar for every enabled instrument before the quote request is admitted.

## Live eToro audit result — 2026-11-22

The user-triggered audit returned:

- Instrument resolution: **12/12 resolved**, with the expected ETF/STOCK/CRYPTO classifications.
- Instrument display metadata: **VALIDATED**.
- Exchange metadata: **VALIDATED**.
- Exchange-to-calendar mapping: **12/12 matched**.
- Market quote validation: **VALIDATED_WITH_CLOSED_MARKETS**.
- Overall audit: **PASS**.
- `account_portfolio_data_accessed`: `false`.
- `instrument_eligibility_status`: `NOT_IN_SCOPE` (expected; not a failed check).
- `portfolio_risk_admission_ready`: `false` (intentionally not assessed by this market-data audit).

Resolved instruments and venues:

| Symbol | eToro instrument ID | eToro exchange | Calendar | eToro symbol |
|---|---:|---|---|---|
| CSPX | 3434 | LSE | XLON | CSPX.L |
| CNDX | 8015 | LSE | XLON | CNDX.L |
| TSLA | 1111 | Nasdaq | XNYS | TSLA |
| NVDA | 1137 | Nasdaq | XNYS | NVDA |
| AAPL | 1001 | Nasdaq | XNYS | AAPL |
| MSFT | 1004 | Nasdaq | XNYS | MSFT |
| TTWO | 1388 | Nasdaq | XNYS | TTWO |
| BTC | 100000 | Digital Currency | 24/7 | BTC |
| ETH | 100001 | Digital Currency | 24/7 | ETH |
| SOL | 100063 | Digital Currency | 24/7 | SOL |
| XLM | 100020 | Digital Currency | 24/7 | XLM |
| XRP | 100003 | Digital Currency | 24/7 | XRP |

At the time of this run, the London and US exchanges were closed:

- CSPX and CNDX were accepted as `CLOSED_LAST_SESSION` quotes from the last completed XLON session.
- TSLA, NVDA, AAPL, MSFT and TTWO were accepted as `CLOSED_LAST_SESSION` quotes from the last completed US session.
- BTC, ETH, SOL, XLM and XRP had `FRESH` quotes.

This is why `market_quotes_validated` is `true` while `complete_realtime_quotes_validated` is `false`. A last-session quote is accepted for audit diagnostics only; it is not a fresh entry price and must not be used to generate an actionable entry while that exchange is closed.

## Quote validation policy

- A quote is considered fresh when its timestamp is no more than 30 seconds old.
- A timestamp more than five seconds in the future is rejected. A small clock skew of up to five seconds is tolerated; the observed crypto timestamps were four seconds ahead of the audit clock.
- For a closed exchange only, a quote may be accepted as `last_session` when its timestamp falls inside the last completed exchange session and is no more than seven days old.
- A stale quote while its exchange is open is rejected.
- A stale quote for a 24/7 crypto instrument or an instrument without a known calendar is rejected.
- A delayed, incomplete, duplicate, crossed or invalid bid/ask set is rejected.

To verify all listed instruments with fresh quotes, rerun the manual audit during the overlap of the London and US regular trading sessions. A successful closed-market audit must not be confused with complete real-time quote validation.

## Strategy and signal-release gates

Passing this audit does not approve a trading strategy or enable alerts. The following production safeguards remain intentionally disabled until separately validated:

- `STRATEGY_VALIDATED = False`.
- `ALLOW_POSITION_SIZING = False`.
- `ETORO_EXECUTION_TYPES_CONFIRMED = False`.
- `ETORO_FEE_SCHEDULE_CONFIRMED = False`.
- `ETORO_EXECUTION_PRICE_BASIS_CONFIRMED = False`.
- Close fees, spreads and FX conversion remain unconfirmed.
- `SIGNAL_ALERTS_ENABLED` defaults to `false`.
- The bot remains paper/manual-signal-only and has no automatic execution path.

Historical research has not established a robust positive net edge after realistic trading costs. No strategy is approved for trade-ready alerts. Keep the strategy and alert gates closed while prospective paper-forward evidence is collected.

## Safety boundary

1. Do not query or use the user's real-money holdings, balances, open positions, pending orders or stop risk as bot inputs or constraints.
2. Keep the virtual paper portfolio and its $1,000 reference capital separate from the user's eToro account.
3. Do not use last-session or delayed prices as actionable entry prices.
4. Do not infer eToro execution prices from Yahoo Finance or Twelve Data OHLC data. Their price basis must be validated separately against eToro bid/ask quotes.
5. Do not enable sizing or alerts merely because instrument resolution or the market-data audit passes.
6. Never add order creation, modification, closure, cancellation or other trading-write operations.

## Official and technical references

- eToro API authentication: https://api-portal.etoro.com/core/getting-started/authentication
- eToro API documentation portal: https://api-portal.etoro.com/
- eToro authentication guide: https://api-portal.etoro.com/core/getting-started/authentication
- eToro market-order guide (includes the documented instrument-search example and market-rate guidance): https://api-portal.etoro.com/guides/market-orders
- `exchange_calendars` calendar aliases: https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/calendar_utils.py


## Additional eToro evidence collector — 2026-11-23

The separate workflow .github/workflows/etoro_market_evidence.yml now performs a broader **read-only instrument and hypothetical-cost capture** than the original market-data audit described above. Its allowlisted requests are:

- GET /api/v1/market-data/search
- GET /api/v2/market-data/instruments
- GET /api/v1/market-data/instruments/rates
- POST /api/v2/trading/info/eligibility
- POST /api/v2/trading/info/costs

The POST requests are informational what-if queries only. The collector explicitly blocks all endpoints outside this allowlist. It does not request account balances, PnL, holdings, open positions, pending orders, order history, or submit/modify/cancel orders.

### Latest captured result

Workflow run 45195086845 completed on 2026-11-23:

- 12/12 configured instruments resolved.
- 12/12 validated as supporting REAL, unleveraged LONG (1x) for the account-specific eligibility response.
- 12/12 realtime quotes captured.
- 12/12 hypothetical opening-cost estimates captured.
- 0 instrument-level errors.
- account_data_accessed: false and order_actions_performed: false.
- Overall collector status COMPLETE; net_performance_validation remains NOT_VALIDATED.

The eligibility response is useful for instrument admission and order constraints (including minimum amount and fractional quantity support). It is not a substitute for a completed execution ticket or a guarantee of future fills.

### Cost and spread interpretation

The eToro what-if-cost response is explicitly scoped to a **hypothetical $100 opening only**. It must not be doubled to invent a closing fee, nor treated as a verified round-trip cost. In the captured sample:

- US stocks reported a $1 opening transaction-fee component per hypothetical order.
- Crypto reported a $0.99 opening transaction-fee component.
- ETFs reported no transaction-fee component in that opening sample.
- The reported opening market-spread component is retained separately from the quote-derived bid/ask spread.

The quote-derived spread is a current snapshot, not a historical spread model. The reconciliation found material differences for CNDX, TTWO and XLM, and the API omitted the market-spread component for BTC. Preserve both raw observations; do not sum them or select one as authoritative without further evidence.

Consequently, this capture materially improves instrument eligibility and opening-cost knowledge, but it does not by itself complete historical OHLC-to-execution price parity, closing costs, historical spread, or all-in net performance. The economic registry remains the admission-grade source for verified round-trip costing. Unknown close costs remain unknown.



### Automated forward quote evidence collection

Starting with the workflow update in PR #158, the read-only evidence collector is scheduled for weekdays at 15:00 UTC, which overlaps the regular London and US sessions across seasonal clock changes. The manual workflow-dispatch option remains available. Each run uploads its evidence JSON artifact with 90-day retention.

This builds a timestamped **forward** sample of bid/ask observations and opening-cost what-if responses. It does not reconstruct historical spreads before the first capture, and a single current snapshot must never be substituted for a historical spread series. Market holidays and closed sessions can produce explicit skipped-cost records; they are not zero-cost observations.



## eToro historical chart candles — additional market-only source

The eToro Market Data API also exposes:

GET /api/v1/market-data/instruments/{instrumentId}/history/candles/{direction}/{interval}/{candlesCount}

The new weekly collector in etoro_historical_candles.py requests up to 1,000 daily candles for each configured instrument. Its endpoint allowlist contains only exact instrument search and this historical market-candle path. It does not call account, portfolio, positions, PnL, orders, or trade-history endpoints.

The normalized report preserves timestamped OHLC and volume fields and checks instrument IDs, timezone-aware timestamps, duplicate bars, positive prices, and OHLC coherence. It explicitly labels the source as historical chart OHLC: bid/ask history, execution-price basis, and split/dividend adjustment status remain unspecified. Therefore this dataset can support eToro-vs-provider historical price reconciliation, but it cannot alone prove executable bid/ask prices or complete round-trip costs.

The weekly workflow requests 12 instruments and up to 1,000 daily candles per instrument, well below the documented market-data request rate when run sequentially. Artifacts are retained for 90 days. This is read-only market data, not personal trading history.



The API path requires the documented interval enum (for example, OneDay), not the shorthand 1d. The client accepts concise internal aliases and maps them to the official API enum before making the request. The candle endpoint documents a maximum of 1,000 candles per request and a shared market-data quota of 120 requests per 60 seconds.


## Historical candle and secondary OHLC reconciliation — 2026-11-23

The first live historical capture resolved all 12 configured instruments. Seven histories passed the duplicate/structural checks; five crypto histories (BTC, ETH, SOL, XLM, XRP) remain unusable.

The crypto failures are not caused by local timestamp conversion. The raw eToro response contains identical fromDate strings for multiple candles on US daylight-saving transition dates (2024-11-03, 2025-11-02 and, in the latest capture, 2026-11-01), while the OHLC values differ. The collector excludes the conflicted timestamps, retains both source values in diagnostics, and marks the affected history unusable. It does not choose a candle or synthesize a replacement.

A secondary Yahoo Finance comparison was run for the seven usable eToro histories, using the same auto_adjust=False, split-aware daily OHLC policy already used by the data engine. It is a diagnostic comparison only. The current incomplete UTC date is excluded and incoherent Yahoo OHLC envelopes are rejected.

Latest overlapping completed daily bars:
- CSPX: 509
- CNDX: 992
- TSLA, NVDA, AAPL, MSFT and TTWO: 995 each

Median absolute same-date close differences were approximately 0.13%–0.16% for six assets and 0.16% for TTWO; 95th-percentile close differences ranged from approximately 0.27% to 0.73%. Comparing the same eToro date to Yahoo's previous or next calendar date materially worsened the median close difference, so a one-day date shift is not supported by this sample.

The open/high/low comparisons are materially less consistent. There are individual large discrepancies in both sources, including abnormal Yahoo ETF ranges and eToro-vs-Yahoo stock opening/range differences. These may reflect session definitions, venue/extended-hours prints or provider anomalies; the available evidence does not establish which. Consequently:
- Same-date closes are broadly aligned at a diagnostic level, not certified.
- Daily open/high/low price-basis parity is not validated.
- Historical executable bid/ask parity is not validated.
- Do not use these comparisons to approve entry, stop or target execution assumptions, or to mark transaction costs complete.

The separate etoro_yahoo_reconciliation.json artifact retains per-field distributions and dated OHLC outliers for review. The collector remains market-only and performs no account, position, PnL, order or trade-history requests.

## Forward spread sample collection

The weekday market-evidence workflow continues to collect timestamped realtime bid/ask snapshots. A separate weekly GitHub Actions audit now reads the retained etoro-market-evidence artifacts and reports per-asset median, p90, p95 and maximum quote-derived spread after deduplicating to one latest sample per UTC date.

The first aggregation found one available forward sample and zero assets with the minimum 30 distinct dates. The report remains FORWARD_SAMPLE_COLLECTION; at 30 or 60 samples it will only become ready for manual review, never automatic approval. It does not modify asset_economic_registry.json, infer round-trip fees, or access account/position/order data.


The forward spread audit currently samples the scheduled weekday 15:00 UTC snapshot (manual captures can differ) and keeps one latest realtime observation per asset and UTC date. Its percentiles describe this fixed-time snapshot distribution, not continuous intraday or full-session spread behavior.

### Split-adjustment spot check

The eToro NVDA daily history spans the 2024-06-10 10:1 split. Its close was 120.6418 on 2024-06-07 and 121.5274 on 2024-06-10, so the captured series is on the post-split price scale across that event. This is consistent with Yahoo's split-adjusted history and rules out an obvious 10x discontinuity in this NVDA series. It is a spot check, not a full corporate-action audit and does not verify Twelve Data's separate adjustment pipeline.

The eToro TSLA history begins 2022-10-25, after the 2022-08-25 3:1 split, so this capture cannot independently test TSLA's pre/post-split adjustment. Neither spot check changes the Twelve Data split-evidence gate in data_engine.py.


### Historical candle capture — 23 November 2026

The read-only daily candle capture resolved all 12 configured instruments. Seven histories pass the strict duplicate-free gate. Five crypto histories contain conflicting OHLC rows under the same UTC timestamp on daylight-saving transition dates: BTC, ETH, XLM and XRP have two conflicted dates each (2024-11-03 and 2025-11-02); SOL has those two plus 2026-11-01. The normalizer removes **both** rows for every conflicted timestamp rather than choosing either value.

After excluding those ambiguous dates, the five crypto series retain 994–996 daily candles, above the 220-candle research minimum. The collector therefore exposes a separate `research_history_usable` flag while keeping strict `history_usable=false`. The exclusions are recorded per asset and the overall report remains PARTIAL. Research usability is not strict data approval.

The initial Yahoo comparison was run before this research-only flag existed, so it compared only the seven strict histories. Median absolute daily close differences were approximately 0.13–0.16% for CSPX, CNDX and the five equities; 95th-percentile absolute close differences ranged from approximately 0.27% to 0.73%. Individual outliers reached 4–7% in some equities, and Yahoo returned one invalid OHLC row for CSPX and two for CNDX. These are diagnostic differences, not evidence that either provider is the executable-price truth. The comparison does not validate eToro's chart price basis, spread, FX, fees, slippage or execution parity.

All eToro candle records continue to declare adjustment basis UNSPECIFIED, no historical bid/ask availability, and no confirmed execution-price basis. Do not use these chart candles as production execution prices or treat the research-only crypto series as strict-quality history.

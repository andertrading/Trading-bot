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


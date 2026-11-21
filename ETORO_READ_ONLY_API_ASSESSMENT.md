# eToro API: read-only integration assessment

**Status:** research only. No live API calls, credentials, account access, or trading permissions are enabled by this document.

## Objective

Replace manual/unknown portfolio state with a verified, read-only account snapshot. Keep the bot manual-signal-only: it must never submit, modify, or cancel orders.

## Official API capabilities verified

Documentation reviewed on 2026-11-21:

- Authentication supports an application key plus a user key in `x-api-key` and `x-user-key` headers, with a unique `x-request-id` per request. The API portal describes generating a key with **Read** permissions for portfolio-only access. Access remains subject to account eligibility and the permissions actually granted.
- Real-account portfolio endpoint: `GET /api/v1/trading/info/real/pnl`. The documented response contains `clientPortfolio.positions`, `credit`, `orders`, `ordersForOpen`, `ordersForClose`, `ordersForCloseMultiple`, `unrealizedPnL`, and `accountCurrencyId`.
- The account currency ID is documented as `1 = USD`. The account's available-cash guide defines: `credit - sum(ordersForOpen.amount where mirrorID = 0) - sum(orders.amount)`. It explicitly excludes mirrored `ordersForOpen` from this cash calculation but includes every item in `orders`. There is a documentation casing mismatch to resolve against a real response: the PnL schema uses `credit` and `mirrorId`, while the guide's sample code uses `credits` and `mirrorID`. The adapter must validate the live payload and reject ambiguous/missing fields rather than guessing.
- Open-position records expose `positionID`, `instrumentID`, `isBuy`, `openRate`, `stopLossRate`, `amount`, `leverage`, `units`, `settlementTypeID`, `isNoStopLoss`, and fee fields. The API distinguishes CFD, real asset, swap, crypto margin, and futures settlement types.
- Bid/ask endpoint: `GET /api/v2/market-data/rates?instrumentIds=...`. Each result has bid, ask, UTC date and `quoteType` (`realtime` or `delayed`). HTTP 206 means only a subset of requested instruments was returned.
- Candle history endpoint: `GET /api/v1/market-data/instruments/{instrumentId}/history/candles/{direction}/{interval}/{candlesCount}`. It documents OHLCV intervals from one minute to one week, maximum 1,000 candles per request.
- What-if cost endpoint: `POST /api/v2/trading/info/costs`. For an `action: open` request it can return markup, market spread, transaction fee, overnight fee, over-weekend fee and SDRT. The endpoint documentation lists read scopes as acceptable. It is a cost-estimation query, not an order submission. The request schema does not expose a planned holding-period parameter, so overnight/over-weekend values must not be treated as total lifecycle costs without validation. The documented close action is currently not supported; do not build a close-cost workflow around it yet.
- Rate limits: account reads are generally 60 requests per rolling minute; market-data endpoints and the what-if cost endpoint have their own documented quotas. Respect `Retry-After`, rate-limit headers, cache stable instrument metadata, and use exponential backoff.

## Safety interpretation

The API response is useful but is not, by itself, a complete risk calculation.

1. **Account snapshot:** require a successful response, expected account context, USD currency confirmation, and all required arrays/fields. Missing or malformed data means `UNKNOWN`, never an empty portfolio.
2. **Open positions:** a position with no stop loss (`isNoStopLoss=true`, missing stop, or non-positive stop) has no reliably bounded stop risk. Until a conservative and verified method exists for that position type, block new risk rather than treating its risk as zero.
3. **Risk conversion:** do not assume `amount`, `initialAmountInDollars`, `unitsBaseValueDollars`, or leverage alone equals loss-at-stop. Risk must account for direction, current units after partial closes, instrument multiplier, settlement type, quote currency and conversion, stop execution/gap risk, and fees. Futures and unsupported instruments must fail closed.
4. **Mirrors/copy trading:** avoid double counting positions repeated under mirror objects and the top-level portfolio. The first implementation should use one canonical source of positions and explicitly detect unsupported mirror structures.
5. **Pending orders and cash:** follow eToro's documented available-cash formula exactly: subtract manual `ordersForOpen` amounts (`mirrorId = 0`) and all `orders` amounts from `credit`. Reconcile actual response field casing first because the guide example and schema disagree (`credits` vs `credit`, `mirrorID` vs `mirrorId`). Separately assess potential risk from pending entries. Do not count closing orders as new exposure. Treat missing or unknown order arrays as an incomplete snapshot.
6. **Quotes:** only use a fresh `realtime` bid/ask for actionable spread/execution checks. A delayed quote or stale timestamp must block the relevant validation.
7. **What-if costs:** request a hypothetical cost estimate only after instrument, settlement type, direction, leverage and amount are known. Require a complete response with all applicable cost components; absent or ambiguous cost data keeps the candidate blocked.
8. **Read-only boundary:** the integration must contain only GET account/market requests and the documented what-if cost POST. It must not call trading execution, order creation, close, modify-stop, cancel, transfer, or any write endpoint.

## Proposed integration sequence

1. Add a read-only API client with environment-variable credentials, request IDs, strict timeouts, bounded retries, redacted logs, and no secrets in source control.
2. Add schema validation and fixtures based on the official response shapes. Test malformed payloads, missing positions, no-stop positions, partial HTTP 206 rates, delayed quotes, stale quotes, 401/403, 429 and 5xx.
3. Build an account snapshot normalizer. Keep portfolio status `UNKNOWN` unless the response is complete and all account-level checks pass.
4. Resolve all open instrument IDs and supported settlement types. Establish position risk only for combinations with verified unit/multiplier/FX semantics; block everything else.
5. Reconcile pending entry orders and available cash using eToro's formula, explicitly resolving the documented field-casing discrepancy. Independently compare computed cash with the broker UI before enabling any sizing.
6. Validate what-if cost results against the eToro order ticket for representative REAL and CFD instruments and for different holding periods.
7. Run in shadow/read-only mode for multiple sessions. Compare balances, positions, cash, quotes and costs manually. Only then consider removing the corresponding validation blockers.

## Not yet verified

- Whether this specific account is eligible to generate the required API key and receive the necessary read scopes.
- Whether every asset currently enabled in the bot has an eToro instrument ID, supported settlement type and usable live bid/ask.
- Historical candle depth/coverage and volume semantics for each of the 12 configured assets.
- Whether what-if estimates match the user's actual ticket, including account-specific discounts and the final execution price.
- Exact risk-at-stop semantics for every position type and instrument, including FX conversion and gap risk.

## Official references

- [Authentication](https://api-portal.etoro.com/core/getting-started/authentication)
- [Rate limits](https://api-portal.etoro.com/core/getting-started/rate-limits)
- [Real account PnL and portfolio details](https://api-portal.etoro.com/api-reference/trading--real/get-account-pnl-and-portfolio-details)
- [Real portfolio breakdown](https://api-portal.etoro.com/api-reference/trading--real/get-portfolio-breakdown)
- [Bid/ask rates](https://api-portal.etoro.com/api-reference/market-data/retrieve-bidask-rates-for-one-or-more-instruments)
- [Historical candles](https://api-portal.etoro.com/api-reference/market-data/get-instrument-candle-history)
- [What-if trading costs](https://api-portal.etoro.com/api-reference/trading--real/get-what-if-trading-cost-breakdown)
- [Calculate available cash](https://api-portal.etoro.com/core/guides/calculate-available-cash) (includes the exact pending-order deduction formula)

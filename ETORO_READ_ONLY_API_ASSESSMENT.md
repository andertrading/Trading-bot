# eToro API: read-only integration assessment

**Status:** research only. No live API calls, credentials, account access, or trading permissions are enabled by this document.

## Objective

Replace manual/unknown portfolio state with a verified, read-only account snapshot. Keep the bot manual-signal-only: it must never submit, modify, or cancel orders.

## Official API capabilities verified

Documentation reviewed on 2026-11-21:

- Authentication supports an application key plus a user key in `x-api-key` and `x-user-key` headers, with a unique `x-request-id` per request. The API portal describes generating a key with **Read** permissions for portfolio-only access. Access remains subject to account eligibility and the permissions actually granted.
- API base URL in the current official examples: `https://public-api.etoro.com`.
- Real-account portfolio endpoint: `GET /api/v1/trading/info/real/pnl`. The documented response nests the account data under `clientPortfolio`: `credit`, `unrealizedPnL`, `positions`, `orders`, `ordersForOpen`, `ordersForClose`, `ordersForCloseMultiple`, and `mirrors`. The example also shows positions nested inside individual mirrors; the top-level `clientPortfolio.positions` list is the canonical list for the first adapter to avoid double counting.
- The PnL endpoint example does **not** expose `accountCurrencyId`. The bot's current account currency is configured as USD, so the normalizer requires that configured currency to be passed in explicitly; it must not claim that this endpoint independently verifies the broker account currency. The available-cash guide defines: `credit - sum(ordersForOpen.amount where mirrorID = 0) - sum(orders.amount)`. It excludes mirrored `ordersForOpen` from this cash calculation but includes every item in `orders`. There are two documentation mismatches to resolve against the first real response: the PnL endpoint schema nests these fields under `clientPortfolio` and uses `credit`/`mirrorId`, while the available-cash guide's sample accesses root-level `credits`/`mirrorID`. The adapter currently accepts the documented PnL endpoint's nested `clientPortfolio` shape and either casing variant for those two names, but rejects missing or ambiguous fields and any unexpected root-level shape.
- Open-position records use lower-camel field names such as `positionId`, `instrumentId`, `isBuy`, `openRate`, `stopLossRate`, `amount`, `leverage`, `units`, `settlementTypeId`, and `isNoStopLoss`, plus fee fields. The API distinguishes CFD, real asset, swap, crypto margin, and futures settlement types.
- Instrument discovery endpoint: `GET /api/v1/market-data/search`. It requires a `fields` parameter and supports pagination and exact schema-field filters. The adapter requests the instrument ID, display name, internal symbol, instrument type and tradability flags; it resolves only a unique exact symbol/name match and rejects inactive, delisted, non-tradable or buy-disabled instruments. Actual IDs for the bot's 12 assets remain to be obtained from an authenticated account/API session.
- Bid/ask endpoint: `GET /api/v2/market-data/rates?instrumentIds=...`. Each result has bid, ask, UTC date and `quoteType` (`realtime` or `delayed`). HTTP 206 means only a subset of requested instruments was returned. The current helper requires a complete HTTP 200 set, fresh UTC timestamps (30-second default), realtime quotes and positive uncrossed bid/ask; it is not yet wired into signal generation.
- Candle history endpoint: `GET /api/v1/market-data/instruments/{instrumentId}/history/candles/{direction}/{interval}/{candlesCount}`. It documents OHLCV intervals from one minute to one week, maximum 1,000 candles per request.
- Instrument eligibility endpoint: `POST /api/v2/trading/info/eligibility`. It accepts up to 100 instrument IDs in one request and returns account-specific open/close permissions, minimum exposure, maximum units, order flags, unit type, and `leverageConfigs` by settlement type and direction. Each config can include leverage values and stop/take-profit percentage bounds. It is a read-only eligibility check, not order submission, and has a dedicated 20 requests/60s limit. The client validates exact coverage and the documented USD response; the manual audit calls it once for all resolved assets and requires long-position permission for every asset, printing only a pass/fail flag.
- What-if cost endpoint: `POST /api/v2/trading/info/costs`. For an `action: open` request it can return markup, market spread, transaction fee, overnight fee, over-weekend fee and SDRT. The endpoint documentation lists read scopes as acceptable. It is a cost-estimation query, not an order submission. The current client validates the documented request shape (direction, one instrument selector, settlement, order type, leverage, one sizing field and USD order currency) and validates response identity, known cost types, USD currency and a fresh UTC timestamp. The request schema does not expose a planned holding-period parameter, so overnight/over-weekend values must not be treated as total lifecycle costs without validation. The documented close action is currently not supported; do not build a close-cost workflow around it yet.
- Rate limits: account reads are generally 60 requests per rolling minute; market-data endpoints and the what-if cost endpoint have their own documented quotas. Respect `Retry-After`, rate-limit headers, cache stable instrument metadata, and use exponential backoff.

## Safety interpretation

The API response is useful but is not, by itself, a complete risk calculation.

1. **Account snapshot:** require a successful response, explicit `clientPortfolio`, explicit positions and required order arrays/fields. Supply the configured USD currency separately because this endpoint's documented example does not contain `accountCurrencyId`. Missing or malformed data means `UNKNOWN`, never an empty portfolio.
2. **Open positions:** a position with no stop loss (`isNoStopLoss=true`, missing stop, or non-positive stop) has no reliably bounded stop risk. Until a conservative and verified method exists for that position type, block new risk rather than treating its risk as zero.
3. **Risk conversion:** do not assume `amount`, `initialAmountInDollars`, `unitsBaseValueDollars`, or leverage alone equals loss-at-stop. Risk must account for direction, current units after partial closes, instrument multiplier, settlement type, quote currency and conversion, stop execution/gap risk, and fees. Futures and unsupported instruments must fail closed.
4. **Mirrors/copy trading:** the snapshot normalizer rejects any non-empty `clientPortfolio.mirrors` array and any pending entry order with a non-zero mirror ID. This deliberately blocks accounts with copy-trading activity until positions and pending exposure can be reconciled without double counting.
5. **Pending orders and cash:** the snapshot normalizer now rejects *any* non-empty `ordersForOpen` list, including manual entries (`mirrorId = 0`), because their stop risk is not represented in `positions` and cannot yet be quantified reliably. It also rejects mirrored pending entries. Do not apply the same blanket rule to `orders`: the official example includes order records linked to open positions by `orderId`, so these are not automatically additional pending exposure. The standalone available-cash calculation remains available and applies eToro's documented formula: subtract manual `ordersForOpen` amounts and all `orders` amounts from `credit`. Reconcile actual response field casing because the guide example and schema disagree (`credits` vs `credit`, `mirrorID` vs `mirrorId`). Do not count closing orders as new exposure. Treat missing or unknown order arrays as an incomplete snapshot.
6. **Quotes:** only use a fresh `realtime` bid/ask for actionable spread/execution checks. A delayed quote or stale timestamp must block the relevant validation.
7. **What-if costs:** request a hypothetical cost estimate only after instrument, settlement type, direction, leverage and amount are known. Require a complete response with all applicable cost components; absent or ambiguous cost data keeps the candidate blocked.
8. **Read-only boundary:** the integration must contain only GET account/market requests and the documented what-if cost POST. It must not call trading execution, order creation, close, modify-stop, cancel, transfer, or any write endpoint.

## Implementation status (2026-11-21)

- The theoretical position-sizing result is now connected to the candidate validation gate through the independent portfolio admission check. The complete reserved risk (stop risk plus fixed costs) is checked against the hard 1% per-trade and 3% portfolio limits. This remains simulation/manual-review only.
- The current scan still supplies an unconfirmed portfolio state and no candidate sizing result, so candidates remain blocked. `ALLOW_POSITION_SIZING` and `STRATEGY_VALIDATED` remain false; this is intentional until strategy robustness, broker costs and position-risk semantics are verified.
- A pending manual entry order now blocks account-snapshot normalization. Open-position risk is still not derived from `amount`, `units` or `leverage`; no new trade may be admitted while any existing position lacks a trustworthy USD stop-risk value.

## Manual audit workflow

A manually triggered GitHub Actions workflow is being added for the first authenticated validation. It uses only `ETORO_API_KEY` and `ETORO_USER_KEY` repository secrets, performs read-only account/schema, instrument-discovery and realtime-quote checks, and prints only pass/fail flags plus public instrument IDs. It does not print balances, positions, order details, raw responses or credentials. It has no schedule and does not activate trading.

## Proposed integration sequence

1. Add a read-only API client with environment-variable credentials, request IDs, strict timeouts, bounded retries, redacted errors, and a strict endpoint allowlist. (Implemented, dormant.)
2. Add schema validation and fixtures for the official nested account response, exact instrument search, and fresh realtime quotes. (Implemented, dormant; CI tested.)
3. Run the manual redacted audit after the user adds read-only API credentials as GitHub repository secrets. The workflow is manual-only and emits no private account values; it checks account schema, instrument candidates, account-specific eligibility and realtime quotes.
4. Compare the live account snapshot and calculated available cash with the eToro UI. Resolve any remaining response-shape differences before using account data.
5. Resolve all 12 instrument IDs and supported settlement types. Establish position risk only for combinations with verified unit/multiplier/FX semantics; block everything else.
6. Validate what-if cost results against the eToro order ticket for representative REAL and CFD instruments and for different holding periods.
7. Run in shadow/read-only mode for multiple sessions. Compare balances, positions, cash, quotes and costs manually. Only then consider removing the corresponding validation blockers.

## Not yet verified

- Whether this specific account is eligible to generate the required API key and receive the necessary read scopes.
- The exact eToro instrument IDs and supported settlement types for all 12 assets currently enabled in the bot, plus their live bid/ask availability.
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

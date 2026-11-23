# eToro read-only market evidence — 2026-11-23

## Scope and safety

- Source: authenticated eToro Public API market-only collector.
- Capture time: 2026-11-23 14:31:42 UTC (quotes/costs were refreshed sequentially through 14:31:54 UTC).
- Hypothetical order amount: USD 100 per instrument; the collector raises this only if the account-specific minimum is higher.
- Account portfolio, cash balance, holdings, positions, PnL, orders and trade history were not accessed.
- No order was submitted, modified or closed.
- The evidence is a current snapshot, not historical execution evidence.

Workflow: [eToro Read-Only Market Evidence run 45195086845](https://github.com/andertrading/Trading-bot/actions/runs/45195086845)

## Capture result

| Metric | Result |
|---|---:|
| Configured instruments | 12 |
| Exact instruments resolved | 12 |
| Instruments with validated REAL, LONG, 1x eligibility | 12 |
| Realtime quotes | 12 |
| Hypothetical opening-cost estimates | 12 |
| Capture errors | 0 |
| Registry-approved assets | 0/12 |
| Net-performance validation | NOT VALIDATED |

The 12/12 account-specific eligibility result confirms that the API returned a REAL, LONG, 1x configuration for each resolved instrument at capture time. It does not by itself establish the complete economic evidence required by `asset_economic_registry.json`.

## Per-instrument snapshot

All spread values below are from the quote snapshot. `marketSpread` is the separate component returned by the hypothetical opening-cost endpoint. The quote-reference column is the full quoted bid/ask spread multiplied by the USD 100 sample notional; it is diagnostic only and is not asserted to be the broker's exact execution-cost calculation.

| Asset | eToro symbol | Quote spread (bps) | Quote full-spread reference (USD) | API marketSpread (USD) | Transaction fee (USD) | Total API opening cost (USD) | Cross-check |
|---|---|---:|---:|---:|---:|---:|---|
| CSPX | CSPX.L | 7.295 | 0.0729 | 0.07 | — | 0.07 | Within $0.01 |
| CNDX | CNDX.L | 44.110 | 0.4411 | 0.08 | — | 0.08 | Divergence |
| TSLA | TSLA | 7.137 | 0.0714 | 0.07 | 1.00 | 1.07 | Within $0.01 |
| NVDA | NVDA | 4.297 | 0.0430 | 0.04 | 1.00 | 1.04 | Within $0.01 |
| AAPL | AAPL | 5.741 | 0.0574 | 0.06 | 1.00 | 1.06 | Within $0.01 |
| MSFT | MSFT | 5.737 | 0.0574 | 0.06 | 1.00 | 1.06 | Within $0.01 |
| TTWO | TTWO | 65.162 | 0.6516 | 0.64 | 1.00 | 1.64 | Within $0.01 |
| BTC | BTC | 0.174 | 0.0017 | Omitted | 0.99 | 0.99 | API component omitted |
| ETH | ETH | 2.194 | 0.0219 | 0.02 | 0.99 | 1.01 | Within $0.01 |
| SOL | SOL | 4.939 | 0.0494 | 0.04 | 0.99 | 1.03 | Within $0.01 |
| XLM | XLM | 0.731 | 0.0073 | 0.09 | 0.99 | 1.08 | Divergence |
| XRP | XRP | 6.247 | 0.0625 | 0.06 | 0.99 | 1.05 | Within $0.01 |

The cross-check deliberately uses a one-cent absolute tolerance because the API reports currency amounts to cents. A mismatch is a diagnostic, not a capture failure: the two quote/cost fields may use different source precision, price construction or calculation rules. Missing `marketSpread` is recorded as omitted, never interpreted as zero.

### Follow-up required for the discrepancies

- **CNDX:** quote-derived full-spread reference USD 0.4411 versus API `marketSpread` USD 0.08.
- **XLM:** quote-derived full-spread reference USD 0.0073 versus API `marketSpread` USD 0.09.
- **BTC:** positive quoted spread (0.174 bps), but the cost response omitted `marketSpread`.

Do not average, overwrite or select one figure silently. Retain both raw observations and keep historical net validation blocked until quote precision and endpoint semantics are reconciled.

## Fee schedule context

The current Spanish eToro public fee page states:

- REAL stock commission may be USD 1 or USD 2 on both opening and closing, depending on residence and exchange.
- ETFs have no platform transaction commission.
- REAL crypto fees are tiered by Club level and monthly crypto volume; Bronze/Silver/Gold are listed at 1%, with lower tiers for Platinum and above.
- Natural market bid/ask spread varies and is distinct from eToro's own spread fee.

Source: [eToro Spain — fees](https://www.etoro.com/es/trading/fees/).

The capture's USD 1 stock and USD 0.99 crypto `transactionFee` values are opening estimates for the USD 100 sample only. They do not prove the close-side fee or a historical fee schedule. Keep the bot's conservative public-fee scenario assumptions unchanged until instrument/account-specific round-trip evidence is available.

## Why close-side costs were not queried

The eToro `POST /api/v2/trading/info/costs` reference documents both `action=open` and `action=close`; a close request requires non-empty `positionIds`. This collector intentionally validates and permits only `action=open`. It does not retrieve real position IDs or call portfolio/position endpoints, in accordance with the project's account-data boundary.

Endpoint reference: [eToro API costs endpoint reference](https://skmtc.dev/etoro/apis/etoro-api/docs/api/v2/trading/info/costs/post).

Therefore, no round-trip cost is confirmed by this capture.

## Production and research decision

- Do not populate the economic registry from this one-time quote snapshot.
- Do not treat a current spread snapshot as historical spread evidence.
- Do not infer a missing cost component to be zero.
- Do not enable production signals, position sizing, alerts or live trading.
- Preserve the daily long-only, unleveraged REAL candidate and the separate M15 research-only lane.
- The registry remains 0/12 ELIGIBLE; net performance remains NOT VALIDATED.

## Automated diagnostics added with this evidence review

The collector now writes `market_spread_crosscheck` per asset when a hypothetical opening-cost estimate is present. It records the quote-derived full-spread reference, API `marketSpread`, their difference and one of:

- `WITHIN_ONE_CENT`
- `DIVERGENCE`
- `API_COMPONENT_OMITTED`

The summary includes counts for divergences and omitted API components. These diagnostics do not turn a mismatch into an API error or change the capture's COMPLETE/PARTIAL status; they make the discrepancy visible while preserving fail-closed net-performance gates.

# eToro spread reconciliation — 2026-11-23

## Decision

Research evidence only. The 2026-11-23 eToro market-only capture is complete as a data collection run, but it does **not** validate historical or round-trip costs. Keep the economic registry at 0/12 eligible and keep net-performance validation blocked.

The read-only collector's additive diagnostic is implemented in PR #151 (merged to `main`). It preserves both observations; it does not select, average, replace, or infer a missing component as zero.

## Capture provenance and scope

- Workflow: [eToro Read-Only Market Evidence, run 45195086845](https://github.com/andertrading/Trading-bot/actions/runs/45195086845)
- Capture began: 2026-11-23 14:31:42 UTC; sequential quote/cost refreshes completed by 14:31:54 UTC.
- Sample notional: USD 100 per instrument, subject to the broker-reported minimum.
- Configured/resolved instruments: 12/12.
- Fresh real-time quotes: 12/12.
- Account-specific REAL, LONG, 1x eligibility returned: 12/12.
- Hypothetical opening-cost estimates: 12/12.
- Capture errors: 0.
- Account portfolio, cash, holdings, positions, PnL, orders and trade history were not accessed.
- No order action was performed and no external market-data credits were consumed.

The 12/12 eligibility result is a snapshot of instrument configuration returned by eToro. It is not evidence that any instrument has complete historical economics or is approved by the bot's economic registry.

## Spread cross-check

The diagnostic computes a quote-derived reference:

`sample_notional_usd × quote_spread_bps / 10,000`

This is the full quoted bid/ask spread expressed against the sample notional. It is a diagnostic reference only, not a confirmed broker execution-cost calculation. The API `marketSpread` field is retained separately. A one-cent absolute tolerance is used because the API cost components are expressed in cents.

| Asset | Quote spread (bps) | Quote full-spread reference (USD) | API marketSpread (USD) | Difference: API − reference (USD) | Diagnostic |
|---|---:|---:|---:|---:|---|
| CSPX | 7.295 | 0.0729 | 0.07 | -0.0029 | Within $0.01 |
| CNDX | 44.110 | 0.4411 | 0.08 | -0.3611 | Divergence |
| TSLA | 7.137 | 0.0714 | 0.07 | -0.0014 | Within $0.01 |
| NVDA | 4.297 | 0.0430 | 0.04 | -0.0030 | Within $0.01 |
| AAPL | 5.741 | 0.0574 | 0.06 | +0.0026 | Within $0.01 |
| MSFT | 5.737 | 0.0574 | 0.06 | +0.0026 | Within $0.01 |
| TTWO | 65.162 | 0.6516 | 0.64 | -0.0116 | Divergence |
| BTC | 0.174 | 0.0017 | Omitted | — | API component omitted |
| ETH | 2.194 | 0.0219 | 0.02 | -0.0019 | Within $0.01 |
| SOL | 4.939 | 0.0494 | 0.04 | -0.0094 | Within $0.01 |
| XLM | 0.731 | 0.0073 | 0.09 | +0.0827 | Divergence |
| XRP | 6.247 | 0.0625 | 0.06 | -0.0025 | Within $0.01 |

Observed diagnostics: 3 divergences (CNDX, TTWO, XLM) and 1 omitted API component (BTC).

These differences are unresolved semantic/source discrepancies, not proof that either value is the true executable cost. Do not add the two values together: that could double-count the same spread. Do not replace one with the other. Preserve raw quote and cost payloads in the run artifact for future comparisons.

## Opening commissions are not round-trip evidence

The capture's hypothetical opening-cost responses reported these transaction-fee components for the USD 100 sample:

- US stocks: USD 1.00 per opening estimate.
- Crypto: USD 0.99 per opening estimate.
- ETFs: no transaction-fee component in this sample.

These are opening-only, account-specific API estimates. They do not confirm closing fees, future fee tiers, historical fee schedules, or all-in round-trip costs. The project's conservative published-fee scenario assumptions must remain distinct from this one-time capture.

## Why no close-side query was made

The documented close-cost request requires position identifiers. Obtaining real position IDs would cross the project's explicit boundary against reading personal positions/portfolio data. The collector therefore remains restricted to hypothetical opening costs and market-only quote/eligibility evidence. No personal account position identifiers are to be requested or used to complete this research.

## Required evidence before economic approval

For each instrument, the following remain independently required:

1. Confirmed instrument identity/listing and REAL, LONG, 1x execution configuration.
2. Entry **and** exit commission/fee rules applicable to the account, with effective dates and source.
3. A documented spread treatment that prevents double counting between historical prices and modeled execution prices.
4. Time-aligned historical bid/ask evidence, or a conservative spread model validated prospectively over a representative observation window. A single snapshot cannot be projected backwards.
5. Verified quote-to-account FX rate and conversion fee where currencies differ.
6. Confirmed minimum order, fractional-unit rules and execution constraints.
7. Cost-aware portfolio replay using actual allocated paper quantities and all-in stop risk, without changing the 1% per-trade or 3% aggregate risk caps.

Until those requirements are met, do not populate the registry from this snapshot, do not claim net profitability, and do not enable production signals, sizing, alerts or live trading.

## Implementation reference

PR #151 adds per-asset `market_spread_crosscheck` diagnostics and summary counts to the read-only collector. It is intentionally diagnostic-only: it does not change COMPLETE/PARTIAL capture status, strategy signals, position sizing, registry eligibility, or production gates.

- [PR #151 — read-only eToro spread cross-check diagnostics](https://github.com/andertrading/Trading-bot/pull/151)
- [eToro fee information](https://www.etoro.com/es/trading/fees/)

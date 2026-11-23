# eToro public manual-crypto fee sensitivity on Coinbase OOS trades — 2026-11-23

## Purpose and source

A research-only fee sensitivity using the retained Coinbase OOS artifact (workflow run `45244766224`, artifact ID `14852244870`, SHA-256 `9e1d33001b7359e13e9540bd88a23057c394290f1eaa75134d788c5de406ffb6`). It includes only 1,252 OOS test candidate trades across five crypto assets and 21 test folds.

The fee schedule is taken from eToro's public [Spain fee page](https://www.etoro.com/es/trading/fees/). eToro says manual crypto fees vary by Club tier and monthly crypto trading-volume bucket, and are charged on opening and closing positions. This is a published schedule, not confirmation of the user's tier or the actual fee applied to any account.

## Method

For each trade:

`net_R = gross_result_R - (entry_price × quantity + exit_price × quantity) × fee_rate / (initial_risk_per_unit × quantity)`

The same per-side rate is applied to opening and closing notional. It uses the hypothetical quantity and entry/exit prices recorded in the historical research artifact. It does not use account holdings, Club status, trading volume, or any live account endpoint.

## Key finding

At the published 1.00% per-side rate (Bronze/Silver/Gold at all listed monthly-volume buckets), the 1,252 candidate trades produce:

| Metric | Result |
|---|---:|
| Gross total | +4.87R |
| Fee-only scenario total | −5,016.72R |
| Fee-only scenario mean | −4.007R/trade |
| Fee-only scenario win rate | 2.0% |
| Fee-only scenario profit factor | 0.0014 |

Even the lowest published rate in the current schedule (0.10% per side, available only for the highest volume bucket in the Platinum, Platinum+ and Diamond tiers) gives −497.29R, −0.397R/trade and a 0.5519 profit factor in this simplified fee-only calculation.

This is an exceptionally large cost burden relative to the small stop-risk budget used in the candidate sizing. It is a strong warning that the current crypto setup may be economically incompatible with eToro's manual-crypto fee schedule at these position sizes. It is **not** a verified account-specific net backtest.

## Published fee-rate sensitivity

The public schedule's per-side rates are:

| Club tier | $0–10k | $10k–100k | $100k–250k | $250k–1m | $1m–10m | $10m+ |
|---|---:|---:|---:|---:|---:|---:|
| Bronze / Silver / Gold | 1.00% | 1.00% | 1.00% | 1.00% | 1.00% | 1.00% |
| Platinum | 0.95% | 0.90% | 0.85% | 0.80% | 0.70% | 0.10% |
| Platinum+ | 0.90% | 0.85% | 0.80% | 0.75% | 0.50% | 0.10% |
| Diamond | 0.85% | 0.80% | 0.75% | 0.70% | 0.30% | 0.10% |

The monthly-volume bucket and applicable Club tier are not established. No personal account data was accessed.

## Limitations

- Coinbase prices are not established as eToro executable bid/ask prices.
- This is fee-only; it excludes actual market spread, slippage, historical execution basis, and any other unpriced costs.
- It assumes the candidate positions would be classified as manual real crypto, not CFDs.
- It does not replay portfolio cash, risk reservations, concurrency, rejected candidates, or replacement trades.
- Source trades remain `costs_complete=false`; no verified net performance is claimed.

## Decision

Do not approve the current crypto strategy for eToro on this evidence. Do not alter asset universe or sizing based on this ex-post sensitivity. The result warrants further research into whether any of the bot's configured instruments can be traded with economically viable costs and whether the entry/stop geometry is compatible with the venue. No production, automation, alert, account, or execution setting was changed.

## Reproduction

`python research/oos_etoro_crypto_fee_sensitivity.py --input-dir /path/to/extracted/artifact --output fee_sensitivity.json`

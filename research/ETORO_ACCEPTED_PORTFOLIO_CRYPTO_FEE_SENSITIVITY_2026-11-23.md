# Accepted Coinbase OOS portfolio: eToro manual-crypto fee sensitivity — 2026-11-23

## Source and portfolio simulation

This audit uses the retained output of the portfolio allocator, not an unweighted sum of candidate trades:

- Workflow run: [45257891167](https://github.com/andertrading/Trading-bot/actions/runs/45257891167)
- Artifact ID: `14859052638`
- SHA-256: `a4886b52920572f67e9740c34db81b493a2d5761971f973645793d0edec36703`
- 5 crypto assets, 10 series, 21 OOS test folds.
- 1,252 candidate trades; 921 accepted and 331 rejected by the shared risk/cash allocator.
- Paper capital: $1,000; max risk 1% per trade; max aggregate open risk 3%.
- Gross result: +$55.36 (+5.54%); gross PF 1.0347.
- 0/921 accepted trades have complete verified transaction costs. Net P&L is therefore unavailable, not zero.

## Fee sensitivity on accepted allocations

Using each accepted trade's actual hypothetical quantity, entry, exit price and realized gross P&L, the published manual-crypto fee is applied to both sides:

`fee_usd = quantity × (entry + exit_price) × per_side_fee_pct`

| Published fee per side | Fees on 921 accepted trades | Gross P&L | Fee-only adjusted P&L |
|---:|---:|---:|---:|
| 1.00% | $9,096.02 | +$55.36 | −$9,040.66 |
| 0.70% | $6,367.21 | +$55.36 | −$6,311.86 |
| 0.50% | $4,548.01 | +$55.36 | −$4,492.65 |
| 0.30% | $2,728.81 | +$55.36 | −$2,673.45 |
| 0.10% | $909.60 | +$55.36 | −$854.25 |

Even at 0.10% per side, the fee-only cost is approximately 16.4 times the entire gross portfolio profit. At 1.00%, it is approximately 164.3 times gross profit.

## Breakdown at 1.00% per side

| Asset | Accepted trades | Gross P&L | Fee-only cost | Adjusted P&L |
|---|---:|---:|---:|---:|
| BTC | 240 | +$71.78 | $2,850.44 | −$2,778.66 |
| ETH | 185 | +$104.11 | $1,786.47 | −$1,682.36 |
| SOL | 180 | +$14.26 | $1,636.47 | −$1,622.21 |
| XLM | 131 | −$77.75 | $1,096.20 | −$1,173.95 |
| XRP | 185 | −$57.04 | $1,726.44 | −$1,783.48 |

## Interpretation and strict limitations

This is much more representative than an equal-weight candidate-trade calculation because it uses the 921 trades actually accepted by the $1,000 shared-cash allocator. However, it remains an **ex-post fee-only sensitivity**, not a portfolio replay with costs:
- The allocator is not rerun after fee deductions reduce equity, so the accepted trade set and quantities are held fixed.
- The Club tier and monthly crypto trading-volume bucket are unknown; no personal account data was accessed.
- The public schedule is not confirmation of the user's applicable fee or each instrument's actual execution classification.
- Coinbase OHLC is not eToro executable bid/ask evidence.
- Market spread, slippage, and other unpriced charges are excluded.
- The source simulation itself excludes intratrade drawdown and unrealized P&L.

The published schedule is from eToro's [Spain fees page](https://www.etoro.com/es/trading/fees/). Manual-crypto fees vary by Club tier and monthly volume and apply on opening and closing positions.

## Decision

The current crypto strategy is **not economically approved for eToro**. The gross allocator result (+$55.36) is too small to withstand even the lowest published manual-crypto fee scenario on these position sizes. Do not change sizing, asset universe, risk rules, or production behavior based on this ex-post test. The correct next step is to investigate venue/instrument economics and redesign the research only after a pre-registered cost model and executable-price evidence exist.

No live account, holdings, positions, orders, balances, paid data credits, automation, alerts, or production settings were accessed or changed.

## Reproduction

`python research/oos_portfolio_etoro_crypto_fee_sensitivity.py --input /path/to/coinbase_oos_portfolio_result.json --output portfolio_fee_sensitivity.json`

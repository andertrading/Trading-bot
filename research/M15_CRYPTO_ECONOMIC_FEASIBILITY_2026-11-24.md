# M15 crypto economic feasibility audit — 2026-11-24

## Executive decision

**Do not treat the current M15 crypto strategy as an eToro candidate. Keep it research-only.** The retained OOS sample has essentially zero gross edge, while the published 1% per-side crypto fee scenario is several R per trade before spread. No threshold or sizing adjustment can make this exact trade set economically viable under that fee assumption.

This is a feasibility audit, not a strategy optimization. It does not change production code, thresholds, asset universe, risk, sizing, routing, workflows, alerts or execution.

## Data and reproducibility

- Input: ten JSON series from Coinbase-only OOS run `45495049495`, artifact `14966689253`, ZIP SHA-256 `4f82e9d5d12dc7a5db43fd473b398af654189c04533593f1999056d2560cdae8`.
- M15 sample: 1,609 validation trades and 1,247 test trades, 2,856 total. The remaining five trades are daily and are not pooled into this M15 analysis.
- The artifact reports Coinbase-only data, zero network requests, no production changes and null verified cost-adjusted R.
- Gross trade results and the 1%-per-side scenario fields were read directly from each retained trade record. Commission burden in R is `commission_cost_usd / hypothetical_risk_budget_usd`.
- The hypothetical trade sizing is per-trade research sizing, not a shared-capital portfolio simulation. Aggregate dollar totals across all trades are therefore not presented as a portfolio P&L.

## Findings

| M15 OOS fold | Trades | Gross mean R/trade | Gross profit factor | Mean commission burden | Mean after assumed commission |
|---|---:|---:|---:|---:|---:|
| Validation | 1,609 | -0.00535R | 0.9917 | 3.76R/trade | -3.77R/trade |
| Test | 1,247 | +0.00550R | 1.0088 | 4.03R/trade | -4.02R/trade |

The test gross mean is almost zero even before fees. Under the assumed 1% per-side crypto fee, only 1.9% of test trades remain positive after commission; the scenario still excludes market spread, slippage and any execution-price mismatch.

### Commission burden by asset

| Asset | Validation mean gross R | Validation commission R | Test mean gross R | Test commission R |
|---|---:|---:|---:|---:|
| BTC | -0.0436 | 6.42R | +0.0902 | 5.68R |
| ETH | -0.0699 | 3.75R | +0.1236 | 3.68R |
| SOL | +0.0183 | 2.89R | -0.0124 | 3.34R |
| XLM | +0.0124 | 2.53R | -0.1436 | 3.13R |
| XRP | +0.0542 | 2.95R | -0.0787 | 3.80R |

Asset leadership reverses between validation and test. This rules out choosing BTC/ETH as production winners from this sample. Even their positive test gross means are far below the modeled commission burden.

### Break-even fee sensitivity

With the observed test trade set and its hypothetical quantities, the per-side commission rate would need to be approximately **0.00137%** to reduce mean commission to the tiny +0.00550R gross test expectancy, assuming zero spread and zero slippage. That is roughly 730 times lower than a 1% per-side scenario. This is a mathematical break-even sensitivity, not an estimate of the user's actual fee tier.

Validation is already negative before fees, so no non-negative commission rate makes the validation fold profitable.

## What this means for the bot

1. **M15 crypto:** retain for research and feature diagnostics only. Do not promote it to eToro signals under the current published base-fee scenario.
2. **Do not try to repair this with position sizing.** If both commission and risk scale with notional, changing quantity does not improve commission measured in R. Increasing risk would violate the fixed risk limits and would not create an edge.
3. **Test a different economic structure, not a lower score threshold:** longer holding periods and/or instruments with materially lower transaction costs are the rational research directions. Any new strategy must be preregistered and validated on future untouched data.
4. **Daily crypto:** currently too few trades in the retained 2-year-per-asset OOS window (five test trades total and none in validation) to approve or reject it.
5. **ETF/stock daily MVP:** remains a separate research lane. ETF zero published commission does not mean zero total cost; the current spread sample, historical execution basis and full round-trip costs remain unverified. No asset is currently economically eligible in the registry.

## Hard limitations

- The 1% per-side crypto commission is a dated published-fee scenario for Bronze/Silver/Gold, not confirmed for this account. The actual Club tier is unknown.
- Coinbase spot OHLC is not eToro executable bid/ask. No verified spread, slippage or round-trip execution basis is included.
- The OOS test period has already been examined and cannot be reused to select parameters.
- The 2,856 per-trade hypothetical sizing records are not a portfolio equity curve and must not be summed as one.
- No account endpoints, balances, holdings, positions, PnL, orders or trade history were accessed.

## Conclusion

The immediate solution is to stop treating M15 crypto as a near-production candidate and redirect research effort to a lower-cost daily instrument lane, while keeping all current assets and strategy code unchanged. Before any live/alert eligibility, the ETF/stock lane still needs verified round-trip costs and execution-price evidence, plus enough forward trades to establish net expectancy.

Reproduction: local offline aggregation of the retained OOS JSON artifact; zero provider/API requests.
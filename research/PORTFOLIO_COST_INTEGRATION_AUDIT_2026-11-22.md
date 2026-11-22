# Portfolio cost integration audit — 2026-11-22

## Status

Research/design only. No strategy, sizing, execution, provider, or live-trading behavior is changed by this document.

The production candidate remains the daily, long-only, unleveraged REAL-instrument path. The economic registry currently approves **0 of 12 assets**. Missing evidence must continue to block net-performance validation; it must never be replaced with zero-cost assumptions.

## Finding

The repository has two separate cost paths:

1. `backtest_v3._estimate_backtest_cost_fields()` estimates transaction costs for an illustrative position based on `INITIAL_CAPITAL_USD` and the score's risk tier. Its `hypothetical_quantity` is calculated before portfolio allocation. The resulting `verified_result_R` is therefore a per-trade diagnostic for that hypothetical size, not the realized portfolio position.
2. `portfolio_backtest.simulate_portfolio()` allocates the actual portfolio quantity from shared equity, simultaneous-entry risk limits, and cash availability. It then realizes `allocated_risk_usd * result_R`. It does not call either cost estimator and correctly reports `costs_included=False`.

Consequences:

- Portfolio P&L, win rate, profit factor, and drawdown are gross of transaction costs.
- The portfolio CSV's legacy summary field `net_pnl_usd` is calculated from gross trade P&L. The console labels it as gross, but the field name can be misread as net-of-costs. Do not interpret it as net performance.
- Per-trade `verified_net_pnl_usd` and `verified_result_R` do not use the portfolio's allocated quantity. They must not be summed to create portfolio net results.
- Portfolio risk limits currently constrain gross stop-loss exposure. Once costs are included, commissions/spread/FX can make the all-in loss at a stop exceed the nominal 1% per-trade or 3% aggregate limits.

## Public eToro fee schedule spot-check — 2026-11-22

The current Spanish public fee page was checked against the scenario assumptions in `transaction_costs.py`:

- REAL stocks: eToro currently describes a $1 or $2 opening/closing commission depending on residence and exchange. The code's $2 per side is the conservative upper end, not a confirmed fee for every configured stock.
- REAL ETFs: eToro states no platform transaction commission. Market spread still exists.
- REAL crypto: the public schedule is tiered by Club level and monthly volume; Bronze/Silver/Gold show 1%, with reduced rates for higher tiers. The code's 1% per side is a conservative base-tier assumption, not an account-specific quote.
- CFDs: the public page lists 0.15% for stocks/ETFs and 1% for crypto; the current scenario model matches these percentage assumptions. The page also states that new Spanish users have been unable to trade CFDs since 2 August 2024, so CFD paths remain outside this REAL-only production candidate.
- Market spread is the natural bid/ask difference, not an additional eToro spread commission. It varies with market conditions. Currency-conversion fees vary by location, payment method and Club level.

Sources checked:
- https://www.etoro.com/es/trading/fees/
- https://www.etoro.com/es/trading/fees/cfd-spreads/
- https://www.etoro.com/es/trading/fees/cfd-overnight-fees/

This spot-check supports retaining the published-fee values as **scenario assumptions only**. It does not confirm the user's applicable stock fee, Club tier, actual instrument execution type, market spread, or historical OHLC-to-executable-price mapping. The order ticket's estimated-cost display remains the instrument-level evidence source.

## Effect of fixed stock fees at the $1,000 research reference

Using only the bot's configured research capital and risk tiers (not any personal account data):

| Tier | Planned risk budget | $2 round-trip stock fee | $4 round-trip stock fee |
|---|---:|---:|---:|
| A (0.75%) | $7.50 | 26.7% of budget | 53.3% of budget |
| B (0.50%) | $5.00 | 40.0% of budget | 80.0% of budget |
| C (0.25%) | $2.50 | 80.0% of budget | 160.0% of budget |

The $2 and $4 scenarios correspond to the public $1-per-side and $2-per-side stock commission cases. These ratios exclude market spread and any other execution friction.

Implications: with the conservative $2-per-side assumption, a C-tier stock trade cannot fit inside its $2.50 all-in stop-risk budget even before variable costs. A B-tier trade has only $1 left for price risk and all other costs. This is a meaningful small-capital friction for fixed-fee stocks; it is not evidence that the strategy is profitable or unprofitable, and it is not a reason to increase risk or change brokers without validated evidence. ETF and crypto cost structures differ and must be evaluated separately.

### Crypto fee sensitivity at different stop distances

For a long crypto position with a 1% fee on entry and 1% on exit, assuming the exit occurs exactly at the stop and ignoring market spread/slippage, the round-trip fee alone is approximately:

`fee_R = 0.01 * (2 - stop_distance_pct) / stop_distance_pct`

| Stop distance from entry | Approx. round-trip fee |
|---|---:|
| 2% | 0.99R |
| 3% | 0.66R |
| 5% | 0.39R |
| 8% | 0.24R |
| 10% | 0.19R |

This is a sensitivity calculation, not a measured strategy result. It demonstrates why the crypto fee model can consume a large share of the stop-risk budget when stops are tight. Actual spreads, slippage, execution type, and the trade's realized exit price can make the final result different.

## Required integration design

### 1. One source of truth

Use `estimate_verified_registry_trade_costs()` for admission-grade calculations only. Do not use the public-fee scenario estimator to certify net performance. Do not mix scenario costs with registry-verified costs in one metric.

The verified estimator must be called using the **actual portfolio quantity** and the relevant prices. The precomputed hypothetical cost columns remain diagnostics only.

### 2. Spread treatment: prevent double counting

A second integration hazard exists in the current verified estimator: it always adds a separate half-spread on entry and half-spread on exit. The registry's `price_basis` currently stores free-text descriptions, but does not expose a machine-readable flag stating whether the supplied entry/exit prices already include bid/ask spread.

This matters because:

- If historical OHLC prices are trade/last or mid-like reference prices, the modeled half-spread on each side may be needed to approximate executable prices.
- If the backtest already uses executable ask for entry and bid for exit, the spread is embedded in those prices. Subtracting a separate spread charge again double-counts it.
- A non-empty `execution_basis` or `mapping_rule` string does not prove which of these treatments is in force. The current synthetic test fixture describes broker bid/ask execution while the estimator also adds a spread cost; it verifies arithmetic, not the economic non-duplication of spread.

Before net integration, add an explicit validated registry field such as `spread_treatment` with closed values:

- `SEPARATE_FROM_PRICES`: prices exclude the market spread; deduct measured half-spread on entry and exit.
- `EMBEDDED_IN_EXECUTION_PRICES`: prices already represent the relevant executable bid/ask; do not deduct a second spread charge.
- Missing/unknown: costs remain incomplete and net validation is blocked.

The choice must be supported by instrument-specific evidence and tests. Do not infer it from vendor names or from free-text descriptions.

### Existing sizing logic to reuse carefully

`risk_manager.calculate_position_size()` already accepts variable costs per unit and fixed round-trip costs. Its core equation is equivalent to:

`quantity = (risk_budget - fixed_costs) / (entry - stop + variable_costs_per_unit)`

That is useful groundwork, but it is **not** currently the portfolio allocator:

- `portfolio_backtest.simulate_portfolio()` does not call it and currently sizes against gross stop distance only.
- `calculate_position_size()` returns BLOCKED while `ALLOW_POSITION_SIZING=False`. That safety gate must not be enabled to make a research backtest work.
- The helper does not itself solve simultaneous pro-rata allocation, available-cash reservation including entry-side costs, or instrument minimum/fractional quantity rules.

The implementation should extract or adapt the pure arithmetic behind this helper for the research simulator, preserving the production sizing gate as disabled. It must not simply toggle `ALLOW_POSITION_SIZING` globally or bypass the separate economic eligibility gate.

### 3. Cost-aware risk sizing

Sizing must reserve the full modeled loss if the stop is reached, including round-trip costs. For a long position, define:

- Gross stop loss per unit: (E-S), where (E) is entry and (S) is stop.
- All-in modeled loss at stop for quantity (q): (q(E-S)+C(q; E,S)), where (C) is the verified round-trip cost calculated using entry (E), stop exit (S), and quantity (q).

The allocated risk budget must satisfy:

`q(E-S) + C(q; E,S) <= allocated_risk_budget`

Because fixed per-side commissions are not proportional to quantity, do not approximate every cost as a constant percentage. Solve the quantity against the verified cost function (or derive and test its fixed and variable components) and reject a position if no positive feasible quantity exists.

Cash reservation must also include entry-side transaction costs, not only entry notional. Do not reserve the full round-trip fee as cash if the exit-side fee is only payable later; account for the timing explicitly.

### 4. Realized net P&L exactly once

At exit, calculate verified round-trip costs once using the allocated quantity and actual simulated exit price:

`gross_pnl = q * (exit_price - entry_price)`

`net_pnl = gross_pnl - verified_round_trip_cost`

Then use net P&L for equity, subsequent risk budgets, return, win rate, profit factor, and realized drawdown. Preserve gross P&L and cost components as separate audit columns.

Never subtract `result_R_after_known_fees` or any hypothetical-size cost field from portfolio P&L. That would mix position sizes and can double-count costs.

### 5. Fail-closed net reporting

A portfolio net metric is valid only when every accepted and closed trade has complete verified costs and verified execution-price basis. If one trade is incomplete:

- keep gross portfolio metrics available and clearly labeled;
- set portfolio net metrics to null/unavailable;
- report the count of incomplete trades and their missing cost evidence;
- do not silently treat missing costs as zero or publish a partial-cost sum as net.

The current registry state (0/12 eligible) means this gate must remain closed.

### 6. Stop-gap risk is not a hard maximum

Even a correct all-in loss calculation at the stop price does not guarantee a hard 1% maximum loss. The backtest explicitly models a gap through the stop by exiting at the next candle's open, which can be materially worse than the stop. Daily swing positions are exposed to overnight/weekend gaps, halts, and liquidity shocks.

Therefore:

- The 1% and 3% limits can constrain planned stop-risk at entry, including modeled costs; they cannot honestly be described as guaranteed maximum realized losses.
- Report stop-risk budget separately from realized loss and gap slippage.
- Add gap-stress scenarios and report the frequency/severity of losses beyond the planned budget.
- If a guaranteed maximum loss is a non-negotiable requirement, the strategy/instrument must support a genuinely guaranteed stop mechanism with its own verified availability and fee model. Ordinary stop orders do not provide that guarantee.

### 7. Risk-limit semantics

The hard 1% per-trade and 3% aggregate limits must apply to modeled all-in stop loss, not only price movement to the stop. Add tests for simultaneous entries, fixed commissions, variable spread/FX costs, and costs that make a candidate infeasible under the risk budget.

The simulator remains a bar-based approximation: spread measurement and historical-to-executable price parity must be verified independently. Cost subtraction cannot repair missing execution-price evidence or intrabar path uncertainty.

## Required test matrix before implementation can be accepted

- Actual portfolio quantity, not hypothetical capital sizing, drives cost calculations.
- Fixed fees are charged once per side and do not scale with quantity.
- Percentage fees, spread, and FX scale with the correct entry/exit notionals.
- Spread is deducted exactly once: separately for reference prices, or embedded in executable bid/ask prices, never both.
- Missing/unknown spread treatment blocks net reporting.
- Entry-side cash reservation and all-in stop-risk reservation are both respected.
- A verified cost record is applied exactly once to realized P&L.
- An incomplete registry record produces no net P&L, net R, or net portfolio return.
- One incomplete trade blocks portfolio-wide net validation while preserving gross diagnostics.
- Net win rate / profit factor correctly classify trades after fees.
- Same-timestamp entries/exits preserve the existing conservative ordering.
- Gross outputs remain available and explicitly labeled.
- No code path enables live order execution or reads the user's real account holdings.

## Decision

Do **not** integrate estimated costs into portfolio P&L yet. First preserve the existing gross-only simulator, obtain instrument-specific evidence for fees/spreads/FX/execution-price mapping, and then implement cost-aware sizing and net accounting as one tested change. Splitting net P&L from risk sizing would give a misleading risk cap even if the arithmetic looked correct.

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

## Required integration design

### 1. One source of truth

Use `estimate_verified_registry_trade_costs()` for admission-grade calculations only. Do not use the public-fee scenario estimator to certify net performance. Do not mix scenario costs with registry-verified costs in one metric.

The verified estimator must be called using the **actual portfolio quantity** and the relevant prices. The precomputed hypothetical cost columns remain diagnostics only.

### 2. Cost-aware risk sizing

Sizing must reserve the full modeled loss if the stop is reached, including round-trip costs. For a long position, define:

- Gross stop loss per unit: (E-S), where (E) is entry and (S) is stop.
- All-in modeled loss at stop for quantity (q): (q(E-S)+C(q; E,S)), where (C) is the verified round-trip cost calculated using entry (E), stop exit (S), and quantity (q).

The allocated risk budget must satisfy:

`q(E-S) + C(q; E,S) <= allocated_risk_budget`

Because fixed per-side commissions are not proportional to quantity, do not approximate every cost as a constant percentage. Solve the quantity against the verified cost function (or derive and test its fixed and variable components) and reject a position if no positive feasible quantity exists.

Cash reservation must also include entry-side transaction costs, not only entry notional. Do not reserve the full round-trip fee as cash if the exit-side fee is only payable later; account for the timing explicitly.

### 3. Realized net P&L exactly once

At exit, calculate verified round-trip costs once using the allocated quantity and actual simulated exit price:

`gross_pnl = q * (exit_price - entry_price)`

`net_pnl = gross_pnl - verified_round_trip_cost`

Then use net P&L for equity, subsequent risk budgets, return, win rate, profit factor, and realized drawdown. Preserve gross P&L and cost components as separate audit columns.

Never subtract `result_R_after_known_fees` or any hypothetical-size cost field from portfolio P&L. That would mix position sizes and can double-count costs.

### 4. Fail-closed net reporting

A portfolio net metric is valid only when every accepted and closed trade has complete verified costs and verified execution-price basis. If one trade is incomplete:

- keep gross portfolio metrics available and clearly labeled;
- set portfolio net metrics to null/unavailable;
- report the count of incomplete trades and their missing cost evidence;
- do not silently treat missing costs as zero or publish a partial-cost sum as net.

The current registry state (0/12 eligible) means this gate must remain closed.

### 5. Risk-limit semantics

The hard 1% per-trade and 3% aggregate limits must apply to modeled all-in stop loss, not only price movement to the stop. Add tests for simultaneous entries, fixed commissions, variable spread/FX costs, and costs that make a candidate infeasible under the risk budget.

The simulator remains a bar-based approximation: spread measurement and historical-to-executable price parity must be verified independently. Cost subtraction cannot repair missing execution-price evidence or intrabar path uncertainty.

## Required test matrix before implementation can be accepted

- Actual portfolio quantity, not hypothetical capital sizing, drives cost calculations.
- Fixed fees are charged once per side and do not scale with quantity.
- Percentage fees, spread, and FX scale with the correct entry/exit notionals.
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

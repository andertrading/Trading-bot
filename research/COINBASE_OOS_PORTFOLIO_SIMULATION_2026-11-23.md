# Coinbase OOS shared portfolio simulation — 2026-11-23

## Decision

**The shared portfolio simulation is gross-positive, but it does not establish a tradable edge and does not approve any strategy.** The 1,252 Coinbase-only OOS test trades were passed through the existing shared virtual portfolio allocator with $1,000 reference capital. Verified eToro execution-price equivalence and complete round-trip costs remain unavailable.

## Reproducibility and source integrity

- Portfolio workflow run: [45257891167](https://github.com/andertrading/Trading-bot/actions/runs/45257891167)
- Result artifact: [coinbase-oos-portfolio-simulation-45257891167](https://github.com/andertrading/Trading-bot/actions/runs/45257891167/artifacts/14859052638)
- Artifact ZIP SHA-256: `a4886b52920572f67e9740c34db81b493a2d5761971f973645793d0edec36703`
- Input: retained OOS artifact from workflow run [45244766224](https://github.com/andertrading/Trading-bot/actions/runs/45244766224), containing five assets × two timeframes.
- Validation: 10/10 series, 21 test folds, 1,252 test trades, zero duplicate trade identities, zero network/provider requests.
- Venue and price basis: Coinbase Exchange spot OHLC; eToro executable-price equivalence is explicitly not established.

Only trades tagged `oos_fold=test` were included. Training and validation trades were excluded. Every included trade retained its asset, timeframe, segment, and fold provenance.

## Portfolio allocation policy

The run used the existing `portfolio_backtest.simulate_portfolio` implementation; no allocator or production risk settings were changed.

- Initial virtual capital: **$1,000**.
- Score-based risk tiers from the existing configuration, with a hard maximum of **1% per trade**.
- Maximum aggregate open risk: **3% of current realized equity**.
- Long-only, unleveraged allocation with shared available cash.
- Simultaneous entries are allocated proportionally; entries are allocated before exits at the same timestamp.
- Gross P&L only. Cost estimates are reported separately and do not alter allocation or equity.

## Aggregate results

| Metric | Result |
|---|---:|
| Candidate OOS trades | 1,252 |
| Accepted trades | 921 |
| Rejected trades | 331 |
| Ending gross equity | $1,055.36 |
| Gross P&L | **+$55.36** |
| Gross return | **+5.54%** |
| Gross win rate | 36.37% |
| Gross profit factor | 1.0347 |
| Maximum realized drawdown | $66.90 (6.28%) |
| Peak open risk | $15.80 (1.53% of equity) |
| Risk-limit breach events | 0 |
| Complete verified-cost trades | **0 / 921** |
| Net P&L / net return | **Not calculable** |

All 331 rejections were reported as `limite_de_riesgo_o_capital_disponible`; the allocator did not exceed the configured aggregate risk ceiling.

## Accepted-trade breakdown

These are descriptive gross results for accepted trades only. The daily samples are too small for conclusions.

| Asset | Timeframe | Accepted | Gross P&L | Win rate | Gross PF |
|---|---|---:|---:|---:|---:|
| BTC | M15 | 239 | +$74.47 | 40.17% | 1.2322 |
| ETH | M15 | 183 | +$112.24 | 42.62% | 1.3648 |
| SOL | M15 | 178 | +$14.26 | 35.96% | 1.0399 |
| XLM | M15 | 131 | −$77.75 | 29.01% | 0.7042 |
| XRP | M15 | 185 | −$57.04 | 31.35% | 0.8286 |
| BTC | Daily | 1 | −$2.70 | 0.00% | 0.0000 |
| ETH | Daily | 2 | −$8.13 | 0.00% | 0.0000 |
| SOL | Daily | 2 | −$0.00 | 50.00% | 0.9993 |

XLM and XRP remain materially negative in this gross accepted-trade breakdown. BTC and ETH contribute most of the positive gross result. The daily sample has only five candidates in total and is not statistically informative.

## Limitations and decision

1. **Costs:** zero of the 921 accepted trades has complete verified round-trip costs. `net_pnl_usd`, `net_return_pct`, and verified net performance remain null. The +$55.36 is before verified spreads, commissions, slippage, financing, and any execution-price mismatch.
2. **Venue:** Coinbase Exchange spot OHLC is not eToro bid/ask or historical fill evidence.
3. **Drawdown:** the allocator reports realized exit-event drawdown only. Intratrade mark-to-market losses and unrealized P&L are excluded; 6.28% is not a complete portfolio-equity drawdown measure.
4. **Portfolio approximation:** independent per-asset trade outcomes are combined through shared risk and cash allocation. This is not a tick-level multi-asset execution simulation and does not model cross-asset price paths or slippage.
5. **Statistical robustness:** the synchronized cross-asset block bootstrap in the companion OOS report has mean-return intervals that include zero for all tested block lengths.

**Decision: keep all strategy approval, net-performance, live-trading, and alert gates closed.** The gross portfolio result is useful evidence about the allocator, not proof of positive net expectancy.

## Machine-readable result

The concise summary is stored in `research/COINBASE_OOS_PORTFOLIO_RESULTS_2026-11-23.json`. The full accepted/rejected trade ledger is retained in the linked 90-day GitHub Actions artifact.
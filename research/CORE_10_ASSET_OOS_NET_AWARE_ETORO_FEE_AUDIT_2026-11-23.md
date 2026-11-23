# Core 10-asset OOS net-aware eToro fee audit — 2026-11-23

## Source and scope

- Workflow run: [44978022120](https://github.com/andertrading/Trading-bot/actions/runs/44978022120)
- Artifact ID: `14736736998`
- SHA-256: `f2c6db65c03d4b7e37ee89801eefe20a392d6fb1f61ce2635a98cc0a330a7690`
- Research status: COMPLETE, 24/24 asset/timeframe combinations.
- Core sample: five US stocks + five crypto assets, 20 series across M15 and daily.
- The two configured ETFs are excluded from this core M15 sample to avoid truncating the common history.
- Calendar classification is only `COMPARABLE_BOUNDS_ONLY`; common first/last timestamps do not prove complete internal candle coverage.

## Assumed eToro fee scenario

This audit selects the deliberately lower-fee scenario:
- US stocks: $1 per side.
- Crypto: 1% per side.
- ETFs: 0 commission.
- Market spread: 0 bps.

These are public-fee assumptions, not verified instrument/account tickets. Zero spread is optimistic.

## Results

Across 20 strategy/timeframe combinations:
- 13 combinations generated trades and all 13 were net negative under the assumed fee scenario.
- 7 combinations generated no trades.
- No strategy/timeframe combination was net positive.

| Strategy / timeframe | Trades considered | Net return |
|---|---:|---:|
| Baseline M15 | 3,684 | −100.00% |
| Baseline daily | 127 | −14.97% |
| MTF price-confirmed pullback M15 | 143 | −48.53% |
| MTF trend pullback M15 | 148 | −50.64% |
| Daily EMA reclaim | 58 | −8.68% |
| Daily Donchian breakout | 79 | −13.39% |
| Daily trend pullback | 17 | −2.07% |

The best non-empty strategy/timeframe row is daily trend pullback at −2.07% on only 17 trades. It is not an approval candidate.

## Asset-class diagnostic

For MTF price-confirmed pullback M15, the non-additive class split (each row uses an independent $1,000 reference capital) is:

| Class | Trades | Net return |
|---|---:|---:|
| Five US stocks | 73 | −9.97% |
| Five crypto assets | 70 | −42.18% |

Neither class is positive at zero assumed market spread.

## Interpretation

The net-aware simulator reduces allocations as assumed commissions consume capital; its net-aware gross P&L can therefore differ from the gross-only portfolio run. This is more informative than simply subtracting costs from a fixed list of gross trades, but remains a scenario model, not verified execution:
- Actual eToro fee tier and ticket costs are not confirmed.
- Coinbase/Yahoo historical prices are not eToro executable bid/ask.
- Zero market spread excludes a material cost.
- Calendar bounds do not prove continuous synchronized data.
- The core sample excludes the two ETFs and does not represent all 12 configured assets.

## Decision

No strategy is approved for eToro under even this lower-fee, zero-spread assumption. Do not optimize or alter stops, sizing, risk, or universe to force profitability. The correct next step is to resolve the venue-cost and executable-price evidence, then preregister any new strategy research before testing.

No account endpoints, real holdings, positions, orders, balances, paid credits, production settings, automation, or alerts were accessed or changed.

## Reproduction

`python research/oos_core_net_aware_fee_audit.py --input /path/to/strategy-variants-walkforward.zip --output core_fee_audit.json`

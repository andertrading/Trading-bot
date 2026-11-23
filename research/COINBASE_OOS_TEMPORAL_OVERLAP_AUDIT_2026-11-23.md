# Coinbase OOS temporal-overlap audit — 2026-11-23

## Purpose

Measure how many of the 1,252 out-of-sample test trades overlap in calendar time. Trade count must not be mistaken for the number of independent observations. This audit is descriptive and does not alter strategy rules, sizing, or portfolio allocation.

## Source and validation

- Original OOS artifact: [workflow run 45244766224](https://github.com/andertrading/Trading-bot/actions/runs/45244766224)
- Artifact ID: `14852244870`
- Artifact SHA-256: `9e1d33001b7359e13e9540bd88a23057c394290f1eaa75134d788c5de406ffb6`
- 10/10 Coinbase-only series; 21 test folds; 1,252 test trades.
- Test folds only; validation and training trades excluded.
- Duplicate trade identities: 0. Same-asset, same-signal-time duplicates across M15/daily: 0.
- No network requests; no production changes; eToro price equivalence not established.

## Findings

| Diagnostic | Result |
|---|---:|
| Same-asset overlaps, any timeframe | 19 pairs / 20 trades involved |
| Same asset and same timeframe | 0 pairs |
| Same asset across M15 and daily | 19 pairs / 20 trades involved |
| Overlaps between different assets | 1,707 pairs / 1,022 trades involved |
| Maximum concurrent positions in candidate trade stream | 6 |
| Maximum concurrent positions in one asset | 2 (BTC) |
| Maximum concurrent positions in one asset/timeframe | 1 |

Pair overlap requires a positive-duration intersection between entry and exit. Maximum concurrency uses the allocator's conservative same-timestamp ordering: entries are processed before exits. A zero-duration trade can briefly count in the allocator event peak, but is not treated as a positive-duration overlapping pair.

## Interpretation

1. The M15 and daily streams do not produce duplicate same-asset signals at the same signal timestamp in this sample.
2. There are 19 same-asset cross-timeframe overlapping exposures. They are dependent exposures, not independent confirmations.
3. Cross-asset overlap is extensive: 1,707 pairs. This supports synchronized time-block resampling rather than an IID trade bootstrap.
4. The portfolio allocator already reserves risk and cash for simultaneous positions. This audit does not establish a risk-limit breach and does not change the allocator.
5. Calendar overlap is not a return-correlation estimate. The synchronized bootstrap remains necessary, and does not replace more market history or complete eToro costs.

## Decision

Do not interpret 1,252 trades as 1,252 independent samples. Keep strategy approval and net-performance gates closed. No strategy, sizing, risk, alert, automation, eToro account, or production setting was changed.

## Reproduction

Run `python research/oos_trade_overlap_audit.py --input-dir /path/to/extracted/artifact --output overlap_audit.json` after extracting the retained OOS artifact. The audit validates all ten series and test-fold provenance before calculating overlap.
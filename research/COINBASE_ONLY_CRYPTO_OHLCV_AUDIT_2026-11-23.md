# Coinbase-only crypto OHLCV capture and audit — 2026-11-23

## Outcome

A Coinbase Exchange-only historical capture was completed for BTC-USD, ETH-USD, SOL-USD, XLM-USD and XRP-USD. The successful GitHub Actions run is [45233527066](https://github.com/andertrading/Trading-bot/actions/runs/45233527066), with retained artifact [14848692811](https://github.com/andertrading/Trading-bot/actions/runs/45233527066/artifacts/14848692811).

- Artifact: `coinbase-only-ohlcv-45233527066`
- Retention: 90 days (expires 2027-02-21)
- Compressed size: 8,457,689 bytes
- SHA-256: `322c5cc8d47172bdb60da82c7d1b3d65b38740406cb78dc0428411de9605ae2a`
- 22 files: 10 raw OHLCV CSVs, 10 missing-timestamp CSVs, manifest and audit summary
- 1,145 logical Coinbase requests; 1,145 HTTP attempts; no retries
- No Twelve Data or EODHD calls; no cross-provider repair; no synthetic candles or volume

The capture script and workflow are in this research branch. The workflow trigger has now been changed to manual `workflow_dispatch` so later commits do not initiate another large public-data capture.

## Coverage

M15 window: 2024-12-23 17:45 UTC through 2026-11-23 17:30 UTC, 700 days, 67,200 expected 15-minute bars per asset.

Daily window: 2024-11-23 through 2026-11-22 UTC, 730 expected daily bars per asset.

| Asset | M15 received / expected | M15 missing | Daily received / expected | Daily missing |
|---|---:|---:|---:|---:|
| BTC | 67,152 / 67,200 | 48 (0.071429%) | 730 / 730 | 0 |
| ETH | 67,150 / 67,200 | 50 (0.074405%) | 730 / 730 | 0 |
| SOL | 67,152 / 67,200 | 48 (0.071429%) | 730 / 730 | 0 |
| XLM | 67,152 / 67,200 | 48 (0.071429%) | 730 / 730 | 0 |
| XRP | 67,152 / 67,200 | 48 (0.071429%) | 730 / 730 | 0 |
| **Total** | **335,758 / 336,000** | **242** | **3,650 / 3,650** | **0** |

Daily: all five series have complete expected timestamp grids.

M15: none of the five series has a complete expected timestamp grid. The missing bars are clustered in the same two multi-hour intervals for all five assets:
- 2025-10-25 15:15–20:45 UTC: 23 missing consecutive 15-minute bars in each pair.
- 2026-05-08 01:30–07:30 UTC: 25 missing consecutive 15-minute bars in each pair.
- ETH additionally misses 2025-04-25 07:30 and 07:45 UTC (two bars).

These gaps are preserved as gaps and enumerated in the per-series `*_missing_timestamps.csv` files. Do not forward-fill, interpolate, splice Twelve Data, or otherwise fabricate bars. The shared windows suggest a common source/endpoint availability issue, but this capture alone does not establish the cause.

## Data-quality checks

All ten series were checked row-by-row after download:
- Zero-volume rows: 0 across all ten series.
- Positive-volume rows: every received row (335,758 M15; 3,650 daily).
- Missing, invalid or negative volume: 0.
- Invalid OHLC rows (non-positive prices, low/high inversion, or open/close outside low-high): 0.
- Non-monotonic or duplicate timestamps in the retained series: 0.
- Malformed response items and out-of-window response items: 0.

Coinbase candle volume is reported in the base-asset units for the Coinbase product. This is Coinbase venue volume, not consolidated global-market volume and not eToro executable bid/ask volume.

## Research interpretation / gates

1. The daily Coinbase-only histories are structurally complete and have non-zero, valid venue volume. They are suitable as a candidate research input, subject to price/venue/timezone reconciliation and strategy-specific validation.
2. The M15 histories are **not gap-free**. Any M15 research must split data into contiguous segments, reset indicator warm-up after each gap, and exclude warm-up bars; no backtest may silently treat the time series as continuous.
3. The long common missing intervals are material. The M15 set is not approved as a continuous 700-day input.
4. This validates that Coinbase returned positive volume for these products; it does not establish that Coinbase volume is an appropriate proxy for eToro pricing or total market activity.
5. Do not promote either timeframe to production or change provider routing, strategy, sizing, alerts, or execution. Existing source continuity, eToro OHLC adjustment, bid/ask/spread and transaction-cost gates remain closed.
6. The old SOL M15 run `44978022120` remains mixed-provider (Coinbase + Twelve Data gap repair). This newly retained Coinbase-only capture supersedes it for pure-source research.

## Run integrity / artifact note

Two push-triggered workflow runs were created while the workflow still used a branch push trigger. Both completed the Coinbase download (1,145 requests each). Run 45233513739 failed only at artifact upload because its artifact name contained a literal backslash; run 45233527066 uploaded the complete artifact successfully. The workflow is now manual-dispatch-only to prevent commits from starting another full capture.

## Reproducibility

- Script: `research/download_coinbase_only_history.py`
- Workflow: `.github/workflows/coinbase_only_history.yml`
- Successful run commit: `1712215c6e6e74ad5be81a7c8f68f5a256312fbf`
- Capture started: 2026-11-23 17:59:41 UTC
- Manifest and raw CSVs are retained in the GitHub Actions artifact above; the report itself does not copy the 28 MB of uncompressed raw CSVs into the Git repository.

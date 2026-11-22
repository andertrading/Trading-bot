# ETF daily paper-data capture

## Purpose

Build an append-only, reproducible daily OHLCV history for the initial daily MVP universe:

- CSPX.LSE — iShares Core S&P 500, USD listing
- CNDX.LSE — iShares Nasdaq 100, USD listing

The capture uses the existing EODHD daily adapter after its XLON calendar, OHLC, split-adjustment and daily-gap checks. It does not change the production `PROVIDER_MAP`, strategy selection, signal generation, risk sizing or execution behavior.

## Schedule and storage

Workflow: `.github/workflows/etf_paper_data_capture.yml`

- Scheduled weekdays at 17:35 UTC, after the London cash session has closed in both winter and summer.
- Also supports manual `workflow_dispatch`.
- Uses the existing `EODHD_API_KEY`, `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` secrets.
- Persists to a dedicated Upstash key: `tradingbot:etf_daily_paper_data:v1`.
- This key is separate from `tradingbot:signals:v1` and from the virtual trade ledger.
- A sanitized capture report is uploaded as a 30-day GitHub Actions artifact.

The first successful capture seeds the currently available validated history (about 252 sessions per ETF). Later captures append new sessions. Re-running a capture for an already stored session is idempotent.

## Revision and data-quality policy

- Dates must be unique and ordered.
- OHLC values must be finite, positive and internally coherent; volume must be finite and non-negative.
- The persisted source, XLON calendar and exact LSE symbol for each ETF are pinned and validated on every load.
- Previously captured bars are immutable. If EODHD changes any stored OHLCV value, the capture fails closed and does not overwrite the stored state.
- Such a revision requires explicit review. A corporate-action rebase, provider correction or symbol/listing change must never be silently mixed into a prospective paper record. If a future split causes the provider's rolling history to be rebased, capture intentionally stops until the historical basis is reconciled.
- The adapter excludes bars dated outside XLON trading sessions and rejects incomplete/gapped daily history before storage.

## Safety boundaries

- Public market data only.
- No eToro API, account currency, balances, holdings, positions, orders or portfolio endpoints are accessed.
- No broker execution, live signals or Telegram alerts are enabled by this workflow.
- The virtual capital ledger remains separate and fixed at USD 1,000.
- Captured OHLCV is not a substitute for eToro executable bid/ask prices. It cannot establish the spread or complete net transaction costs.
- The dataset is a prospective paper-data record, not evidence of strategy profitability. Strategy and production gates remain closed.

## Operational note

The scheduled capture begins only after this workflow is merged into the repository's default branch. Until then, it is available for review and manual dispatch from the PR branch.

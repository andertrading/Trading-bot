# Scheduled scanner architecture

## Runtime separation

- The Render web service remains an optional HTTP/status interface.
- Scheduled scans run directly from GitHub Actions; they do not depend on Render staying awake.
- Signal state is stored in Upstash Redis through its HTTPS REST API.
- GitHub Actions uses one concurrency group so two scheduled scans cannot write the same state concurrently.

Render Free web services spin down after 15 minutes without inbound traffic and lose local filesystem changes when they spin down, restart or redeploy. Therefore a local JSON/SQLite file on the Render Free instance is not a durable signal store.

## Scan schedule

All cron times are UTC.

- Intraday: minutes 3, 18, 33 and 48 of every hour. The scanner uses completed 15-minute candles and skips equity/ETF requests while the relevant exchange is closed, on weekends or on configured market holidays.
- Swing: once daily at 00:08 UTC, after the UTC crypto daily candle boundary and the prior equity sessions.
- The scheduled workflow can also be run manually for either `15m` or `1d`.

GitHub scheduled workflows have a five-minute minimum interval, but GitHub documents that scheduled runs can be delayed or dropped under high load. This is periodic polling, not a guaranteed real-time feed.

## Safety defaults

The workflow is opt-in and does nothing until the repository Actions variable `SIGNAL_AUTOMATION_ENABLED` is exactly `true`.

Telegram alerts are separately disabled by default through `SIGNAL_ALERTS_ENABLED`. In addition, `STRATEGY_VALIDATED` is a code-level release gate and is currently `False`. It must not be changed until current-provider, cost-inclusive, out-of-sample and walk-forward validation demonstrates a stable edge.

No scan can place orders, access an eToro account, or infer whether the user entered manually. Expiry, invalidation and target-level messages describe the market signal only.

## Required GitHub Actions configuration

Repository secrets:

- `UPSTASH_REDIS_REST_URL`
- `UPSTASH_REDIS_REST_TOKEN`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TWELVE_DATA_API_KEY`
- `EODHD_API_KEY`

The data-provider secrets are already referenced by the backtest workflow; verify that they exist in the repository Actions secrets. Telegram secrets may currently exist only in Render and must be added to GitHub Actions if scheduled Telegram notifications are later enabled.

Repository Actions variables:

- `SIGNAL_AUTOMATION_ENABLED=false` initially. Change to `true` only after Upstash and all required secrets are configured.
- `SIGNAL_ALERTS_ENABLED=false` initially. Keep false until the strategy release gate is passed.

The Upstash free Redis plan currently advertises 250 MB and 500,000 commands per month. The scanner retains pending signals and prunes terminal signal history older than 90 days (with an additional record cap).

## Signal lifecycle

- Stable ID per asset, timeframe, direction and completed candle.
- Two future completed bars of validity; equity daily expiry respects US/London trading calendars, weekends and early closes.
- Stale/missing market data does not invalidate a signal, but clock-based expiry still applies.
- A stop-level event invalidates the research setup; a target-level event records a market milestone. Neither event claims that a user position was opened or closed.
- Failed Telegram notifications are retained for retry.

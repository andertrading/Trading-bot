# Extended daily history for backtests

The production scanner keeps its existing providers and history behavior. The
extended mode is opt-in and intended only for historical research.

Set:

- `BACKTEST_EXTENDED_DAILY_HISTORY=true`
- `BACKTEST_DAILY_LOOKBACK_DAYS=1825`
- `BACKTEST_DATA_MODE=download`
- `BACKTEST_SNAPSHOT_DIR=historicos_daily_5y`
- `BACKTEST_TIMEFRAMES=1d`

For CSPX/CNDX, extended backtests use Yahoo daily OHLC because the EODHD free
tier is limited to one year of daily history. Normal/live daily scans continue
to use EODHD. Yahoo OHLC is marked split-adjusted and not dividend-adjusted.
The other ten assets continue to use Twelve Data; the provider response is
validated and the resulting snapshot records the exact provider, date range,
row count, and adjustment policy.

The default 730-day backtest keeps the legacy `2y` snapshot label. Other daily
lookbacks are recorded as `<days>d`, so replay validation cannot silently mix
different history windows.

This mode does not enable Telegram alerts or trading. It does not place orders.

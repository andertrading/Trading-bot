# Extended intraday history for backtests

The live scanner continues to use the normal 5,000-bar Twelve Data request. Extended history is enabled only when the explicit environment variable `BACKTEST_EXTENDED_INTRADAY_HISTORY=true` is present.

## Backfill policy

- Default lookback: 700 calendar days (about 1.9 years).
- Crypto 15m: 50-day windows, at most 4,800 theoretical bars per request.
- US equities 15m: 180-day windows, comfortably below the 5,000-bar provider cap for regular sessions.
- Each window uses both `start_date` and `end_date`; Twelve Data documents that the pair returns all values in the selected range and that `outputsize` must be omitted.
- Windows are contiguous. Duplicate boundary candles are removed, then the complete series is sorted and checked for intraday gaps. If any window fails or the combined series has gaps, the asset history is rejected rather than silently backtested with incomplete data.

For the current universe this is approximately 90 additional Twelve Data requests for intraday history (70 crypto windows and 20 equity windows), plus the normal daily history requests. It is a one-off research/backtest operation, not part of the scheduled live scanner. The Twelve Data Basic plan currently lists 800 credits/day and 8 credits/minute; the provider dashboard should be checked before running large backfills.

## Reproducibility

The backtest workflow stores the extended OHLC snapshots and manifest in its artifact. Subsequent parameter research should use replay mode against those saved snapshots rather than downloading the same history again.

# Coinbase vs Twelve Data vs eToro crypto OHLC reconciliation — 2026-11-23

## Scope and retained inputs

This is a read-only comparison of existing retained files; it made no new provider requests.

- Coinbase Exchange-only capture: run [45233527066](https://github.com/andertrading/Trading-bot/actions/runs/45233527066), artifact [14848692811](https://github.com/andertrading/Trading-bot/actions/runs/45233527066/artifacts/14848692811).
- Twelve Data historical snapshots: artifact 14690539346 from run 44862820157, `historicos_backtest_extended/`.
- eToro historical chart candles: artifact 14840597932 from run 45223607860, `etoro_historical_candles.json`.

All comparisons below use exact UTC timestamp matches. Percentage difference is `abs(Coinbase - comparison provider) / abs(comparison provider) * 100`. Coinbase is the numerator/source being evaluated. Conflicting eToro duplicate timestamps are excluded according to the retained eToro manifest policy. No timestamp shifting, interpolation, cross-provider bar substitution or normalization is applied.

## Coinbase vs Twelve Data

### Daily OHLC close reconciliation

Five assets, 728 exact common UTC daily timestamps per asset.

| Asset | Median absolute close difference | P95 | Maximum |
|---|---:|---:|---:|
| BTC | 0.04126% | 0.14977% | 0.27126% |
| ETH | 0.04393% | 0.15192% | 1.04006% |
| SOL | 0.04178% | 0.14656% | 0.28231% |
| XLM | 0.04516% | 0.17193% | 1.36885% |
| XRP | 0.03753% | 0.14408% | 0.23148% |

For all five assets, the median close difference is small on exact timestamp matches. However, non-close OHLC tails can be materially larger: examples include daily low outliers around 4.9% for BTC, 5.0% for ETH, 4.8% for SOL and 41.2% for XRP. These tails need investigation before any OHLC source substitution or combined-source backtest.

A ±1-calendar-day shift worsens the median close difference for every asset (exactly aligned medians 0.0375–0.0452%; shifted medians approximately 1.14–2.20%). This supports exact UTC date alignment for this comparison; it does not establish equal candle construction or venue prices.

### M15 OHLC close reconciliation

The retained Twelve Data artifact has M15 snapshots for BTC, ETH, XLM and XRP, but not SOL. SOL M15 from the old research run was Coinbase + Twelve Data gap repair and is deliberately excluded from this comparison.

| Asset | Exact common timestamps | Median absolute close difference | P95 | Maximum |
|---|---:|---:|---:|---:|
| BTC | 66,904 | 0.04046% | 0.14921% | 1.51680% |
| ETH | 66,902 | 0.03957% | 0.14505% | 2.12033% |
| XLM | 66,904 | 0.04491% | 0.15789% | 27.72082% |
| XRP | 66,904 | 0.03962% | 0.14154% | 18.83806% |

A ±1 M15-bar shift worsens the median close difference for all four assets (exact medians 0.0396–0.0449%; shifted medians 0.1159–0.2161%).

The largest M15 close outliers cluster around 2025-10-10 21:15 UTC: BTC 1.52%, ETH 2.12%, XLM 27.72%, XRP 18.84%. These may represent venue-specific price/market microstructure differences or source anomalies; this comparison does not assign a cause. The very large XLM/XRP outliers are not compatible with treating the two M15 series as interchangeable.

### Volume provenance

- Coinbase capture: every received candle has positive volume (base-asset units as reported for the Coinbase Exchange product).
- Twelve Data retained crypto snapshots: BTC, ETH, XLM and XRP have zero volume in every retained M15 and daily row; SOL daily also has zero volume. SOL M15 is not a Twelve Data-only series and is excluded here.
- eToro chart candles: volume is null for every retained candle in all five crypto assets.

This confirms a practical source difference: the Coinbase endpoint returns non-zero venue volume while the retained Twelve Data crypto snapshots do not, and the retained eToro chart data has no crypto volume field values. It does not prove that Coinbase venue volume represents consolidated market volume or eToro volume.

## Coinbase vs eToro daily chart candles

eToro price basis is recorded as `UNSPECIFIED`; historical bid/ask and executable fill-price basis are unavailable. The eToro source also contains conflicting duplicate daily timestamps, which were excluded using the artifact's `excluded_conflict_timestamps` list.

| Asset | Exact common dates | Median absolute close difference | P95 | Maximum |
|---|---:|---:|---:|---:|
| BTC | 729 | 0.46130% | 1.85500% | 3.74594% |
| ETH | 729 | 0.53334% | 2.52184% | 5.87351% |
| SOL | 729 | 0.63784% | 2.71320% | 7.31859% |
| XLM | 728 | 0.71918% | 3.17938% | 7.72180% |
| XRP | 729 | 0.54261% | 2.65681% | 6.75277% |

A ±1-day shift worsens the median close difference for every asset (exact medians 0.461–0.719%; shifted medians 1.163–2.301%). Timestamp alignment is therefore not the principal source of the Coinbase/eToro price divergence in this sample.

These differences are substantially larger than Coinbase/Twelve Data daily close differences. They are consistent with meaningful venue/chart-price basis differences, but cannot establish which price is executable or which source is correct. No eToro bid/ask history is available to make that determination.

## Decisions

1. Coinbase-only daily crypto OHLCV is a credible **research candidate**, with valid non-zero Coinbase venue volume and low median daily close differences versus Twelve Data on exact timestamps. It is not approved as an eToro execution-price substitute.
2. Coinbase M15 is **not a complete continuous 700-day history**: 242 expected bars are missing, including two common multi-hour gaps. The Twelve Data comparison shows small median exact-time close differences but material tails, especially XLM/XRP. M15 requires gap-aware segmentation and separate outlier review before research use.
3. Do not merge Coinbase, Twelve Data and eToro OHLCV into a single candle series. Do not splice Coinbase volume into Twelve Data/eToro price bars.
4. Do not use Coinbase volume as a consolidated market-volume or eToro-volume proxy without an independently validated rationale.
5. Keep production source routing, strategies, sizing, risk, alerts and execution unchanged. eToro price adjustment, bid/ask/spread and transaction-cost gates remain closed.

## Reproducibility notes

- Coinbase capture manifest records UTC windows, request counts, missing timestamp files and source policy.
- Twelve Data daily/M15 metadata marks provider `twelvedata`; raw crypto volume in these snapshots is zero.
- eToro artifact records chart-candle source, unspecified price-adjustment status, no bid/ask history, and the duplicate-date conflicts.
- This report was generated from the three retained artifacts listed above. No live eToro account portfolio, position or order data was accessed.

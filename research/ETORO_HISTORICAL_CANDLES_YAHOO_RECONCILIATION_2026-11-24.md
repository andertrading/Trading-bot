# eToro historical daily candle audit — 2026-11-24

## Provenance and scope

Analysis of retained artifacts from [Actions run 45486134324](https://github.com/andertrading/Trading-bot/actions/runs/45486134324), captured 2026-11-24 around 20:58 UTC.

- eToro historical candles: artifact `14963623696`, SHA-256 `06a374cd59db04b91afb085af3afaa62b84406a18b91d48b9a06828a64d38eb6`.
- Yahoo comparison: artifact `14964566830`, SHA-256 `bccf30233fa2bfd22e0ef349d3f8490ccd5fe8136ae95cc7ee23c8c8b7ff392e`.
- eToro public historical chart OHLCV, daily, 1,000 candles requested per instrument.
- Yahoo Finance daily OHLC via yfinance with `auto_adjust=False`, actions requested and repair disabled.
- Both artifacts explicitly report `account_data_accessed=false` and `order_actions_performed=false`.

This analysis uses only retained market-history artifacts. No account, portfolio, balance, holdings, positions, PnL, orders, or trade-history endpoint was accessed. No additional eToro or provider request was made for this audit.

## Collection quality

All 12 configured instruments resolved and no request-level errors were recorded, but the overall capture status is `PARTIAL`.

- 7/12 assets pass the collector's strict no-conflict and minimum-history checks.
- 12/12 are marked research-usable, but the five crypto series have ambiguous duplicate daily timestamps and are not strict-history usable.
- 12/12 have missing expected sessions; 0/12 qualify as continuous history.
- Price-adjustment status is `UNSPECIFIED` for every asset.
- Historical bid/ask candles are unavailable and execution-price basis is unconfirmed for every asset.

| Asset | Bars | First date | Last date | Conflicting dates | Missing expected sessions | Strict history |
|---|---:|---|---|---:|---:|---|
| CSPX | 513 | 2024-10-31 | 2026-11-24 | 0 | 10 | Yes |
| CNDX | 1,000 | 2022-11-02 | 2026-11-24 | 0 | 27 | Yes |
| TSLA | 1,000 | 2022-10-26 | 2026-11-24 | 0 | 28 | Yes |
| NVDA | 1,000 | 2022-10-26 | 2026-11-24 | 0 | 28 | Yes |
| AAPL | 1,000 | 2022-10-26 | 2026-11-24 | 0 | 28 | Yes |
| MSFT | 1,000 | 2022-10-26 | 2026-11-24 | 0 | 28 | Yes |
| TTWO | 1,000 | 2022-10-26 | 2026-11-24 | 0 | 28 | Yes |
| BTC | 996 | 2024-03-02 | 2026-11-24 | 2 | 2 | No |
| ETH | 996 | 2024-03-02 | 2026-11-24 | 2 | 2 | No |
| SOL | 996 | 2024-03-02 | 2026-11-24 | 2 | 2 | No |
| XLM | 996 | 2024-03-02 | 2026-11-24 | 2 | 2 | No |
| XRP | 996 | 2024-03-02 | 2026-11-24 | 2 | 2 | No |

All five crypto series share ambiguous timestamps `2024-11-03` and `2025-11-02`. Strict history excludes those conflicted dates; no source row is silently selected or repaired.

The 1,000-candle limit means this is not a five-year series for every instrument. CSPX returned 513 bars; each crypto series retained 996 rows after the two conflicted dates. The 1,000-bar equity series begin in October/November 2022. This does not establish complete or continuous history.

## Same-date eToro versus Yahoo close comparison

All 12 assets have overlapping dates. The comparison excludes the incomplete current UTC day and rejects incoherent Yahoo OHLC rows before computing the main metrics.

| Asset | Comparable days | Median absolute close difference | P95 absolute close difference | Invalid Yahoo OHLC | Yahoo dates missing |
|---|---:|---:|---:|---:|---:|
| CSPX | 509 | 0.133% | 0.266% | 1 | 2 |
| CNDX | 990 | 0.161% | 0.332% | 2 | 7 |
| TSLA | 995 | 0.159% | 0.385% | 0 | 4 |
| NVDA | 995 | 0.164% | 0.447% | 0 | 4 |
| AAPL | 995 | 0.150% | 0.314% | 0 | 4 |
| MSFT | 995 | 0.152% | 0.318% | 0 | 4 |
| TTWO | 995 | 0.164% | 0.733% | 0 | 4 |
| BTC | 995 | 0.627% | 2.036% | 0 | 0 |
| ETH | 995 | 0.716% | 2.657% | 0 | 0 |
| SOL | 995 | 0.762% | 2.977% | 0 | 0 |
| XLM | 995 | 0.895% | 3.023% | 0 | 0 |
| XRP | 995 | 0.722% | 2.581% | 0 | 0 |

The median of the 12 asset-level median absolute close differences is 0.164%; the median of the 12 asset-level P95 values is 0.590%. Equity/ETF closes are generally closer in this sample than crypto closes. These are descriptive cross-provider differences, not execution slippage or broker spread.

For every asset, same-date alignment (Yahoo offset 0) has lower median and P95 absolute close difference than shifting Yahoo by -1 or +1 calendar day. This supports matching calendar dates for this dataset, but does not establish identical session cutoffs, price construction, or executable price parity.

### OHLC anomalies

Close comparisons are materially more stable than some high/low comparisons.

- CSPX has one invalid Yahoo OHLC row: 2025-02-03 Yahoo low is 231.00 while the other fields and eToro range are around 630. It is excluded from main OHLC metrics.
- CSPX also has unusually large Yahoo high outliers on dates such as 2025-02-28 and 2025-02-24, despite much closer open/close/low values.
- CNDX has two invalid Yahoo OHLC rows.
- Material close outliers remain visible and are not discarded merely for being large; examples include TTWO (-7.405% on 2025-11-06) and SOL (-11.412% on 2024-04-13), using eToro-minus-Yahoo signed percentage.

The Yahoo request used unadjusted OHLC (`auto_adjust=False`), but eToro's adjustment basis is unspecified. Corporate actions, venue, session boundaries, provider revisions, and differing price construction remain possible explanations; no cause is asserted.

## What this proves — and what it does not

**Supported by the artifacts**
- eToro's public historical chart endpoint returned daily OHLCV for all 12 configured instruments.
- Seven instruments pass the collector's strict no-conflict and minimum-history criteria.
- Five crypto series contain two ambiguous duplicate daily timestamps each.
- Same-date Yahoo close values are relatively close for the equity/ETF series and less close for crypto, with notable outliers.
- Same-date alignment beats +/-1-day shifts in this captured comparison.

**Not established**
- Historical bid/ask, spread, midpoint, last-trade, or executable fill basis.
- Whether eToro candles are split-adjusted, dividend-adjusted, or otherwise adjusted.
- Identical exchange sessions or daily cutoffs between providers.
- Which provider is correct on individual outlier dates.
- Round-trip costs, slippage, FX, overnight fees, or account-specific fees.
- Net strategy expectancy or permission to route live signals.

## Next work and consequences

1. Keep eToro history as a separate source; do not silently merge it with Coinbase or Yahoo data.
2. Preserve strict exclusion of conflicted crypto dates and missing-session diagnostics. Do not synthesize or repair candles.
3. Use the seven strict equity/ETF series only for controlled research, with explicit adjustment-basis and execution-basis caveats.
4. Never promote chart OHLC or Yahoo parity to executable bid/ask evidence.
5. Continue the separate round-trip cost investigation; open-only hypothetical costs remain insufficient.
6. Keep the no-new-backfill guardrail for the five-year raw-history retention issue. This eToro capture does not replace missing retained five-year research snapshots.

**Conclusion:** this provides an independent daily chart-history reference and a useful calendar-alignment diagnostic. It does not resolve executable-price parity or round-trip costs and does not change the bot's non-production status.

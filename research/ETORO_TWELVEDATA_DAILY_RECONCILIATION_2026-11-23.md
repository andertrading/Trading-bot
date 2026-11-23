# eToro vs Twelve Data daily OHLC reconciliation — 2026-11-23

## Scope

Research-only comparison of two already-retained datasets:

- eToro read-only historical chart candles from the 2026-11-23 capture (workflow run [45223607860](https://github.com/andertrading/Trading-bot/actions/runs/45223607860)).
- Twelve Data daily OHLC snapshots from the retained `extended-intraday-backtest-2026-11-21` artifact (workflow run [44862820157](https://github.com/andertrading/Trading-bot/actions/runs/44862820157), artifact 14690539346).

No market-data API requests were made for this comparison. The two artifacts were downloaded from GitHub Actions and compared locally by UTC calendar date. The five crypto series use eToro's research-only candles after excluding both rows at every conflicting duplicate timestamp.

This is a price-discrepancy diagnostic, not a source approval or execution-price validation.

## Method

For each of the ten core assets, match eToro and Twelve Data daily candles on the same UTC date and calculate:

`absolute_close_difference_pct = abs(eToro_close / TwelveData_close - 1) * 100`

Report the number of overlapping dates, median absolute difference, 95th percentile, and maximum. Also repeat the median comparison with the Twelve Data series shifted by -1 and +1 calendar days to test whether a simple date-boundary offset explains the discrepancy.

Price policies are not proven equivalent:

- Twelve Data daily OHLC is configured as split-adjusted, without dividend adjustment.
- eToro explicitly reports its historical chart-candle adjustment status as `UNSPECIFIED`.
- Neither source establishes historical executable bid/ask prices or eToro fill prices.

## Same-date close comparison

| Asset | Overlap dates | Median abs. close diff | P95 abs. close diff | Maximum abs. close diff |
|---|---:|---:|---:|---:|
| AAPL | 491 | 0.148% | 0.433% | 4.604% |
| MSFT | 491 | 0.152% | 0.439% | 7.196% |
| TSLA | 491 | 0.164% | 0.499% | 4.116% |
| NVDA | 491 | 0.176% | 0.667% | 4.170% |
| TTWO | 491 | 0.180% | 0.986% | 7.405% |
| BTC | 730 | 0.451% | 1.822% | 3.629% |
| ETH | 730 | 0.553% | 2.608% | 5.658% |
| XRP | 730 | 0.579% | 2.729% | 6.290% |
| SOL | 730 | 0.630% | 2.665% | 7.878% |
| XLM | 729 | 0.722% | 3.094% | 7.173% |

The median same-date close differences are comparatively small for the five US equities (0.148%–0.180%), but the tails are material: P95 ranges from 0.433% to 0.986%, with individual maximum differences of 4.116%–7.405%.

For crypto, median differences range from 0.451% to 0.722%, while P95 ranges from 1.822% to 3.094% and maximum differences from 3.629% to 7.878%.

## Date-shift sensitivity

The median absolute close difference increased for every asset when the Twelve Data series was shifted by one calendar day in either direction.

| Asset | Same date | Twelve Data -1 day | Twelve Data +1 day |
|---|---:|---:|---:|
| AAPL | 0.148% | 0.730% | 0.760% |
| MSFT | 0.152% | 0.864% | 0.881% |
| TSLA | 0.164% | 1.957% | 1.948% |
| NVDA | 0.176% | 1.557% | 1.549% |
| TTWO | 0.180% | 0.991% | 0.999% |
| BTC | 0.451% | 1.144% | 1.341% |
| ETH | 0.553% | 1.690% | 1.932% |
| XRP | 0.579% | 1.714% | 1.967% |
| SOL | 0.630% | 2.019% | 2.231% |
| XLM | 0.722% | 2.169% | 2.341% |

A simple ±1-day shift does not explain the differences. Same-date alignment is materially closer in this sample, but it does not prove identical candle boundaries or price construction.

## eToro missing-session cross-check

The eToro capture's expected-session gap list was cross-checked against the retained Twelve Data/EODHD daily dates:

- Each of the five US equities (TSLA, NVDA, AAPL, MSFT, TTWO) has 11 missing expected sessions in the recent two-year window. **All 11 dates are present** in the corresponding Twelve Data daily snapshot for every one of the five equities.
- CSPX and CNDX each have 10 missing expected sessions in that window. Four of those ten dates are present in the retained EODHD daily ETF snapshot; the remaining dates fall outside the available one-year EODHD range or cannot be cross-checked from the retained data.

For the five US equities, this confirms that the eToro omissions are not ordinary exchange holidays: an independent daily provider has bars for every date the configured XNYS calendar expects and eToro omitted. It does not establish whether the cause is an eToro API sampling issue, source correction, or another feed-specific condition. For the ETFs, the shorter EODHD history prevents a complete independent check.

## OHLC field observations

Median absolute differences for the Open field are higher than for High/Low on several instruments, particularly equities:

- US equity median absolute Open differences: approximately 0.264%–0.526%.
- Crypto median absolute Open differences: approximately 0.461%–0.721%.
- High/Low medians are generally lower in this sample, but their upper tails and individual outliers remain material.

This pattern is consistent with different daily candle boundary/venue construction or source-specific price handling, but the comparison cannot identify which source is correct. The eToro feed's adjustment status is unspecified, and the Twelve Data series uses its configured split-adjustment policy.

Largest same-date close discrepancies include TSLA 4.116% (2026-04-22), NVDA 4.170% (2025-11-19), AAPL 4.604% (2026-07-30), MSFT 7.196% (2025-07-30), TTWO 7.405% (2025-11-06), BTC 3.629% (2026-04-07), ETH 5.658% (2026-04-07), SOL 7.878% (2025-04-02), XLM 7.173% (2026-05-29), and XRP 6.290% (2025-08-07).

## Interpretation

1. Same-date closes broadly align at a diagnostic level for equities, but their P95/max tails are too large to assume exact parity.
2. Crypto close discrepancies are larger, especially in the P95 tail.
3. A one-day calendar shift worsens the median comparison for all ten instruments.
4. Open/high/low parity is not established; eToro's adjustment basis is unspecified.
5. The eToro histories also have independent continuity defects: the seven stock/ETF histories miss expected exchange sessions; all recent two-year gaps for the five US equities appear in the corresponding Twelve Data daily snapshots. Crypto research histories exclude conflicting DST-transition dates.
6. The Twelve Data crypto snapshots have zero volume in the retained daily/M15 files, so this OHLC comparison does not validate crypto volume or any relative-volume strategy.
7. Neither provider's chart OHLC can be treated as historical eToro executable bid/ask prices. No historical spread, slippage, FX, commission, or stop-fill model is validated by this comparison.

## Decision

**COMPARISON ONLY — NOT VALIDATION.**

- Do not replace one provider's candles with the other's.
- Do not merge OHLC fields across providers.
- Do not infer historical bid/ask, slippage, or eToro execution prices from these differences.
- Do not mark any instrument economically eligible or complete the transaction-cost registry from this comparison.
- Keep production signals, sizing and operational alerts disabled.
- Preserve the existing market-session gap guards and duplicate-conflict exclusions.

## Reproducibility and credit policy

The comparison used only the retained GitHub Actions artifacts above. It made no new Twelve Data or EODHD API calls and consumed no provider credits. Repeating it requires the same source artifacts; the raw five-year daily extension from the later research workflow was not retained in that workflow's metrics artifact, as tracked separately in issue #190.

Related findings:
- [Issue #189 — crypto volume provenance](https://github.com/andertrading/Trading-bot/issues/189)
- [Issue #190 — research snapshot retention and ETF depth](https://github.com/andertrading/Trading-bot/issues/190)
- [eToro historical API assessment](https://github.com/andertrading/Trading-bot/blob/main/ETORO_READ_ONLY_API_ASSESSMENT.md)

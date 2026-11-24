# Coinbase rejected-signal shadow replay — 2026-11-24

## Executive conclusion

This is an offline, research-only counterfactual. It does not change production strategy logic, signal thresholds, the asset universe, data routing, or execution. The replay uses Coinbase spot OHLCV and gross R only; it does not establish eToro executable prices or net expectancy.

The existing M15 quality threshold is protective in this sample: removing it makes both validation and test substantially worse. Removing the main-trend gate is regime-dependent: positive in validation and sharply negative in test. The two RR gates have distinct effects and neither is ready to change: the strategy-level RR gate has only small combined gross improvements, while removing the execution RR gate has a mixed standalone candidate cohort and remains unpriced. The ATR gate produced no candidate that passed all remaining gates.

**No production change is justified.** The previously exposed test period is diagnostic, not a pristine holdout for selecting new thresholds.

## Inputs and integrity

- Coinbase-only raw OHLCV artifact: workflow run `45233527066`, artifact `14848692811`; ZIP SHA-256 `322c5cc8d47172bdb60da82c7d1b3d65b38740406cb78dc0428411de9605ae2a`.
- OOS output artifact: workflow run `45495049495`, artifact `14966689253`; ZIP SHA-256 `4f82e9d5d12dc7a5db43fd473b398af654189c04533593f1999056d2560cdae8`.
- Ten series: BTC, ETH, SOL, XLM and XRP; M15 and daily. Coinbase was the only data source. All ten OOS outputs report zero network requests, no production changes and null verified cost-adjusted R.
- Indicators were recalculated from the retained raw candles using the repository's `indicators.py` formulas and segment reset policy. All **119,636** strategy-rejection records mapped to a source candle; every recorded OHLC-derived indicator in those records matched the recalculation within numerical tolerance.
- The baseline candidate queue was rebuilt from the retained `signal_diagnostics`, including both executed and overlap-skipped candidates. The sequential replay reproduced all **2,861/2,861** original OOS trades exactly: signal/entry/exit timestamps, exit reason and four-decimal result R.
- The 9,335 original prepared candidates comprise 2,861 executed trades and 6,474 candidates skipped due to overlap. Keeping the skipped candidates in the queue is essential: adding an earlier shadow candidate can change which later baseline candidate is accepted.

## Counterfactual method

Each scenario relaxes one gate only. The other strategy gates remain in place, and each eligible shadow candidate is passed through the same entry-on-next-candle, stop/target, gap-entry, minimum remaining-risk and holding-horizon rules. For the execution-RR scenario only, the execution RR cutoff is bypassed; the original strategy filters remain. Crypto series are contiguous within each segment, so no session-close rule applies.

The combined replay merges the complete original prepared candidate queue with the eligible shadow candidates, sorts chronologically per asset/timeframe/fold, and applies the original no-overlap rule. It is a per-series gross-R comparison, **not** a portfolio simulation: it does not apply shared capital, aggregate risk, position sizing, cross-asset concurrency, or costs.

### M15 counterfactual results

| One-gate scenario | Eligible shadow candidates (validation / test) | Shadow trades selected (validation / test) | Baseline R → combined R (validation) | Baseline R → combined R (test) |
|---|---:|---:|---:|---:|
| Remove score/quality floor | 15,257 / 12,977 | 1,979 (-95.32R) / 1,711 (-166.53R) | -8.61R → -135.80R (-127.19R) | +6.86R → -183.31R (-190.17R) |
| Remove EMA50 > EMA200 trend gate | 720 / 823 | 377 (+19.65R) / 437 (-72.72R) | -8.61R → +8.32R (+16.93R) | +6.86R → -72.53R (-79.39R) |
| Remove strategy-level RR gate, retain execution RR gate | 1,678 / 1,413 | 447 (+4.29R) / 333 (-41.49R) | -8.61R → -2.43R (+6.19R) | +6.86R → +9.66R (+2.79R) |
| Remove execution-level RR gate only | 2,905 / 2,268 | 656 (-33.69R) / 514 (+19.33R) | -8.61R → -3.11R (+5.50R) | +6.86R → +52.49R (+45.63R) |
| Remove ATR% ceiling | 0 / 0 | 0 / 0 | No eligible candidates | No eligible candidates |

R values are gross and rounded to two decimals. Shadow trades selected are those admitted in the combined chronological replay, not the full independent candidate set. Baseline and combined totals sum independent asset/timeframe/fold replays; they are not portfolio returns.

### Interpretation

1. **Keep the score floor.** Removing it adds thousands of candidates and damages both validation and test. The non-overlapping shadow cohort alone is -116.16R in validation and -218.31R in test before it is merged with baseline candidates.
2. **Keep the main-trend gate.** Its apparent validation benefit reverses sharply in test. This is regime dependence, not robust evidence for removing the gate.
3. **Do not lower the strategy-level RR gate yet.** The one-gate counterfactual is only modestly positive in combined gross R and the selected shadow cohort is negative in test. The daily sample is too small to infer anything.
4. **The execution-level RR cutoff deserves a separate, future hypothesis test, not an immediate change.** The combined gross replay improves in both M15 periods, but the shadow cohort itself is negative in validation (-33.69R) and positive in test (+19.33R). The standalone non-overlap shadow sequence also changes sign between validation (-36.19R) and test (+36.39R). This instability, plus unverified costs, prevents approval.
### Execution RR threshold sensitivity (exploratory only)

The engine recorded **5,173 M15 execution-RR rejections**. Their median actual entry RR was 1.9768; 73.8% were at or above 1.95 and 31.1% at or above 1.99. This confirms that the strict 2.00 cutoff rejects many near-threshold candidates, but does not by itself show that admitting them is profitable.

A sensitivity replay admitted only rejected candidates meeting each lower actual-RR threshold, while retaining the complete baseline candidate queue and original no-overlap ordering:

| Actual execution RR floor | Eligible candidates (validation / test) | Combined gross delta vs baseline (validation / test) |
|---:|---:|---:|
| 1.90 | 2,679 / 2,065 | +12.65R / +43.41R |
| 1.95 | 2,145 / 1,673 | +2.25R / +46.12R |
| 1.98 | 1,270 / 1,106 | +9.37R / +23.38R |
| 1.99 | 851 / 758 | +8.87R / +18.95R |
| 1.995 | 697 / 643 | +9.77R / +9.81R |

These are exploratory gross deltas, not an optimized threshold recommendation. At the 1.95 floor, the standalone non-overlap shadow cohort is -18.99R in validation and +48.63R in test; results also vary materially by asset. The combined deltas depend partly on which original trades are displaced by earlier shadow candidates. The test period has already been examined, and no verified costs are included. Therefore, no lower RR threshold is approved.

5. **The ATR ceiling is not the binding opportunity filter here.** No volatility-rejected M15 candidate passed both the trend and score requirements when ATR alone was relaxed. Daily counterfactual counts are too small.

## Limitations and decision

- Coinbase venue prices/volume are not eToro executable bid/ask or consolidated market data.
- No candidate result includes verified round-trip eToro costs, FX, slippage or overnight charges. Net expectancy remains unknown.
- OOS test outputs have already been inspected in prior diagnostics. They cannot now serve as an untouched holdout for choosing new parameters.
- Counterfactual cohorts overlap heavily. Independent candidate sums must not be interpreted as achievable strategy returns; the combined chronological replay is the relevant no-overlap comparison, but still not a portfolio backtest.
- No parameter, score threshold, ATR ceiling, trend rule, risk limit, asset, workflow or production file was changed.

**Decision:** retain current production filters. Prioritize verified execution-cost evidence and a genuinely future/untouched forward sample before considering any RR-gate adjustment. No live signal or trade is authorized by this report.

## Reproduction context

- Main commit used as code reference: `61fe35bb38c3643654d196dc8076051b205fecf3`.
- Research output run: `45495049495`.
- Research only; zero API/provider calls during this analysis.
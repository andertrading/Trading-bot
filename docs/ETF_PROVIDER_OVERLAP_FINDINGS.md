# ETF provider overlap findings — CSPX / CNDX

**Run:** GitHub Actions `44980789543`  
**Artifact ID:** `14738020763` (`etf-provider-overlap-audit`)  
**Audited code commit:** `4416a79330ceb2b789d07390d3d4d941fd21a3a3`  
**Window requested:** 2025-11-16 to 2026-11-21  
**Actual overlap:** CSPX 251 sessions; CNDX 246 sessions  
**Scope:** public market OHLCV only. No eToro account, portfolio, positions, orders, or signals accessed.

## Executive decision

**Do not replace or standardize providers based on this run. The extended Yahoo LSE history is not approved for strategy research yet.** The audit has found material OHLC data-quality defects that can distort ATR, stops, targets, and any strategy using intraday range.

The successful Actions status means the diagnostic completed and produced its artifact; it does **not** mean that the provider data passed quality thresholds.

## Findings

| Asset | Yahoo LSE vs EODHD | Independent SIX cross-check | Decision |
|---|---|---|---|
| CSPX | Open and close practically identical (median absolute error below 0.000003%); high is materially inconsistent: median 0.091%, p95 6.002%, max 16.384%; 65 matched sessions have high error above 1%. One matched Yahoo LSE bar (2026-03-02) is structurally invalid; across the full Yahoo response, 2 of 257 bars are structurally invalid. | CSSPX.SW high/low values are generally much closer to EODHD than the Yahoo LSE high series; SIX is a cross-check, not an interchangeable primary feed. | **Yahoo LSE CSPX OHLC rejected for extended backtests pending repair.** |
| CNDX | Open/close practically identical; high p95 error below 0.000004%, max 0.319%; low max error 0.342%. One matched Yahoo LSE bar (2026-03-02) is structurally invalid; across the full Yahoo response, 7 of 257 bars are structurally invalid. | CSNDX.SW has materially wider high/low differences, so it should not be used as a blind substitute for the LSE feed. | **Yahoo LSE CNDX is a candidate after explicit repair/rejection of the invalid 2026-03-02 bar and a wider-period audit. Not approved yet.** |

### Automated research-quality gate

The audit now applies a conservative gate: zero structurally invalid Yahoo OHLC rows, p95 absolute OHLC discrepancy no greater than 0.5%, and maximum absolute OHLC discrepancy no greater than 1.0% versus EODHD. Both assets fail, so `provider_research_approved` is `false`. The full Yahoo responses contain 2/257 structurally invalid CSPX rows and 7/257 CNDX rows; within the EODHD overlap, each has one invalid row on 2026-03-02. This distinguishes a completed diagnostic run from approval of the underlying data.

### Session coverage

- CSPX: 251 matched sessions; no EODHD-only sessions; 5 Yahoo-only sessions.
- CNDX: 246 matched sessions; 2 EODHD-only sessions; 5 Yahoo-only sessions.
- Volume is not fully interchangeable: CSPX Yahoo/EODHD median ratio is 1.071 and p95 1.467; CNDX ratio is 1.0 over the matched positive-volume observations.

### Structural defects

The Yahoo LSE 2026-03-02 bar is internally inconsistent for both instruments: its reported low is above the open. The current extended-backtest repair path detects structurally invalid OHLC bars and can patch them from the SIX listing only when the close deviation is at most 0.5%. On this sample, the SIX close deviations for that date are 0.229% (CSPX) and 0.155% (CNDX), so they fall within the configured limit; this still needs an end-to-end test through the actual yfinance download path. Crucially, the repair path does not detect a structurally valid yet economically implausible high. For CSPX, 65 Yahoo LSE highs exceed EODHD by more than 1%, while open and close match EODHD almost exactly.

## Impact on the current code path

- Normal daily scans for London ETFs use EODHD.
- Extended daily backtests use Yahoo LSE to obtain a longer history.
- Therefore, historical strategy evaluation can see different high/low values from the live daily feed. This can alter ATR, stop placement, target levels, and trade outcomes even when open/close match.
- Existing Yahoo alternate-SIX repair is only invoked for structurally invalid bars. It should be able to repair the 2026-03-02 rows under the configured close-deviation threshold, but that must be verified end-to-end. It will not catch the CSPX high-series anomalies that remain structurally valid.
- This diagnostic obtains Yahoo data from the Yahoo chart endpoint, while the extended-backtest helper uses yfinance. Both are Yahoo-sourced, but the exact rows and repair behavior must be checked through the production helper before certifying the full pipeline.

## Required next steps

1. Add provider-quality checks that test OHLC coherence **and** cross-provider range anomalies on the overlapping window.
2. Reject the CSPX Yahoo LSE extended history for any research that depends on High/Low until the source or repair method is validated.
3. Add an end-to-end test through the actual yfinance extended-history helper to verify that both 2026-03-02 bad bars are repaired from SIX (or fail closed) and that the repaired OHLC is coherent.
4. For CNDX, validate a longer overlap and confirm that the one bad bar is repaired or causes a fail-closed stop.
5. Keep EODHD as the current live daily source; do not switch production routing based on this diagnostic.
6. Do not run new strategy optimization until the historical/live OHLC basis is consistent enough for the chosen research window.

## Limitations

- This is one rolling year, not a multi-year provider certification.
- Yahoo SIX is a different exchange listing of the same USD share class. It is useful as an independent anomaly check, but differences in liquidity, trading hours, and volume mean it is not automatically a drop-in replacement for LSE.
- This diagnostic compares split-adjusted OHLC without dividend adjustment. It does not validate eToro executable prices, spreads, slippage, or fees.
- A provider's successful HTTP response or a green workflow is not evidence of accurate prices.

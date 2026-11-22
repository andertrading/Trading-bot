# ETF provider overlap findings — CSPX / CNDX

**Latest yfinance-path run:** GitHub Actions `44981488628`  
**Artifact ID:** `14738042078` (`etf-provider-overlap-audit`)  
**Audited code commit:** `34e796eb3bed8a7e3d717a0557b3cff400fe9c80`  
**Window requested:** 2021-11-22 to 2026-11-21  
**Yahoo chart history:** 1,262 rows per ETF; **yfinance history used by backtests:** CSPX 1,260 rows, CNDX 1,255 rows  
**EODHD history returned:** CSPX 252 rows; CNDX 254 rows (approximately one year only)  
**Actual EODHD overlap:** CSPX 251 sessions; CNDX 246 sessions (2025-11-24 to 2026-11-20)  
**Scope:** public market OHLCV only. No eToro account, portfolio, positions, orders, or signals accessed.

## Executive decision

**Do not replace or standardize providers based on this run. The extended Yahoo LSE history is not approved for strategy research yet.** The audit has found material OHLC data-quality defects that can distort ATR, stops, targets, and any strategy using intraday range.

The successful Actions status means the diagnostic completed and produced its artifact; it does **not** mean that the provider data passed quality thresholds.

## Findings

| Asset | Yahoo LSE vs EODHD | Independent SIX cross-check | Decision |
|---|---|---|---|
| CSPX | Open and close practically identical (median absolute error below 0.000003%); high is materially inconsistent: median 0.091%, p95 6.002%, max 16.384%; 65 matched sessions have high error above 1%. On the exact yfinance backtest path, 2 of 1,260 CSPX bars are structurally invalid (2024-05-02 and 2026-03-02). The Yahoo chart endpoint shows two additional invalid rows that yfinance omits. | CSSPX.SW high/low values are generally much closer to EODHD than the Yahoo LSE high series; SIX is a cross-check, not an interchangeable primary feed. | **Reject Yahoo LSE CSPX High/Low for extended backtests. Investigate SIX as the historical source; do not patch individual highs automatically.** |
| CNDX | Open/close practically identical; high p95 error below 0.000004%, max 0.319%; low max error 0.342%. On the exact yfinance backtest path, 9 of 1,255 CNDX bars are structurally invalid (seven dates in 2022/2023 and 2026-03-02). The Yahoo chart endpoint reports additional invalid rows that yfinance omits. | CSNDX.SW has materially wider high/low differences, so it should not be used as a blind substitute for the LSE feed. | **CNDX LSE is a candidate only after the real repair smoke test passes and an independent multi-year source is found. Not approved yet.** |

### Automated research-quality gate

The audit now applies a conservative gate: zero structurally invalid Yahoo OHLC rows, p95 absolute OHLC discrepancy no greater than 0.5%, and maximum absolute OHLC discrepancy no greater than 1.0% versus EODHD. Both assets fail, so `provider_research_approved` is `false`. The exact yfinance backtest histories contain 2/1,260 structurally invalid CSPX rows and 9/1,255 CNDX rows. All are eligible for SIX repair under the current safeguards. EODHD returned only about one year of history despite the five-year request, so price comparison against EODHD remains limited to the latest 251/246 overlapping sessions. The five-year Yahoo structural-integrity scan is broader, but it does not certify older bars against an independent price source. This distinguishes a completed diagnostic run from approval of the underlying data.

### Exact yfinance path and repair feasibility

The audit now downloads the same yfinance history path used by `data_engine.py` (`auto_adjust=False`, `actions=True`, `repair=False`) and normalizes the daily session date. Across all matched five-year rows, Yahoo chart OHLC and yfinance OHLC are identical; therefore, the CSPX high anomalies are present in the exact backtest data path, not just in a separate chart endpoint.

The production SIX alternate source was also downloaded through yfinance:
- CSPX SIX: 1,257 rows, zero structurally invalid bars.
- CNDX SIX: 1,253 rows, zero structurally invalid bars.

The audit mirrored the production repair safeguards (same-date alternate bar, coherent alternate OHLC, close deviation no greater than 0.5%, and repair count cap):
- CSPX: both invalid yfinance bars are repairable; maximum close deviation 0.229308%; production cap 13.
- CNDX: all nine invalid yfinance bars are repairable; maximum close deviation 0.174442%; production cap 13.

This is strong evidence that the existing structural repair routine can repair the detected bad rows. The audit assesses eligibility against the real downloaded rows; the automated tests separately exercise the repair function and extended-download orchestration. It does **not** repair the CSPX high-series anomalies because those bars are structurally valid.

### Live smoke test through the production helper

The controlled GitHub Actions run executed `data_engine._download_yahoo(..., "1d")` against live public market data with the five-year lookback. The actual helper successfully repaired 2 CSPX bars from CSSPX.SW (maximum close deviation 0.2293%) and 9 CNDX bars from CSNDX.SW (0.1744%). It then correctly rejected both complete histories because the LSE session on **2025-10-24 is missing**. The UK exchange calendar identifies it as a valid session (previous session 2025-10-23; next 2025-10-27).

This is a useful fail-closed result: the repair routine itself works on real downloaded data, but the full history is still not usable because a missing session remains. The smoke test did not produce an accepted backtest dataframe for either ETF. Do not bypass the gap validator or silently forward-fill the missing candle.

### Missing-session source audit and alternative-provider access

The missing-session audit shows:
- **CSPX:** 2026-03-06 is present in EODHD and can be sourced from the current live provider; SIX also matches EODHD closely on that date. The other unresolved date is 2025-10-24.
- **CNDX:** EODHD has valid bars for the recent missing sessions 2025-12-22, 2025-12-29/30/31, 2026-01-02, 2026-03-06, 2026-05-04 and 2026-08-31. SIX is absent on several of those dates or diverges materially on High, so EODHD is the safer fill source for those recent dates.
- **2025-10-24 remains unresolved for both ETFs:** Yahoo chart has no usable Close; EODHD does not return that date under the current one-year history window; SIX has no CNDX bar, and its CSPX bar cannot be cross-validated against a valid LSE close.

The existing Twelve Data credential was tested without exposing its value. Symbol search resolves both instruments as USD ETFs on XLON, but its response marks access as **Grow** (business: Venture); the current key's time-series request returns HTTP 404. This is not a usable Basic-tier alternative. Twelve Data's official pricing lists Basic at 8 credits/minute and Grow as the tier that includes EOD global equities/ETFs; no upgrade has been made. References: https://twelvedata.com/pricing and https://twelvedata.com/etf.

### Five-year comparison between the two Yahoo listings

The same-share-class LSE and SIX yfinance histories overlap for 1,236 CSPX sessions and 1,233 CNDX sessions:
- **CSPX:** LSE/SIX High absolute error has p95 8.519% and max 21.207%; 388 sessions differ by more than 1%. Open and Close p95 errors are only 0.119% and 0.143%. This reinforces that the CSPX LSE High series is anomalous, but does not by itself prove which listing is correct for the older four years.
- **CNDX:** LSE/SIX High p95 error is 0.968% (max 6.355%) and Low p95 error is 0.746% (max 7.639%). These listings are not interchangeable as a blanket replacement.

On the recent EODHD overlap, the SIX yfinance CSPX High is much closer to EODHD (p95 0.150%, max 0.354%) than the LSE yfinance High (p95 6.002%, max 16.384%). For CNDX the reverse is true: LSE is closer to EODHD (High max 0.318%, Low max 0.341%) while SIX differs by as much as 2.406% on High and 2.306% on Low. Therefore, the current evidence supports investigating SIX as a CSPX historical source, but it does not justify globally replacing LSE with SIX or using SIX for CNDX.

### Session coverage

- CSPX: 251 matched sessions; no EODHD-only sessions; 5 Yahoo-only sessions.
- CNDX: 246 matched sessions; 2 EODHD-only sessions; 5 Yahoo-only sessions.
- Volume is not fully interchangeable: CSPX Yahoo/EODHD median ratio is 1.071 and p95 1.467; CNDX ratio is 1.0 over the matched positive-volume observations.

### Structural defects

Within the recent EODHD overlap, the Yahoo LSE 2026-03-02 bar is internally inconsistent for both instruments: its reported low is above the open. The five-year scan found additional structurally invalid Yahoo bars on the dates listed above. The current extended-backtest repair path detects structurally invalid OHLC bars and can patch them from the SIX listing only when the close deviation is at most 0.5%. The five-year yfinance audit confirms all 2 CSPX and 9 CNDX invalid bars have same-date coherent SIX alternatives within that limit; maximum deviations are 0.229308% and 0.174442%, respectively. Crucially, the repair path does not detect a structurally valid yet economically implausible high. For CSPX, 65 Yahoo LSE highs exceed EODHD by more than 1%, while open and close match EODHD almost exactly.

## Impact on the current code path

- Normal daily scans for London ETFs use EODHD.
- Extended daily backtests use Yahoo LSE to obtain a longer history.
- Therefore, historical strategy evaluation can see different high/low values from the live daily feed. This can alter ATR, stop placement, target levels, and trade outcomes even when open/close match.
- Existing Yahoo alternate-SIX repair is only invoked for structurally invalid bars. It should be able to repair the 2026-03-02 rows under the configured close-deviation threshold, but that must be verified end-to-end. It will not catch the CSPX high-series anomalies that remain structurally valid.
- The diagnostic now checks both Yahoo's chart endpoint and the exact yfinance helper used by extended backtests. Their OHLC values match exactly on the five-year overlapping rows. The repair feasibility calculation mirrors the production constraints; a live, unmocked full-history run through the production helper remains a separate final verification.

## Required next steps

1. Add provider-quality checks that test OHLC coherence **and** cross-provider range anomalies on the overlapping window.
2. Reject the CSPX Yahoo LSE extended history for any research that depends on High/Low until the source or repair method is validated.
3. Resolve the 2025-10-24 LSE session using an independently validated source; current Yahoo/EODHD/SIX data do not provide a valid cross-check, so keep rejecting the history until one is available.
4. Do not upgrade Twelve Data or EODHD solely for this project yet; first establish whether a viable strategy exists using currently available data and costs.
5. Re-run the production smoke test only after the missing-session source is validated, and require complete exchange-calendar coverage.
6. Investigate SIX as a CSPX historical source, but do not substitute or patch highs until its older history is independently validated.
7. Keep EODHD as the current live daily source; do not switch production routing based on this diagnostic.
8. Do not run new strategy optimization until the historical/live OHLC basis is consistent enough for the chosen research window.

## Limitations

- This is a five-year Yahoo structural scan but only a one-year EODHD price overlap, not a five-year cross-provider certification. The one-year EODHD response is consistent with EODHD's Free plan, whose official documentation states one year of EOD history; paid EOD Historical All-World lists 30+ years. EODHD also cautions that actual history varies by ticker and exchange, so the plan limit alone does not guarantee full CSPX/CNDX coverage. No paid upgrade is assumed or recommended at this stage. References: https://eodhd.com/help/plans and https://eodhd.com/pricing/.
- Yahoo SIX is a different exchange listing of the same USD share class. It is useful as an independent anomaly check, but differences in liquidity, trading hours, and volume mean it is not automatically a drop-in replacement for LSE.
- This diagnostic compares split-adjusted OHLC without dividend adjustment. It does not validate eToro executable prices, spreads, slippage, or fees.
- A provider's successful HTTP response or a green workflow is not evidence of accurate prices.

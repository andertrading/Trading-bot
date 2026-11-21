# Price adjustments and eToro transaction-cost model

## Price adjustment policy

The bot uses one explicit policy per timeframe:

- **15-minute OHLC:** raw, unadjusted prices. Twelve Data documents that intraday prices are unadjusted; Yahoo chart OHLC is consumed as raw quote OHLC, not the separate adjusted-close field.
- **Daily OHLC:** split-adjusted OHLC in current share units, without dividend adjustment. Twelve Data documents split adjustment for daily bars. EODHD supplies raw OHLC plus adjusted close, so the adapter fetches the EODHD splits endpoint and applies each split ratio to bars strictly before the ex-split date.
- **Volume:** EODHD already reports split-adjusted volume; the adapter does not adjust it again.
- **Dividends:** not applied to OHLC. This is a price-series policy, not a total-return/reinvested-dividend backtest.

The EODHD split response uses new-shares / old-shares. Historical prices before the event are multiplied by old-shares / new-shares. If the split endpoint fails or returns malformed data, the daily ETF history fails closed. Split events are cached for six hours to limit API use.

The adjustment metadata is attached to live DataFrames and saved in backtest snapshots. Old snapshots with the previous unverified policy are intentionally rejected by replay validation; regenerate them with the current workflow.

**Important limitation:** consistent split treatment does not make different vendors' OHLC identical, nor does it guarantee eToro execution-price parity. Exchange venue, auction prints, timestamps, corporate-action corrections and bid/ask execution can still differ.

## eToro fee model

The code uses public eToro fee information as a **baseline estimate**, not as proof of the fees on an individual account or instrument:

| Execution type | Asset | Baseline used |
|---|---|---:|
| REAL | Stocks | $2 per side (upper end of published $1–$2 range) |
| REAL | ETFs | $0 platform commission; eToro confirms UK SDRT exemption for ETFs |
| REAL | Crypto | 1% per side (base tier; Club discounts may apply) |
| CFD | Stocks / ETFs | 0.15% per side |
| CFD | Crypto | 1% per side |

The model also supports:
- Full market bid/ask spread in basis points, charged as half-spread on entry and exit.
- FX conversion when quote currency differs from account currency.
- CFD financing: for leveraged BUY stock/ETF CFDs, the published 6.4% annual rate plus the currency benchmark, applied to opening notional and divided by 365 for each broker-billable night (including the x3 weekend charge); for short stock/ETF CFDs, an instrument-specific annual borrow rate applied to opening notional; and for crypto CFDs, the instrument-specific USD-per-unit nightly fee. Unleveraged BUY stock/ETF CFDs have no overnight fee; crypto CFD overnight fees remain applicable to Spanish clients.
- One-off deposit/withdrawal FX conversion, calculated separately from trade-level FX when the source and account currencies differ.

The model deliberately does **not** invent a market spread, FX fee or CFD financing rate. Even if a spread estimate is entered, historical OHLC-to-eToro bid/ask execution-price parity remains unverified, so `costs_complete` remains false until that basis is validated. Missing components are listed in each trade row.

Funding conversion is a one-off account-funding cost, not a per-trade cost. It has a separate estimator and is not deducted from each trade's R. The current model leaves it unpriced until the actual source currency, deposit method, account currency and applicable conversion fee are known. The published $5 withdrawal fee for USD accounts (local-currency withdrawals may be free) is also excluded from per-trade R and must be handled separately in cash-flow planning.

The eToro public fee schedule says stock commissions vary by country and exchange, crypto fees may vary by Club level, conversion fees depend on location/payment method/Club level, and CFD financing rates can change. The current overnight schedule states that non-leveraged BUY stock/ETF positions do not incur overnight fees, while crypto CFDs incur nightly fees (with a stated German-client exception). It also warns that some unleveraged BUY orders in stocks and ETFs can still be executed as CFDs because of product restrictions. The Spanish page states that new Spanish users have not been able to trade CFDs since 2 August 2024. The bot therefore keeps CFD availability and each instrument's actual execution type unconfirmed until the order ticket labels it REAL or CFD.

## Backtest interpretation

- Daily backtests use at most the last two years of bars, matching the data engine's validated equity/crypto gap window; 15-minute backtests use the provider's available history.
- `result_R` is the gross strategy result.
- `result_R_after_known_fees` subtracts only the fees that the model can calculate from the published baseline and configured inputs. It is **not** an all-in net result while any cost is unpriced.
- `costs_complete` is the explicit all-cost completeness flag.
- Hypothetical quantity is calculated from the reference capital and the bot's risk tier only to translate fixed-dollar fees into R. It is not a live position size and does not simulate simultaneous portfolio allocation.
- The bot's validation gate and `ALLOW_POSITION_SIZING=False` remain in place.

## Primary documentation

- Twelve Data price adjustments: https://support.twelvedata.com/en/articles/5179064-are-the-prices-adjusted
- EODHD OHLC and adjusted-close methodology: https://eodhd.com/help/methodology
- EODHD corporate splits endpoint: https://eodhd.com/financial-apis/api-splits-dividends
- eToro Spain fee schedule: https://www.etoro.com/es/trading/fees/
- eToro CFD spreads: https://www.etoro.com/es/trading/fees/cfd-spreads/
- eToro SDRT help page (explicitly exempts ETFs): https://help.etoro.com/s/article/What-is-the-Stamp-Duty-Reserve-Tax-on-UK-listed-stocks?language=en_GB
- eToro overnight CFD fees: https://www.etoro.com/es/trading/fees/cfd-overnight-fees/
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
| REAL | ETFs | $0 platform commission |
| REAL | Crypto | 1% per side (base tier; Club discounts may apply) |
| CFD | Stocks / ETFs | 0.15% per side |
| CFD | Crypto | 1% per side |

The model also supports:
- Full market bid/ask spread in basis points, charged as half-spread on entry and exit.
- FX conversion when quote currency differs from account currency.
- Instrument-specific CFD overnight costs supplied as the broker's actual billable fee units.

The model deliberately does **not** invent a market spread, FX fee or CFD financing rate. If those values or the actual REAL/CFD classification are not confirmed, `costs_complete` remains false and the missing components are listed in each trade row.

The eToro public fee schedule says stock commissions vary by country and exchange, crypto fees may vary by Club level, conversion fees depend on location/payment method/Club level, and CFD financing rates can change. The Spanish page also states that new Spanish users have not been able to trade CFDs since 2 August 2024. The bot therefore keeps CFD availability and each instrument's actual execution type unconfirmed.

## Backtest interpretation

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
- eToro overnight CFD fees: https://www.etoro.com/es/trading/fees/cfd-overnight-fees/
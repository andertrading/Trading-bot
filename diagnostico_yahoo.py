
import yfinance as yf
import pandas as pd


VERSION = "1.0"

TICKERS = {
    "CSPX": "CSPX.L",
    "CNDX": "CNDX.L",
}

START_DATE = "2026-02-27"
END_DATE = "2026-03-05"

TARGET_DATE = "2026-03-02"

OHLC_COLUMNS = ["Open", "High", "Low", "Close"]


def check_candle(row):
    try:
        open_price = float(row["Open"])
        high_price = float(row["High"])
        low_price = float(row["Low"])
        close_price = float(row["Close"])

        return (
            high_price >= max(open_price, close_price, low_price)
            and low_price <= min(open_price, close_price, high_price)
            and min(open_price, high_price, low_price, close_price) > 0
        )
    except (TypeError, ValueError, KeyError):
        return False


def download_data(ticker, auto_adjust, repair):
    return yf.Ticker(ticker).history(
        start=START_DATE,
        end=END_DATE,
        interval="1d",
        auto_adjust=auto_adjust,
        actions=True,
        repair=repair,
    )


def inspect_data(asset, ticker, label, auto_adjust, repair):
    print("\n" + "=" * 70)
    print(f"{asset} | {ticker} | {label}")
    print("=" * 70)

    try:
        data = download_data(ticker, auto_adjust, repair)

        if data is None or data.empty:
            print("ERROR: Yahoo Finance no ha devuelto datos.")
            return

        print(f"Filas recibidas: {len(data)}")
        print(f"Columnas: {list(data.columns)}")

        print("\nDatos descargados:")

        columns = [
            col for col in
            OHLC_COLUMNS + [
                "Adj Close",
                "Volume",
                "Dividends",
                "Stock Splits",
            ]
            if col in data.columns
        ]

        print(data[columns].to_string())

        target = data[
            data.index.strftime("%Y-%m-%d") == TARGET_DATE
        ]

        print(f"\nComprobación del {TARGET_DATE}:")

        if target.empty:
            print("No se ha encontrado esa fecha.")
            return

        for date, row in target.iterrows():
            print(f"Fecha: {date}")

            for column in columns:
                print(f"{column}: {row[column]}")

            valid = check_candle(row)

            print(
                "Vela OHLC:",
                "CORRECTA" if valid else "INCORRECTA"
            )

    except Exception as error:
        print(f"ERROR durante la descarga: {error}")


def main():
    print("=" * 70)
    print(f"DIAGNÓSTICO YAHOO FINANCE {VERSION}")
    print(f"yfinance instalado: {yf.__version__}")
    print("=" * 70)

    for asset, ticker in TICKERS.items():

        inspect_data(
            asset,
            ticker,
            "SIN AJUSTAR",
            auto_adjust=False,
            repair=False,
        )

        inspect_data(
            asset,
            ticker,
            "AUTO ADJUST ACTIVADO",
            auto_adjust=True,
            repair=False,
        )

        inspect_data(
            asset,
            ticker,
            "REPARACIÓN YAHOO ACTIVADA",
            auto_adjust=False,
            repair=True,
        )

    print("\n" + "=" * 70)
    print("DIAGNÓSTICO FINALIZADO")
    print("=" * 70)


if __name__ == "__main__":
    main()

from tradingview_scraper.symbols.technicals import Indicators

i = Indicators()
data = i.scrape(
    exchange="NASDAQ",
    symbol="AAPL",
    timeframe="1m",
    allIndicators=True
)

print("=== Datos de TradingView ===")
for key, value in data.items():
    print(f"{key}: {value}")

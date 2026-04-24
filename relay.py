from flask import Flask, request
import os, threading, asyncio, re
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from tradingview_scraper.symbols.technicals import Indicators
import yfinance as yf

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")

flask_app = Flask(__name__)
scheduler = BackgroundScheduler()
watchlist = {}
tg_app    = None
bot_loop  = None

FUTURES_MAP = {
    "ES1": "CME_MINI", "NQ1": "CME_MINI", "YM1": "CME_MINI", "RTY1": "CME_MINI",
    "GC1": "COMEX",    "SI1": "COMEX",    "HG1": "COMEX",
    "CL1": "NYMEX",    "NG1": "NYMEX",    "RB1": "NYMEX",
    "ZB1": "CBOT",     "ZN1": "CBOT",     "ZC1": "CBOT",
}

EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ",
    "NYQ": "NYSE",   "NYA": "NYSE",
    "PCX": "AMEX",   "ASE": "AMEX",
    "BTS": "BINANCE","CCC": "BINANCE",
}

def detect_exchange(symbol):
    s = symbol.upper().replace("!","")
    if s in FUTURES_MAP:
        return FUTURES_MAP[s]
    forex_currencies = ["USD","EUR","GBP","JPY","CHF","AUD","CAD","NZD","MXN","BRL","SEK","NOK","DKK"]
    if len(s) == 6 and s[:3] in forex_currencies and s[3:] in forex_currencies:
        return "FX"
    if re.search(r'(USDT|USDC|BUSD|BTC|ETH|BNB)$', s):
        return "BINANCE"
    try:
        info = yf.Ticker(symbol).fast_info
        exch = getattr(info, 'exchange', None)
        if exch and exch in EXCHANGE_MAP:
            return EXCHANGE_MAP[exch]
    except:
        pass
    return "NASDAQ"

def get_fundamentals(symbol):
    try:
        info = yf.Ticker(symbol).info
        pe     = info.get("trailingPE")
        margin = info.get("profitMargins")
        roe    = info.get("returnOnEquity")
        pe_txt     = f"`{round(pe,2)}`"          if pe     else "`N/A`"
        margin_txt = f"`{round(margin*100,2)}%`"  if margin else "`N/A`"
        roe_txt    = f"`{round(roe*100,2)}%`"     if roe    else "`N/A`"
        return pe_txt, margin_txt, roe_txt
    except:
        return "`N/A`", "`N/A`", "`N/A`"

def sma_emoji(price, sma):
    if not sma or sma == 0: return "⚪"
    return "🟢" if price > sma else "🔴"

@flask_app.route("/")
def home():
    return "Bot activo", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    data    = request.get_json() if request.is_json else {"message": request.data.decode()}
    ticker  = data.get("ticker",  "N/A")
    price   = data.get("price",   "N/A")
    action  = data.get("action",  "")
    message = data.get("message", "")
    emoji   = "🟢" if "buy" in action.lower() else "🔴" if "sell" in action.lower() else "📡"
    text = (f"{emoji} *Alerta TradingView*\n"
            f"📌 Ticker: `{ticker}`\n"
            f"💲 Precio: `{price}`\n"
            f"⚡ Acción: `{action}`\n"
            f"📝 {message}")
    if tg_app and bot_loop:
        asyncio.run_coroutine_threadsafe(
            tg_app.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown"),
            bot_loop
        )
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

def get_stock(symbol, exchange=None):
    try:
        if not exchange:
            exchange = detect_exchange(symbol)
        tv_symbol = symbol.upper()
        clean = tv_symbol.replace("!","")
        if clean in FUTURES_MAP and not tv_symbol.endswith("!"):
            tv_symbol = clean + "!"
        i    = Indicators()
        data = i.scrape(exchange=exchange, symbol=tv_symbol, timeframe="1w", allIndicators=True)
        if data.get("status") != "success":
            return None, exchange
        d      = data.get("data", {})
        close  = round(d.get("close") or 0, 2)
        change = round(d.get("change") or 0, 2)
        sma20  = round(d.get("SMA20")  or 0, 2)
        sma50  = round(d.get("SMA50")  or 0, 2)
        sma200 = round(d.get("SMA200") or 0, 2)
        rsi    = round(d.get("RSI")    or 0, 2)
        if close == 0:
            return None, exchange
        pe_txt, margin_txt, roe_txt = get_fundamentals(clean)
        chg_emoji = "🟢" if change >= 0 else "🔴"
        rsi_emoji = "🔴" if rsi >= 70 or rsi <= 30 else "🟢"
        msg = (
            f"📡 *{clean}* — `${close}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{chg_emoji} Cambio diario: `{change}%`\n"
            f"📊 RSI14:  {rsi_emoji} `{rsi}`\n"
            f"〽️ SMA20:  {sma_emoji(close,sma20)} `${sma20}`\n"
            f"〽️ SMA50:  {sma_emoji(close,sma50)} `${sma50}`\n"
            f"〽️ SMA200: {sma_emoji(close,sma200)} `${sma200}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💼 P/E TTM:    {pe_txt}\n"
            f"💼 Margen Net: {margin_txt}\n"
            f"💼 ROE:        {roe_txt}\n"
            f"🏦 `{exchange}` | _SMA/RSI Semanal_\n"
            f"_Fuente: TradingView + Yahoo Finance_"
        )
        return msg, exchange
    except Exception as e:
        return None, exchange

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📡 *Stock Price Bot*\n\n"
        "`/precio AAPL` — acciones\n"
        "`/precio SPY` — ETFs\n"
        "`/precio EURUSD` — Forex\n"
        "`/precio BTCUSDT` — Crypto\n\n"
        "`/precio AAPL NYSE` — forzar exchange\n"
        "`/watch BTCUSDT 60` — monitoreo auto\n"
        "`/stop BTCUSDT` — detener\n"
        "`/lista` — activos monitoreados",
        parse_mode="Markdown"
    )

async def precio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Uso: `/precio AAPL`", parse_mode="Markdown")
        return
    symbol   = ctx.args[0].upper()
    exchange = ctx.args[1].upper() if len(ctx.args) > 1 else None
    detected = exchange or detect_exchange(symbol)
    await update.message.reply_text(f"⏳ Consultando `{symbol}` en `{detected}`...", parse_mode="Markdown")
    msg, exch = get_stock(symbol, exchange)
    if msg:
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"❌ No encontré `{symbol}` en `{detected}`.\n"
            f"Prueba: `/precio {symbol} EXCHANGE`\n"
            f"Opciones: NASDAQ NYSE AMEX BINANCE FX CME_MINI NYMEX COMEX CBOT",
            parse_mode="Markdown"
        )

async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await update.message.reply_text("Uso: `/watch BTCUSDT 60`", parse_mode="Markdown")
        return
    symbol   = ctx.args[0].upper()
    interval = max(30, int(ctx.args[1]))
    exchange = ctx.args[2].upper() if len(ctx.args) > 2 else None

    def send_update():
        msg, _ = get_stock(symbol, exchange)
        if msg and tg_app and bot_loop:
            asyncio.run_coroutine_threadsafe(
                tg_app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown"),
                bot_loop
            )

    job_id = f"watch_{symbol}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(send_update, "interval", seconds=interval, id=job_id)
    watchlist[symbol] = interval
    await update.message.reply_text(
        f"✅ Monitoreando *{symbol}* cada *{interval}s*\n`/stop {symbol}` para cancelar.",
        parse_mode="Markdown"
    )

async def stop_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Uso: `/stop AAPL`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    if scheduler.get_job(f"watch_{symbol}"):
        scheduler.remove_job(f"watch_{symbol}")
        watchlist.pop(symbol, None)
        await update.message.reply_text(f"🛑 Detenido: *{symbol}*", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ No hay monitoreo activo para `{symbol}`.", parse_mode="Markdown")

async def lista(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not watchlist:
        await update.message.reply_text("📋 No hay monitoreos activos.")
        return
    lines = ["📋 *Monitoreos activos:*\n"] + [f"• `{s}` — cada {v}s" for s, v in watchlist.items()]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

def main():
    global tg_app, bot_loop
    threading.Thread(target=run_flask, daemon=True).start()
    scheduler.start()
    tg_app   = Application.builder().token(BOT_TOKEN).build()
    bot_loop = asyncio.new_event_loop()
    tg_app.add_handler(CommandHandler("start",  start))
    tg_app.add_handler(CommandHandler("precio", precio))
    tg_app.add_handler(CommandHandler("watch",  watch))
    tg_app.add_handler(CommandHandler("stop",   stop_watch))
    tg_app.add_handler(CommandHandler("lista",  lista))
    tg_app.run_polling()

if __name__ == "__main__":
    main()

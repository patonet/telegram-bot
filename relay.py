from flask import Flask, request
import os, threading, asyncio, re
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from tradingview_scraper.symbols.technicals import Indicators

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")

flask_app = Flask(__name__)
scheduler = BackgroundScheduler()
watchlist = {}
tg_app    = None
bot_loop  = None

def detect_exchange(symbol):
    s = symbol.upper()
    futures_base = ["ES1","NQ1","CL1","GC1","SI1","ZB1","ZN1","NG1","YM1","RTY1","NKD1","6E1","6J1","6B1"]
    clean = s.replace("!","")
    if clean in futures_base or s.endswith("1!") or s.endswith("2!"):
        if clean in ["GC1","SI1","HG1"]: return "COMEX"
        if clean in ["CL1","NG1","RB1","HO1"]: return "NYMEX"
        return "CME"
    forex_currencies = ["USD","EUR","GBP","JPY","CHF","AUD","CAD","NZD","MXN","BRL","SEK","NOK","DKK"]
    if len(s) == 6 and s[:3] in forex_currencies and s[3:] in forex_currencies:
        return "FX"
    if re.search(r'(USDT|USDC|BUSD|BTC|ETH|BNB)$', s):
        return "BINANCE"
    etfs = ["SPY","QQQ","IWM","DIA","GLD","SLV","USO","TLT","IEF","HYG","LQD","EEM","VTI","VOO","VEA","VWO","XLF","XLE","XLK","XLV","XLI","XLU","XLP","XLY","XLB","ARKK","ARKG","ARKW"]
    if s in etfs: return "AMEX"
    nyse = ["JPM","BAC","WFC","GS","MS","C","V","MA","XOM","CVX","JNJ","PG","KO","PEP","MCD","WMT","HD","DIS","BA","GE","IBM","MMM","CAT","T","VZ","BRK.A","BRK.B"]
    if s in nyse: return "NYSE"
    return "NASDAQ"

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

def sma_emoji(price, sma):
    if sma == 0: return "⚪"
    return "🟢" if price > sma else "🔴"

def get_stock(symbol, exchange=None):
    try:
        if not exchange:
            exchange = detect_exchange(symbol)
        futures_base = ["ES1","NQ1","CL1","GC1","SI1","ZB1","ZN1","NG1","YM1","RTY1"]
        tv_symbol = symbol.upper()
        if tv_symbol.replace("!","") in futures_base and not tv_symbol.endswith("!"):
            tv_symbol = tv_symbol + "!"
        i    = Indicators()
        data = i.scrape(exchange=exchange, symbol=tv_symbol, timeframe="1W", allIndicators=True)
        if data.get("status") != "success":
            return None, exchange
        d      = data.get("data", {})
        close  = round(d.get("close", 0), 2)
        change = round(d.get("change", 0), 2)
        sma20  = round(d.get("SMA20", 0), 2)
        sma50  = round(d.get("SMA50", 0), 2)
        sma200 = round(d.get("SMA200", 0), 2)
        rsi    = round(d.get("RSI", 0), 2)
        pe     = round(d.get("price_earnings_ttm", 0), 2)
        margin = round(d.get("net_margin", 0) * 100, 2)
        roe    = round(d.get("return_on_equity", 0) * 100, 2)
        chg_emoji  = "🟢" if change >= 0 else "🔴"
        rsi_emoji  = "🟢" if rsi < 70 and rsi > 30 else "🔴" if rsi >= 70 or rsi <= 30 else "🟡"
        pe_txt     = f"`{pe}`" if pe > 0 else "`N/A`"
        margin_txt = f"`{margin}%`" if margin != 0 else "`N/A`"
        roe_txt    = f"`{roe}%`" if roe != 0 else "`N/A`"
        msg = (
            f"📡 *{symbol.upper()}* — `${close}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{chg_emoji} Cambio semanal: `{change}%`\n"
            f"📊 RSI14:  {rsi_emoji} `{rsi}`\n"
            f"〽️ SMA20:  {sma_emoji(close,sma20)} `${sma20}`\n"
            f"〽️ SMA50:  {sma_emoji(close,sma50)} `${sma50}`\n"
            f"〽️ SMA200: {sma_emoji(close,sma200)} `${sma200}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💼 P/E TTM:    {pe_txt}\n"
            f"💼 Margen Net: {margin_txt}\n"
            f"💼 ROE:        {roe_txt}\n"
            f"🏦 Exchange: `{exchange}` | _Semanal_\n"
            f"_Fuente: TradingView_"
        )
        return msg, exchange
    except Exception as e:
        return None, exchange

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📡 *Stock Price Bot*\n\n"
        "Exchange detectado automáticamente:\n\n"
        "`/precio AAPL` — NASDAQ\n"
        "`/precio SPY` — ETF\n"
        "`/precio EURUSD` — Forex\n"
        "`/precio BTCUSDT` — Crypto\n"
        "`/precio ES1` — Futuro S&P\n\n"
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
            f"Opciones: NASDAQ NYSE AMEX BINANCE FX CME NYMEX COMEX",
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

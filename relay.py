from flask import Flask, request
import os, threading
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tradingview_scraper.symbols.technicals import Indicators

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")

flask_app = Flask(__name__)
scheduler = AsyncIOScheduler()
watchlist = {}
tg_app    = None

@flask_app.route("/")
def home():
    return "Bot activo", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json() if request.is_json else {"message": request.data.decode()}
    ticker  = data.get("ticker",  "N/A")
    price   = data.get("price",   "N/A")
    action  = data.get("action",  "")
    message = data.get("message", "")
    emoji   = "🟢" if "buy" in action.lower() else "🔴" if "sell" in action.lower() else "📡"
    text = (f"{emoji} *Alerta TradingView*\n━━━━━━━━━━━━━━━━\n"
            f"📌 Ticker: `{ticker}`\n💲 Precio: `{price}`\n"
            f"⚡ Acción: `{action}`\n📝 {message}")
    if tg_app:
        import asyncio
        asyncio.run_coroutine_threadsafe(
            tg_app.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown"),
            tg_app.update_queue._loop if hasattr(tg_app.update_queue, '_loop') else asyncio.get_event_loop()
        )
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


def get_stock(symbol, exchange="NASDAQ"):
    try:
        i    = Indicators()
        data = i.scrape(exchange=exchange, symbol=symbol, timeframe="1m", allIndicators=True)
        if data.get("status") != "success":
            return None
        d       = data.get("data", {})
        close   = round(d.get("close", 0), 2)
        ema10   = round(d.get("EMA10", 0), 2)
        ema20   = round(d.get("EMA20", 0), 2)
        rsi     = round(d.get("RSI", 0), 2)
        macd    = round(d.get("MACD.macd", 0), 4)
        sma20   = round(d.get("SMA20", 0), 2)
        rec     = d.get("Recommend.All", 0)
        if rec >= 0.5:   rec_txt = "💚 COMPRA"
        elif rec <= -0.5: rec_txt = "🔴 VENTA"
        else:             rec_txt = "🟡 NEUTRO"
        return (
            f"📡 *{symbol.upper()}* — `${close}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 RSI:    `{rsi}`\n"
            f"📈 EMA10:  `${ema10}`\n"
            f"📈 EMA20:  `${ema20}`\n"
            f"📉 SMA20:  `${sma20}`\n"
            f"⚡ MACD:   `{macd}`\n"
            f"🤖 Señal:  {rec_txt}\n"
            f"🕐 Hora:   `{datetime.utcnow().strftime('%H:%M:%S')} UTC`\n"
            f"_Fuente: TradingView_"
        )
    except Exception as e:
        return None


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📡 *Stock Price Bot — TradingView*\n\n"
        "Comandos:\n"
        "`/precio AAPL` — precio + indicadores\n"
        "`/precio AAPL NYSE` — especifica el exchange\n"
        "`/watch AAPL 60` — monitoreo cada N segundos\n"
        "`/stop AAPL` — detener monitoreo\n"
        "`/lista` — ver monitoreos activos\n\n"
        "🔔 También recibo alertas de TradingView automáticamente",
        parse_mode="Markdown"
    )

async def precio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Uso: `/precio AAPL` o `/precio AAPL NYSE`", parse_mode="Markdown")
        return
    symbol   = ctx.args[0].upper()
    exchange = ctx.args[1].upper() if len(ctx.args) > 1 else "NASDAQ"
    await update.message.reply_text(f"⏳ Consultando {symbol} en TradingView...")
    msg = get_stock(symbol, exchange)
    await update.message.reply_text(msg if msg else f"❌ No encontré `{symbol}` en `{exchange}`.", parse_mode="Markdown")

async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await update.message.reply_text("⚠️ Uso: `/watch AAPL 60`", parse_mode="Markdown")
        return
    symbol   = ctx.args[0].upper()
    interval = max(30, int(ctx.args[1]))
    exchange = ctx.args[2].upper() if len(ctx.args) > 2 else "NASDAQ"
    app      = ctx.application

    async def send_update():
        msg = get_stock(symbol, exchange)
        if msg:
            await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

    job_id = f"watch_{symbol}"
    if scheduler.get_job(job_id):
        scheduler.get_job(job_id).remove()
    scheduler.add_job(send_update, "interval", seconds=interval, id=job_id, max_instances=1)
    watchlist[symbol] = interval
    await update.message.reply_text(
        f"✅ Monitoreando *{symbol}* cada *{interval}s* vía TradingView\n`/stop {symbol}` para cancelar.",
        parse_mode="Markdown"
    )

async def stop_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Uso: `/stop AAPL`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    job    = scheduler.get_job(f"watch_{symbol}")
    if job:
        job.remove()
        watchlist.pop(symbol, None)
        await update.message.reply_text(f"🛑 Monitoreo de *{symbol}* detenido.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ No hay monitoreo activo para `{symbol}`.", parse_mode="Markdown")

async def lista(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not watchlist:
        await update.message.reply_text("📋 No hay monitoreos activos.")
        return
    lines = ["📋 *Monitoreos activos:*\n"]
    for sym, iv in watchlist.items():
        lines.append(f"• `{sym}` — cada {iv}s")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def main():
    global tg_app
    threading.Thread(target=run_flask, daemon=True).start()
    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start",  start))
    tg_app.add_handler(CommandHandler("precio", precio))
    tg_app.add_handler(CommandHandler("watch",  watch))
    tg_app.add_handler(CommandHandler("stop",   stop_watch))
    tg_app.add_handler(CommandHandler("lista",  lista))
    scheduler.start()
    tg_app.run_polling()

if __name__ == "__main__":
    main()

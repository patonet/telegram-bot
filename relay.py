from flask import Flask, request
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "TU_TOKEN_AQUI")
CHAT_ID   = os.environ.get("CHAT_ID",   "TU_CHAT_ID_AQUI")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def send_telegram(text):
    requests.post(TELEGRAM_URL, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.is_json:
        data = request.get_json()
    else:
        data = {"message": request.data.decode("utf-8")}

    ticker  = data.get("ticker",  "N/A")
    price   = data.get("price",   "N/A")
    action  = data.get("action",  "")
    message = data.get("message", "")

    emoji = "🟢" if "buy"  in action.lower() else \
            "🔴" if "sell" in action.lower() else "📡"

    text = (
        f"{emoji} *Alerta TradingView*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📌 Ticker:  `{ticker}`\n"
        f"💲 Precio:  `{price}`\n"
        f"⚡ Acción:  `{action}`\n"
        f"📝 Mensaje: {message}"
    )

    send_telegram(text)
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

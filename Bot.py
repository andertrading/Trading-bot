
import os
import requests
from flask import Flask

app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

@app.route("/")
def home():
    return "Trading Bot activo"

@app.route("/test")
def test():
    if not TOKEN or not CHAT_ID:
        return "Faltan las variables de Telegram", 500

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": "✅ Trading Bot conectado correctamente. ¡Prueba superada!"
        },
        timeout=15
    )

    return "Mensaje enviado" if response.ok else "Error al enviar mensaje", response.status_code

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

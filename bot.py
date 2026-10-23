import asyncio
import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from google import genai
from google.genai import types
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gemini-bot")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
# IDs de usuarios permitidos, separados por comas. Vacío = acceso para todos.
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_USERS", "").split(",") if x.strip()}

MODELS = {"flash": "gemini-3.8-flash", "lite": "gemini-3.5-flash-lite", "pro": "gemini-3.1-pro-preview"}
DEFAULT_PERSONA = "Eres un asistente útil. Responde en el idioma del usuario, de forma clara y concisa."

client = genai.Client(api_key=GEMINI_API_KEY)

# Ajustes y conversación por chat (en memoria)
settings = {}
chats = {}


def cfg(chat_id: int) -> dict:
    return settings.setdefault(
        chat_id, {"model": DEFAULT_MODEL, "persona": DEFAULT_PERSONA, "search": False}
    )


def get_chat(chat_id: int):
    if chat_id not in chats:
        s = cfg(chat_id)
        config = types.GenerateContentConfig(
            system_instruction=s["persona"],
            tools=[types.Tool(google_search=types.GoogleSearch())] if s["search"] else None,
        )
        chats[chat_id] = client.aio.chats.create(model=s["model"], config=config)
    return chats[chat_id]


def allowed(update: Update) -> bool:
    return not ALLOWED or update.effective_user.id in ALLOWED


async def send_long(update: Update, text: str):
    for i in range(0, len(text), 4000):  # límite de Telegram: 4096
        await update.message.reply_text(text[i : i + 4000])


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        response = None
        for attempt in range(4):  # reintenta si Google está saturado (503)
            try:
                response = await get_chat(chat_id).send_message(parts)
                break
            except Exception as e:
                if ("503" in str(e) or "UNAVAILABLE" in str(e)) and attempt < 3:
                    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise
        text = response.text or "(respuesta vacía)"
    except Exception as e:
        log.exception("Error con Gemini")
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            text = "Límite gratuito alcanzado. Inténtalo de nuevo en un minuto."
        else:
            text = f"Error con Gemini: {type(e).__name__}: {str(e)[:400]}"
    await send_long(update, text)


# ---------- Comandos ----------
async def start(update, context):
    if not allowed(update):
        return await update.message.reply_text("No tienes acceso a este bot.")
    await update.message.reply_text(
        "¡Hola! Soy un bot con Gemini 🤖\n"
        "Puedes escribirme, enviarme fotos, notas de voz o PDFs.\n"
        "Usa /ayuda para ver los comandos."
    )


async def ayuda(update, context):
    await update.message.reply_text(
        "/reset – borra la conversación\n"
        "/modelo flash|lite|pro – cambia de modelo\n"
        "/buscar – activa/desactiva la búsqueda en Google\n"
        "/persona <texto> – cambia mi personalidad (sin texto = restablecer)\n"
        "/estado – muestra tus ajustes\n"
        "/id – muestra tu ID de Telegram"
    )


async def reset(update, context):
    if not allowed(update):
        return
    chats.pop(update.effective_chat.id, None)
    await update.message.reply_text("Conversación reiniciada ✅")


async def modelo(update, context):
    if not allowed(update):
        return
    if not context.args or context.args[0] not in MODELS:
        return await update.message.reply_text("Uso: /modelo flash | lite | pro")
    cfg(update.effective_chat.id)["model"] = MODELS[context.args[0]]
    chats.pop(update.effective_chat.id, None)
    await update.message.reply_text(f"Modelo: {MODELS[context.args[0]]} (conversación reiniciada)")


async def buscar(update, context):
    if not allowed(update):
        return
    s = cfg(update.effective_chat.id)
    s["search"] = not s["search"]
    chats.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        f"Búsqueda en Google: {'activada 🔎' if s['search'] else 'desactivada'}"
    )


async def persona(update, context):
    if not allowed(update):
        return
    s = cfg(update.effective_chat.id)
    s["persona"] = " ".join(context.args) or DEFAULT_PERSONA
    chats.pop(update.effective_chat.id, None)
    await update.message.reply_text("Personalidad actualizada ✅")


async def estado(update, context):
    s = cfg(update.effective_chat.id)
    await update.message.reply_text(
        f"Modelo: {s['model']}\nBúsqueda: {'sí' if s['search'] else 'no'}\nPersona: {s['persona']}"
    )


async def mi_id(update, context):
    await update.message.reply_text(f"Tu ID: {update.effective_user.id}")


# ---------- Mensajes ----------
async def on_text(update, context):
    if not allowed(update):
        return await update.message.reply_text("No tienes acceso a este bot.")
    await ask(update, context, update.message.text)


async def on_photo(update, context):
    if not allowed(update):
        return
    f = await update.message.photo[-1].get_file()
    data = bytes(await f.download_as_bytearray())
    caption = update.message.caption or "Describe esta imagen."
    await ask(update, context, [types.Part.from_bytes(data=data, mime_type="image/jpeg"), caption])


async def on_voice(update, context):
    if not allowed(update):
        return
    f = await update.message.voice.get_file()
    data = bytes(await f.download_as_bytearray())
    await ask(
        update,
        context,
        [
            types.Part.from_bytes(data=data, mime_type="audio/ogg"),
            "Responde al mensaje de voz (transcríbelo brevemente si es útil).",
        ],
    )


async def on_document(update, context):
    if not allowed(update):
        return
    doc = update.message.document
    if doc.mime_type != "application/pdf":
        return await update.message.reply_text("Por ahora solo acepto PDFs.")
    if doc.file_size and doc.file_size > 15 * 1024 * 1024:
        return await update.message.reply_text("El PDF es demasiado grande (máx. 15 MB).")
    f = await doc.get_file()
    data = bytes(await f.download_as_bytearray())
    caption = update.message.caption or "Resume este documento."
    await ask(update, context, [types.Part.from_bytes(data=data, mime_type="application/pdf"), caption])


class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def start_ping_server():
    # Render (Web Service) exige un puerto abierto; esto lo satisface
    port = os.environ.get("PORT")
    if port:
        server = HTTPServer(("0.0.0.0", int(port)), Ping)
        threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    start_ping_server()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    for name, fn in [
        ("start", start), ("ayuda", ayuda), ("help", ayuda), ("reset", reset),
        ("modelo", modelo), ("buscar", buscar), ("persona", persona),
        ("estado", estado), ("id", mi_id),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    asyncio.set_event_loop(asyncio.new_event_loop())  # necesario en Python 3.14
    app.run_polling()


if __name__ == "__main__":
    main()

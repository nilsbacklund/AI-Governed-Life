import asyncio
import base64
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode

from config import Config

logger = logging.getLogger(__name__)


def setup_telegram(config: Config, queue: asyncio.Queue):
    """Build the Telegram Application with a message handler that pushes to the queue."""
    app = ApplicationBuilder().token(config.telegram_bot_token).build()

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != config.telegram_chat_id:
            return  # ignore messages from other chats

        message = update.message
        if message is None:
            return

        content = []

        if message.text:
            content.append({"type": "text", "text": message.text})

        if message.caption and not message.text:
            content.append({"type": "text", "text": message.caption})

        if message.photo:
            photo_file = await message.photo[-1].get_file()
            image_bytes = await photo_file.download_as_bytearray()
            b64 = base64.b64encode(image_bytes).decode()
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                }
            })

        if message.location:
            content.append({
                "type": "text",
                "text": f"[LOCATION] lat={message.location.latitude}, lon={message.location.longitude}",
            })

        if message.voice:
            content.append({
                "type": "text",
                "text": "[VOICE MESSAGE - transcription not yet supported]",
            })

        if content:
            await queue.put(content)

    app.add_handler(MessageHandler(filters.ALL, handle_message))
    return app


def make_send_fn(app, chat_id: int):
    """Return an async closure for sending messages. Retries without Markdown on parse failure."""

    async def send(text: str | None = None, image_url: str | None = None):
        bot = app.bot
        if image_url:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=text,
                )
        elif text:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                )

    return send

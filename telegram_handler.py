import asyncio
import base64
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode

from config import Config

logger = logging.getLogger(__name__)


def _human_size(nbytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


async def _save_file(file_obj, filename: str, data_dir: Path) -> tuple[str, int]:
    """Download a Telegram file and save to data/inbox/. Returns (relative_path, size_bytes)."""
    inbox = data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    # Avoid overwriting existing files
    target = inbox / filename
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = target.stem
        suffix = target.suffix
        filename = f"{ts}_{stem}{suffix}"
        target = inbox / filename

    file_bytes = await file_obj.download_as_bytearray()
    target.write_bytes(file_bytes)

    rel_path = f"inbox/{filename}"
    return rel_path, len(file_bytes)


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

        if message.document:
            doc = message.document
            file_obj = await doc.get_file()
            filename = doc.file_name or f"document_{doc.file_id}"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            mime = doc.mime_type or "unknown"
            content.append({
                "type": "text",
                "text": f"[FILE RECEIVED] {filename} ({mime}, {_human_size(size)}) saved to {rel_path}",
            })

        if message.audio:
            audio = message.audio
            file_obj = await audio.get_file()
            filename = audio.file_name or f"audio_{audio.file_id}.ogg"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            mime = audio.mime_type or "audio"
            content.append({
                "type": "text",
                "text": f"[FILE RECEIVED] {filename} ({mime}, {_human_size(size)}) saved to {rel_path}",
            })

        if message.voice:
            voice = message.voice
            file_obj = await voice.get_file()
            filename = f"voice_{voice.file_id}.ogg"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            content.append({
                "type": "text",
                "text": f"[VOICE MESSAGE] ({_human_size(size)}) saved to {rel_path}",
            })

        if message.video:
            video = message.video
            file_obj = await video.get_file()
            filename = video.file_name or f"video_{video.file_id}.mp4"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            mime = video.mime_type or "video"
            content.append({
                "type": "text",
                "text": f"[FILE RECEIVED] {filename} ({mime}, {_human_size(size)}) saved to {rel_path}",
            })

        if message.video_note:
            vn = message.video_note
            file_obj = await vn.get_file()
            filename = f"videonote_{vn.file_id}.mp4"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            content.append({
                "type": "text",
                "text": f"[VIDEO NOTE] ({_human_size(size)}) saved to {rel_path}",
            })

        if message.sticker:
            sticker = message.sticker
            content.append({
                "type": "text",
                "text": f"[STICKER] emoji={sticker.emoji or '?'}, set={sticker.set_name or 'unknown'}",
            })

        if message.location:
            content.append({
                "type": "text",
                "text": f"[LOCATION] lat={message.location.latitude}, lon={message.location.longitude}",
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

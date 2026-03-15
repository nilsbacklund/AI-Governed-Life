import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, MessageHandler, MessageReactionHandler, ContextTypes, filters
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

    # Buffer for collecting media group messages (albums)
    _media_groups: dict[str, list[dict]] = {}
    _media_group_tasks: dict[str, asyncio.Task] = {}
    _MEDIA_GROUP_DELAY = 0.5  # seconds to wait for all parts of a media group

    async def _flush_media_group(group_id: str):
        """Wait briefly, then flush all collected parts of a media group to the queue."""
        await asyncio.sleep(_MEDIA_GROUP_DELAY)
        content = _media_groups.pop(group_id, [])
        _media_group_tasks.pop(group_id, None)
        if content:
            await queue.put(content)

    async def _extract_content(message) -> list[dict]:
        """Extract content blocks from a single Telegram message."""
        content = []
        msg_prefix = f"[msg:{message.message_id}] "

        if message.text:
            content.append({"type": "text", "text": msg_prefix + message.text})

        if message.caption and not message.text:
            content.append({"type": "text", "text": msg_prefix + message.caption})

        if message.photo:
            photo_file = await message.photo[-1].get_file()
            filename = f"photo_{message.photo[-1].file_unique_id}.jpg"
            rel_path, size = await _save_file(photo_file, filename, config.data_dir)
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[FILE RECEIVED] {filename} (image/jpeg, {_human_size(size)}) saved to {rel_path}",
            })

        if message.document:
            doc = message.document
            file_obj = await doc.get_file()
            filename = doc.file_name or f"document_{doc.file_id}"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            mime = doc.mime_type or "unknown"
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[FILE RECEIVED] {filename} ({mime}, {_human_size(size)}) saved to {rel_path}",
            })

        if message.audio:
            audio = message.audio
            file_obj = await audio.get_file()
            filename = audio.file_name or f"audio_{audio.file_id}.ogg"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            mime = audio.mime_type or "audio"
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[FILE RECEIVED] {filename} ({mime}, {_human_size(size)}) saved to {rel_path}",
            })

        if message.voice:
            voice = message.voice
            file_obj = await voice.get_file()
            filename = f"voice_{voice.file_id}.ogg"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[VOICE MESSAGE] ({_human_size(size)}) saved to {rel_path}",
            })

        if message.video:
            video = message.video
            file_obj = await video.get_file()
            filename = video.file_name or f"video_{video.file_id}.mp4"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            mime = video.mime_type or "video"
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[FILE RECEIVED] {filename} ({mime}, {_human_size(size)}) saved to {rel_path}",
            })

        if message.video_note:
            vn = message.video_note
            file_obj = await vn.get_file()
            filename = f"videonote_{vn.file_id}.mp4"
            rel_path, size = await _save_file(file_obj, filename, config.data_dir)
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[VIDEO NOTE] ({_human_size(size)}) saved to {rel_path}",
            })

        if message.sticker:
            sticker = message.sticker
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[STICKER] emoji={sticker.emoji or '?'}, set={sticker.set_name or 'unknown'}",
            })

        if message.location:
            content.append({
                "type": "text",
                "text": f"{msg_prefix}[LOCATION] lat={message.location.latitude}, lon={message.location.longitude}",
            })

        return content

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != config.telegram_chat_id:
            return  # ignore messages from other chats

        message = update.message
        if message is None:
            return

        content = await _extract_content(message)
        if not content:
            return

        # Media group (album): buffer parts and flush together after a short delay
        group_id = message.media_group_id
        if group_id:
            _media_groups.setdefault(group_id, []).extend(content)
            # Cancel any existing flush task and restart the timer
            if group_id in _media_group_tasks:
                _media_group_tasks[group_id].cancel()
            _media_group_tasks[group_id] = asyncio.create_task(_flush_media_group(group_id))
            return

        # Single message: put directly in the queue
        await queue.put(content)

    async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
        reaction = update.message_reaction
        if reaction is None or reaction.chat.id != config.telegram_chat_id:
            return

        new_emojis = [r.emoji for r in (reaction.new_reaction or []) if hasattr(r, "emoji")]
        old_emojis = [r.emoji for r in (reaction.old_reaction or []) if hasattr(r, "emoji")]

        added = [e for e in new_emojis if e not in old_emojis]
        removed = [e for e in old_emojis if e not in new_emojis]

        parts = []
        if added:
            parts.append(f"[REACTION] {' '.join(added)} on msg:{reaction.message_id}")
        if removed:
            parts.append(f"[REACTION REMOVED] {' '.join(removed)} from msg:{reaction.message_id}")

        if parts:
            content = [{"type": "text", "text": t} for t in parts]
            await queue.put(content)

    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(MessageReactionHandler(handle_reaction))
    return app


def make_send_fn(app, chat_id: int):
    """Return an async closure for sending messages. Retries without Markdown on parse failure.
    Returns the message_id of the sent message (or None)."""

    async def send(text: str | None = None, image_url: str | None = None):
        bot = app.bot
        sent_msg = None
        if image_url:
            try:
                sent_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                sent_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=text,
                )
        elif text:
            try:
                sent_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                sent_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                )
        return getattr(sent_msg, "message_id", None)

    return send


def make_react_fn(app, chat_id: int):
    """Return an async closure for reacting to messages with emoji."""

    async def react(message_id: int, emoji: str):
        await app.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )

    return react

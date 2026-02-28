import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.constants import ParseMode

from telegram_handler import make_send_fn, _save_file, _human_size


@pytest.fixture
def mock_app():
    app = MagicMock()
    app.bot = AsyncMock()
    return app


class TestSendFn:

    async def test_send_text_with_markdown(self, mock_app):
        send = make_send_fn(mock_app, chat_id=123)
        await send(text="*bold* message")
        mock_app.bot.send_message.assert_awaited_once_with(
            chat_id=123,
            text="*bold* message",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def test_send_text_markdown_fallback(self, mock_app):
        mock_app.bot.send_message = AsyncMock(
            side_effect=[Exception("parse error"), None]
        )
        send = make_send_fn(mock_app, chat_id=123)
        await send(text="bad *markdown")
        assert mock_app.bot.send_message.await_count == 2
        # Second call should NOT have parse_mode
        second_call = mock_app.bot.send_message.await_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    async def test_send_photo_with_caption(self, mock_app):
        send = make_send_fn(mock_app, chat_id=123)
        await send(text="caption", image_url="https://example.com/img.png")
        mock_app.bot.send_photo.assert_awaited_once_with(
            chat_id=123,
            photo="https://example.com/img.png",
            caption="caption",
            parse_mode=ParseMode.MARKDOWN,
        )


class TestFileReceiving:

    async def test_save_file_creates_inbox(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        file_obj = AsyncMock()
        file_obj.download_as_bytearray.return_value = bytearray(b"PDF content here")

        rel_path, size = await _save_file(file_obj, "report.pdf", data_dir)

        assert rel_path == "inbox/report.pdf"
        assert size == 16
        assert (data_dir / "inbox" / "report.pdf").exists()
        assert (data_dir / "inbox" / "report.pdf").read_bytes() == b"PDF content here"

    async def test_save_file_avoids_overwrite(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        inbox = data_dir / "inbox"
        inbox.mkdir()
        # Pre-create a file with the same name
        (inbox / "report.pdf").write_bytes(b"old content")

        file_obj = AsyncMock()
        file_obj.download_as_bytearray.return_value = bytearray(b"new content")

        rel_path, size = await _save_file(file_obj, "report.pdf", data_dir)

        # Should have a timestamped name
        assert rel_path != "inbox/report.pdf"
        assert rel_path.startswith("inbox/")
        assert rel_path.endswith("_report.pdf")
        # Old file should be untouched
        assert (inbox / "report.pdf").read_bytes() == b"old content"

    def test_human_size(self):
        assert _human_size(500) == "500 B"
        assert _human_size(1024) == "1.0 KB"
        assert _human_size(1024 * 1024) == "1.0 MB"
        assert _human_size(1536) == "1.5 KB"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.constants import ParseMode

from telegram_handler import make_send_fn


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

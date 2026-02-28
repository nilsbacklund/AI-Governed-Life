from unittest.mock import AsyncMock, patch

import pytest

from tools import execute_tool


class TestFileOperations:

    async def test_write_and_read_file(self, setup_tools):
        send_fn, config, timer = setup_tools
        result = await execute_tool("write_file", {"path": "test.txt", "content": "hello world"})
        assert result["status"] == "written"

        result = await execute_tool("read_file", {"path": "test.txt"})
        assert result["content"] == "hello world"

    async def test_write_creates_parent_dirs(self, setup_tools):
        send_fn, config, timer = setup_tools
        result = await execute_tool("write_file", {"path": "a/b/c.txt", "content": "nested"})
        assert result["status"] == "written"
        assert (config.data_dir / "a" / "b" / "c.txt").read_text() == "nested"

    async def test_edit_file_success(self, setup_tools):
        send_fn, config, timer = setup_tools
        await execute_tool("write_file", {"path": "edit.txt", "content": "hello world"})
        result = await execute_tool("edit_file", {
            "path": "edit.txt",
            "old_text": "hello",
            "new_text": "goodbye",
        })
        assert result["status"] == "edited"
        result = await execute_tool("read_file", {"path": "edit.txt"})
        assert result["content"] == "goodbye world"

    async def test_edit_file_old_text_not_found(self, setup_tools):
        send_fn, config, timer = setup_tools
        await execute_tool("write_file", {"path": "edit2.txt", "content": "hello"})
        result = await execute_tool("edit_file", {
            "path": "edit2.txt",
            "old_text": "nonexistent",
            "new_text": "x",
        })
        assert result.get("is_error") is True

    async def test_read_file_not_found(self, setup_tools):
        result = await execute_tool("read_file", {"path": "nope.txt"})
        assert result.get("is_error") is True


class TestPathSecurity:

    async def test_path_traversal_blocked(self, setup_tools):
        result = await execute_tool("read_file", {"path": "../../../etc/passwd"})
        assert result.get("is_error") is True
        assert "ValueError" in result.get("error", "")


class TestSendMessage:

    async def test_send_message_text(self, setup_tools):
        send_fn, config, timer = setup_tools
        result = await execute_tool("send_message", {"text": "hi there"})
        assert result["status"] == "sent"
        send_fn.assert_awaited_once_with(text="hi there", image_url=None)

    async def test_send_message_image(self, setup_tools):
        send_fn, config, timer = setup_tools
        result = await execute_tool("send_message", {"image_url": "https://example.com/img.png"})
        assert result["status"] == "sent"
        send_fn.assert_awaited_once_with(text=None, image_url="https://example.com/img.png")

    async def test_send_message_empty_rejected(self, setup_tools):
        result = await execute_tool("send_message", {})
        assert result.get("is_error") is True


class TestExternalAPIs:

    @patch("tools.fetch_weather", new_callable=AsyncMock)
    async def test_get_weather(self, mock_weather, setup_tools):
        send_fn, config, timer = setup_tools
        mock_weather.return_value = {"temp": 15, "condition": "cloudy"}
        result = await execute_tool("get_weather", {})
        assert result["temp"] == 15
        mock_weather.assert_awaited_once_with(51.4416, 5.4697, 12)

    @patch("tools.web_search", new_callable=AsyncMock)
    async def test_web_search(self, mock_search, setup_tools):
        send_fn, config, timer = setup_tools
        mock_search.return_value = [{"title": f"r{i}"} for i in range(10)]
        result = await execute_tool("web_search", {"query": "test query"})
        assert len(result["results"]) == 5
        mock_search.assert_awaited_once_with("test query", "test-tavily-key")

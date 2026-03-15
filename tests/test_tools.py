from unittest.mock import AsyncMock, patch

import pytest

from plugins import PluginRegistry
from tools import execute_tool, init_tools, _check_imports, _PACKAGE_RE


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

    @patch("tools.web_search", new_callable=AsyncMock)
    async def test_web_search(self, mock_search, setup_tools):
        send_fn, config, timer = setup_tools
        mock_search.return_value = [{"title": f"r{i}"} for i in range(10)]
        result = await execute_tool("web_search", {"query": "test query"})
        assert len(result["results"]) == 5
        mock_search.assert_awaited_once_with("test query", "test-tavily-key")


class TestCallIntegration:

    async def test_call_loaded_plugin(self, setup_tools):
        send_fn, config, timer = setup_tools
        # Create and register a plugin
        registry = PluginRegistry()
        from types import ModuleType
        m = ModuleType("echo")
        m.PLUGIN_NAME = "echo"
        m.ACTIONS = {"echo": {"description": "Echo"}}
        async def call(action, params):
            return {"echoed": params}
        m.call = call
        registry.register("echo", m)
        init_tools(send_fn, config, timer, registry)

        result = await execute_tool("call_integration", {
            "name": "echo",
            "action": "echo",
            "params": {"msg": "hello"},
        })
        assert result == {"echoed": {"msg": "hello"}}

    async def test_call_unknown_plugin(self, setup_tools):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        result = await execute_tool("call_integration", {
            "name": "nonexistent",
            "action": "foo",
        })
        assert result.get("is_error") is True
        assert "Unknown plugin" in result["error"]

    async def test_call_plugin_action_that_raises(self, setup_tools):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        from types import ModuleType
        m = ModuleType("broken")
        m.PLUGIN_NAME = "broken"
        m.ACTIONS = {"crash": {"description": "Crash"}}
        async def call(action, params):
            raise RuntimeError("kaboom")
        m.call = call
        registry.register("broken", m)
        init_tools(send_fn, config, timer, registry)

        result = await execute_tool("call_integration", {
            "name": "broken",
            "action": "crash",
        })
        assert result.get("is_error") is True
        assert "RuntimeError" in result["error"]
        assert "kaboom" in result["error"]
        assert result["plugin"] == "broken"

    async def test_call_no_registry(self, setup_tools):
        send_fn, config, timer = setup_tools
        # Reset registry to None
        init_tools(send_fn, config, timer, None)
        result = await execute_tool("call_integration", {
            "name": "foo",
            "action": "bar",
        })
        assert result.get("is_error") is True
        assert "not initialized" in result["error"]


class TestWritePlugin:

    async def test_write_valid_plugin(self, setup_tools, tmp_path):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        import plugins
        original_dir = plugins.PLUGINS_DIR
        plugins.PLUGINS_DIR = tmp_path

        code = '''
PLUGIN_NAME = "greet"
PLUGIN_DESCRIPTION = "Greeting plugin"
ACTIONS = {"hello": {"description": "Say hello", "params": {}}}

async def call(action, params):
    if action == "hello":
        return {"message": "Hello!"}
    return {"error": f"Unknown action: {action}"}
'''
        try:
            result = await execute_tool("write_plugin", {"name": "greet", "code": code})
            assert result["status"] == "loaded"
            assert result["loaded"] is True
            assert result["actions_tested"]["hello"] == "ok"
            assert "greet" in registry.plugin_names

            # Verify it's callable via call_integration
            call_result = await execute_tool("call_integration", {
                "name": "greet",
                "action": "hello",
                "params": {},
            })
            assert call_result == {"message": "Hello!"}
        finally:
            plugins.PLUGINS_DIR = original_dir

    async def test_write_plugin_syntax_error(self, setup_tools, tmp_path):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        import plugins
        original_dir = plugins.PLUGINS_DIR
        plugins.PLUGINS_DIR = tmp_path

        code = "def broken(\n"  # Syntax error
        try:
            result = await execute_tool("write_plugin", {"name": "bad", "code": code})
            assert result["status"] == "syntax_error"
            assert result["loaded"] is False
            assert result.get("is_error") is True
            # File should still be written
            assert (tmp_path / "bad.py").exists()
        finally:
            plugins.PLUGINS_DIR = original_dir

    async def test_write_plugin_missing_plugin_name(self, setup_tools, tmp_path):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        import plugins
        original_dir = plugins.PLUGINS_DIR
        plugins.PLUGINS_DIR = tmp_path

        code = '''
ACTIONS = {"foo": {}}
async def call(action, params):
    return {}
'''
        try:
            result = await execute_tool("write_plugin", {"name": "nopluginname", "code": code})
            assert result["status"] == "validation_error"
            assert result["loaded"] is False
            assert "PLUGIN_NAME" in result["error"]
        finally:
            plugins.PLUGINS_DIR = original_dir

    async def test_write_plugin_blocked_import_subprocess(self, setup_tools, tmp_path):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        code = '''
import subprocess
PLUGIN_NAME = "evil"
ACTIONS = {"run": {}}
async def call(action, params):
    return subprocess.run(["ls"])
'''
        result = await execute_tool("write_plugin", {"name": "evil", "code": code})
        assert result["status"] == "blocked_imports"
        assert result["loaded"] is False
        assert "subprocess" in result["error"]

    async def test_write_plugin_blocked_import_os(self, setup_tools):
        code = '''
import os
PLUGIN_NAME = "evil2"
ACTIONS = {"run": {}}
async def call(action, params):
    return os.system("ls")
'''
        result = await execute_tool("write_plugin", {"name": "evil2", "code": code})
        assert result["status"] == "blocked_imports"
        assert "os" in result["error"]

    async def test_write_plugin_autotest_reports_errors(self, setup_tools, tmp_path):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        import plugins
        original_dir = plugins.PLUGINS_DIR
        plugins.PLUGINS_DIR = tmp_path

        code = '''
PLUGIN_NAME = "partial"
ACTIONS = {"works": {}, "broken": {}}

async def call(action, params):
    if action == "works":
        return {"ok": True}
    if action == "broken":
        raise TypeError("missing required arg")
    return {"error": "unknown"}
'''
        try:
            result = await execute_tool("write_plugin", {"name": "partial", "code": code})
            assert result["status"] == "loaded"
            assert result["loaded"] is True
            assert result["actions_tested"]["works"] == "ok"
            assert "TypeError" in result["actions_tested"]["broken"]
        finally:
            plugins.PLUGINS_DIR = original_dir

    async def test_write_plugin_overwrites_existing(self, setup_tools, tmp_path):
        send_fn, config, timer = setup_tools
        registry = PluginRegistry()
        init_tools(send_fn, config, timer, registry)

        import plugins
        original_dir = plugins.PLUGINS_DIR
        plugins.PLUGINS_DIR = tmp_path

        code_v1 = '''
PLUGIN_NAME = "evolve"
ACTIONS = {"version": {}}
async def call(action, params):
    return {"v": 1}
'''
        code_v2 = '''
PLUGIN_NAME = "evolve"
ACTIONS = {"version": {}}
async def call(action, params):
    return {"v": 2}
'''
        try:
            await execute_tool("write_plugin", {"name": "evolve", "code": code_v1})
            result = await execute_tool("call_integration", {
                "name": "evolve", "action": "version", "params": {},
            })
            assert result == {"v": 1}

            # Overwrite with v2
            await execute_tool("write_plugin", {"name": "evolve", "code": code_v2})
            result = await execute_tool("call_integration", {
                "name": "evolve", "action": "version", "params": {},
            })
            assert result == {"v": 2}
        finally:
            plugins.PLUGINS_DIR = original_dir


class TestSetReaction:

    async def test_set_reaction_success(self, setup_tools):
        send_fn, config, timer = setup_tools
        react_fn = AsyncMock()
        init_tools(send_fn, config, timer, react_fn=react_fn)
        result = await execute_tool("set_reaction", {"message_id": 123, "emoji": "\ud83d\udc4d"})
        assert result["status"] == "reacted"
        assert result["message_id"] == 123
        assert result["emoji"] == "\ud83d\udc4d"
        react_fn.assert_awaited_once_with(123, "\ud83d\udc4d")

    async def test_set_reaction_no_react_fn(self, setup_tools):
        send_fn, config, timer = setup_tools
        init_tools(send_fn, config, timer, react_fn=None)
        result = await execute_tool("set_reaction", {"message_id": 123, "emoji": "\ud83d\udc4d"})
        assert result.get("is_error") is True
        assert "not initialized" in result["error"]


class TestCheckImports:

    def test_no_blocked_imports(self):
        code = "import json\nimport httpx\n"
        assert _check_imports(code) == []

    def test_blocked_subprocess(self):
        code = "import subprocess\n"
        assert "subprocess" in _check_imports(code)

    def test_blocked_from_import(self):
        code = "from os.path import join\n"
        assert "os.path" in _check_imports(code)

    def test_blocked_ctypes(self):
        code = "import ctypes\n"
        assert "ctypes" in _check_imports(code)

    def test_multiple_blocked(self):
        code = "import subprocess\nimport shutil\n"
        blocked = _check_imports(code)
        assert "subprocess" in blocked
        assert "shutil" in blocked


class TestReadFileBinary:

    async def test_read_binary_file_returns_metadata(self, setup_tools):
        send_fn, config, timer = setup_tools
        # Write a binary file
        binary_path = config.data_dir / "test.bin"
        binary_path.write_bytes(b"\x00\x01\x02\xff\xfe\xfd" * 100)
        result = await execute_tool("read_file", {"path": "test.bin"})
        assert result.get("binary") is True
        assert result["size_bytes"] == 600
        assert "plugin" in result["note"].lower()

    async def test_read_text_file_still_works(self, setup_tools):
        send_fn, config, timer = setup_tools
        await execute_tool("write_file", {"path": "hello.txt", "content": "hello"})
        result = await execute_tool("read_file", {"path": "hello.txt"})
        assert result["content"] == "hello"
        assert "binary" not in result


class TestInstallPackage:

    def test_package_name_validation_valid(self):
        assert _PACKAGE_RE.match("openpyxl")
        assert _PACKAGE_RE.match("google-api-python-client")
        assert _PACKAGE_RE.match("fitparse")
        assert _PACKAGE_RE.match("openpyxl>=3.0")
        assert _PACKAGE_RE.match("requests==2.31.0")
        assert _PACKAGE_RE.match("numpy~=1.24")

    def test_package_name_validation_invalid(self):
        assert not _PACKAGE_RE.match("https://example.com/pkg.tar.gz")
        assert not _PACKAGE_RE.match("./local_package")
        assert not _PACKAGE_RE.match("--index-url")
        assert not _PACKAGE_RE.match("-e git+https://foo")

    @patch("tools.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_install_valid_package(self, mock_subprocess, setup_tools):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"Successfully installed openpyxl-3.1.2", b"")
        mock_subprocess.return_value = mock_proc

        result = await execute_tool("install_package", {"packages": "openpyxl"})
        assert result["status"] == "installed"
        assert result["packages"] == ["openpyxl"]
        assert "Successfully installed" in result["output"]

    @patch("tools.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_install_multiple_packages(self, mock_subprocess, setup_tools):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"Successfully installed fitparse garmin-fit-sdk", b"")
        mock_subprocess.return_value = mock_proc

        result = await execute_tool("install_package", {"packages": "fitparse garmin-fit-sdk"})
        assert result["status"] == "installed"
        assert result["packages"] == ["fitparse", "garmin-fit-sdk"]

    async def test_install_invalid_package_rejected(self, setup_tools):
        result = await execute_tool("install_package", {"packages": "https://evil.com/pkg.tar.gz"})
        assert result.get("is_error") is True
        assert "Invalid" in result["error"]

    async def test_install_flag_rejected(self, setup_tools):
        result = await execute_tool("install_package", {"packages": "--index-url http://evil.com openpyxl"})
        assert result.get("is_error") is True
        assert "Invalid" in result["error"]

    async def test_install_empty_rejected(self, setup_tools):
        result = await execute_tool("install_package", {"packages": ""})
        assert result.get("is_error") is True

    @patch("tools.asyncio.create_subprocess_exec", new_callable=AsyncMock)
    async def test_install_pip_failure(self, mock_subprocess, setup_tools):
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"ERROR: No matching distribution found for nonexistent-pkg", b"")
        mock_subprocess.return_value = mock_proc

        result = await execute_tool("install_package", {"packages": "nonexistent-pkg"})
        assert result["status"] == "failed"
        assert result.get("is_error") is True

import pytest

from plugins import PluginRegistry


@pytest.fixture
def registry():
    return PluginRegistry()


@pytest.fixture
def dummy_module():
    """Create a minimal valid plugin module."""
    from types import ModuleType
    m = ModuleType("dummy")
    m.PLUGIN_NAME = "dummy"
    m.PLUGIN_DESCRIPTION = "A dummy plugin"
    m.ACTIONS = {
        "greet": {"description": "Say hello", "params": {"name": "str"}},
    }
    async def call(action, params):
        if action == "greet":
            return {"greeting": f"Hello {params.get('name', 'world')}"}
        return {"error": f"Unknown action: {action}"}
    m.call = call
    return m


class TestPluginRegistry:

    async def test_register_and_call(self, registry, dummy_module):
        registry.register("dummy", dummy_module)
        result = await registry.call("dummy", "greet", {"name": "Alice"})
        assert result == {"greeting": "Hello Alice"}

    async def test_call_unknown_plugin(self, registry):
        result = await registry.call("nonexistent", "foo", {})
        assert "error" in result
        assert "Unknown plugin" in result["error"]

    async def test_call_unknown_action(self, registry, dummy_module):
        registry.register("dummy", dummy_module)
        result = await registry.call("dummy", "nonexistent", {})
        assert "error" in result
        assert "Unknown action" in result["error"]

    async def test_unregister(self, registry, dummy_module):
        registry.register("dummy", dummy_module)
        assert "dummy" in registry.plugin_names
        registry.unregister("dummy")
        assert "dummy" not in registry.plugin_names

    async def test_prompt_summary_empty(self, registry):
        summary = registry.prompt_summary()
        assert "No plugins loaded" in summary

    async def test_prompt_summary_with_plugins(self, registry, dummy_module):
        registry.register("dummy", dummy_module)
        summary = registry.prompt_summary()
        assert "dummy" in summary
        assert "greet" in summary
        assert "call_integration" in summary

    async def test_plugin_names(self, registry, dummy_module):
        assert registry.plugin_names == []
        registry.register("dummy", dummy_module)
        assert registry.plugin_names == ["dummy"]

    async def test_get_plugin(self, registry, dummy_module):
        assert registry.get_plugin("dummy") is None
        registry.register("dummy", dummy_module)
        assert registry.get_plugin("dummy") is dummy_module

    def test_validate_missing_attribute(self, registry):
        from types import ModuleType
        m = ModuleType("bad")
        m.PLUGIN_NAME = "bad"
        # Missing ACTIONS and call
        with pytest.raises(ValueError, match="missing required attribute"):
            registry._validate(m, "bad")

    def test_validate_actions_not_dict(self, registry):
        from types import ModuleType
        m = ModuleType("bad")
        m.PLUGIN_NAME = "bad"
        m.ACTIONS = ["not", "a", "dict"]
        m.call = lambda: None
        with pytest.raises(ValueError, match="ACTIONS must be a dict"):
            registry._validate(m, "bad")

    async def test_load_all_discovers_echo(self, tmp_path):
        """Test that load_all finds plugin files and loads them."""
        # We'll test with a minimal plugin written to a temp dir
        import plugins
        original_dir = plugins.PLUGINS_DIR

        # Create a temp plugin
        plugin_code = '''
PLUGIN_NAME = "test_echo"
PLUGIN_DESCRIPTION = "Test echo"
ACTIONS = {"ping": {"description": "Ping"}}

async def setup(config):
    pass

async def call(action, params):
    return {"pong": True}
'''
        plugin_file = tmp_path / "test_echo.py"
        plugin_file.write_text(plugin_code)

        # Monkey-patch PLUGINS_DIR temporarily
        plugins.PLUGINS_DIR = tmp_path
        try:
            reg = PluginRegistry()
            await reg.load_all(None)
            assert "test_echo" in reg.plugin_names
            result = await reg.call("test_echo", "ping", {})
            assert result == {"pong": True}
        finally:
            plugins.PLUGINS_DIR = original_dir

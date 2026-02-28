import asyncio
import signal
import sys
from zoneinfo import ZoneInfo

from config import load_config
from timer import WakeupTimer
from logger import AgentLogger
from telegram_handler import setup_telegram, make_send_fn
from tools import init_tools
from plugins import PluginRegistry
from agent import Agent


async def main():
    config = load_config()
    tz = ZoneInfo(config.timezone)

    # Ensure directories exist
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)

    # Ensure plugins directory exists
    from plugins import PLUGINS_DIR
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure inbox and secrets directories exist
    (config.data_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (config.data_dir / "secrets").mkdir(parents=True, exist_ok=True)

    # Seed memory.md if it doesn't exist
    memory_file = config.data_dir / "memory.md"
    if not memory_file.exists():
        memory_file.write_text("")

    timer = WakeupTimer(tz)
    queue = asyncio.Queue()
    logger = AgentLogger(config.logs_dir, tz, model=config.model)

    # Load plugins
    plugin_registry = PluginRegistry()
    await plugin_registry.load_all(config)

    # Set up Telegram (manual lifecycle — don't use run_polling which calls asyncio.run)
    telegram_app = setup_telegram(config, queue)
    send_fn = make_send_fn(telegram_app, config.telegram_chat_id)

    # Inject dependencies into tools
    init_tools(send_fn, config, timer, plugin_registry)

    # Create agent
    agent = Agent(config, timer, queue, logger, plugin_registry)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_signal():
        print("\nShutting down...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    # Start Telegram with manual lifecycle
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    print(f"AIBoss running (model={config.model}, tz={config.timezone})")
    print(f"Plugins loaded: {plugin_registry.plugin_names}")
    print("Press Ctrl+C to stop.")

    # Run agent loop alongside shutdown watcher
    agent_task = asyncio.create_task(agent.run())

    await shutdown_event.wait()

    # Cleanup
    agent_task.cancel()
    try:
        await agent_task
    except asyncio.CancelledError:
        pass

    agent.save_history()
    logger.close()

    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()

    print("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())

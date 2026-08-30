import asyncio
import os
import signal
import sys
from collections.abc import Callable
from io import TextIOWrapper
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from ..bot.engine.core import MisskeyBot
from ..shared.banner import BANNER
from ..shared.config import Config
from ..shared.config_keys import ConfigKeys
from ..shared.exceptions import (
    APIConnectionError,
    AuthenticationError,
    ConfigurationError,
)


def _termination_signals() -> tuple[signal.Signals, ...]:
    return (
        (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        if sys.platform != "win32"
        else (signal.SIGINT, signal.SIGTERM)
    )


def _set_termination_handlers(
    handler: Callable[[signal.Signals], None],
) -> None:
    loop = asyncio.get_running_loop()
    for sig in _termination_signals():
        try:
            loop.add_signal_handler(sig, handler, sig)
        except NotImplementedError:
            signal.signal(
                sig,
                lambda received, _: loop.call_soon_threadsafe(
                    handler, signal.Signals(received)
                ),
            )


async def _hold_until_terminated() -> None:
    terminated = asyncio.Event()
    _set_termination_handlers(lambda _: terminated.set())
    await terminated.wait()


class BotRunner:
    def __init__(self):
        self.bot: MisskeyBot | None = None
        self.shutdown_event: asyncio.Event | None = None
        self._shutdown_called = False

    async def run(self) -> None:
        self.shutdown_event = asyncio.Event()
        load_dotenv()
        config = Config()
        config.load()
        log_path = Path(config.get(ConfigKeys.LOG_PATH))
        log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level: <8}</level> | <level>{message}</level>"
        logger.remove()
        logger.add(
            sys.stderr, level=config.get(ConfigKeys.LOG_LEVEL), format=log_format
        )
        logger.add(
            log_path,
            level=config.get(ConfigKeys.LOG_LEVEL),
            format=log_format,
            rotation="10 MB",
            compression="zip",
            enqueue=True,
        )
        print(BANNER)
        logger.info("Starting bot...")
        try:
            self.bot = MisskeyBot(config)
            await self.bot.start()
            self._setup_monitoring_and_signals()
            await self.shutdown_event.wait()
        finally:
            try:
                await asyncio.shield(self.shutdown())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error during shutdown")

    def _setup_monitoring_and_signals(self) -> None:
        def signal_handler(sig: signal.Signals) -> None:
            logger.info(f"Received signal {sig.name}; preparing to shut down...")
            if self.shutdown_event and not self.shutdown_event.is_set():
                self.shutdown_event.set()

        _set_termination_handlers(signal_handler)

    async def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        logger.info("Shutting down bot...")
        if self.bot:
            await self.bot.stop()
        logger.info("Bot shut down")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(errors="replace")
    try:
        asyncio.run(BotRunner().run())
        logger.info("Bye")
        return 0
    except KeyboardInterrupt:
        return 130
    except (ConfigurationError, AuthenticationError) as e:
        logger.error(f"FATAL startup error: {e}")
        if os.environ.get("TWIPSYBOT_HOLD_ON_STARTUP_ERROR") == "1":
            logger.error("Startup suspended until the container is stopped")
            asyncio.run(_hold_until_terminated())
            return 0
        return 2
    except APIConnectionError as e:
        logger.error(f"Startup error: {e}")
        return 4
    except Exception:
        logger.exception("Unhandled exception during startup")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

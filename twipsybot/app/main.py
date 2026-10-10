import asyncio
import math
import signal
import sys
from collections.abc import Callable
from contextlib import suppress
from io import TextIOWrapper

from dotenv import load_dotenv
from loguru import logger

from ..bot.engine.core import Neuro
from ..shared.config import Config
from ..shared.config_keys import ConfigKeys
from ..shared.exceptions import (
    APIConnectionError,
    APIPermissionError,
    AuthenticationError,
    ConfigurationError,
    WebSocketConnectionError,
)
from ..shared.logs import set_log_level, setup_logging
from .banner import BANNER

_RETRY_POLL_SECONDS = 2.0
_RETRY_CONNECT_SECONDS = 60.0
_BLOCKING_ERRORS = (ConfigurationError, AuthenticationError, APIPermissionError)
_CONNECTION_ERRORS = (APIConnectionError, WebSocketConnectionError)


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


class BotRunner:
    def __init__(self):
        self.bot: Neuro | None = None
        self.shutdown_event = asyncio.Event()
        self._shutdown_called = False

    async def run(self) -> None:
        load_dotenv()
        config = Config()
        setup_logging(config.log_path, "INFO")
        print(BANNER)
        self._setup_monitoring_and_signals()
        try:
            if await self._start(config):
                await self.shutdown_event.wait()
        finally:
            try:
                await asyncio.shield(self.shutdown())
            except Exception:
                logger.exception("Error during shutdown")

    async def _start(self, config: Config) -> bool:
        loop = asyncio.get_running_loop()
        while not self.shutdown_event.is_set():
            before = config.stat()
            try:
                config.load()
                set_log_level(config.get(ConfigKeys.SYSTEM_LOG_LEVEL))
                logger.info("Starting bot...")
                self.bot = Neuro(config)
                await self.bot.start()
                return True
            except (*_BLOCKING_ERRORS, *_CONNECTION_ERRORS) as e:
                logger.error(f"Startup blocked: {str(e) or type(e).__name__}")
                retry = isinstance(e, _CONNECTION_ERRORS)
                suffix = f" (retry in {_RETRY_CONNECT_SECONDS:g}s)" if retry else ""
                logger.warning(
                    f"Waiting for `twipsybot cfg` to update settings{suffix}"
                )
                deadline = loop.time() + _RETRY_CONNECT_SECONDS if retry else math.inf
                await self._discard_bot()
            while (
                config.stat() == before
                and loop.time() < deadline
                and not self.shutdown_event.is_set()
            ):
                with suppress(TimeoutError):
                    async with asyncio.timeout(_RETRY_POLL_SECONDS):
                        await self.shutdown_event.wait()
        return False

    async def _discard_bot(self) -> None:
        if self.bot:
            await self.bot.stop()
            self.bot = None

    def _setup_monitoring_and_signals(self) -> None:
        def signal_handler(sig: signal.Signals) -> None:
            logger.info(f"Received signal {sig.name}; preparing to shut down...")
            if not self.shutdown_event.is_set():
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
    except Exception:
        logger.exception("Unhandled exception during startup")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

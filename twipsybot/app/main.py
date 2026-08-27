import asyncio
import signal
import sys
from io import TextIOWrapper
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from ..bot.infra.core import MisskeyBot
from ..shared.banner import BANNER
from ..shared.config import Config
from ..shared.config_keys import ConfigKeys
from ..shared.exceptions import (
    APIConnectionError,
    AuthenticationError,
    ConfigurationError,
)


def _stop_file_path() -> Path:
    return Path("run") / "twipsybot.stop"


def _fatal_file_path() -> Path:
    return Path("run") / "twipsybot.fatal"


def _termination_signals() -> tuple[signal.Signals, ...]:
    return (
        (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        if sys.platform != "win32"
        else (signal.SIGINT, signal.SIGTERM)
    )


class BotRunner:
    def __init__(self):
        self.bot: MisskeyBot | None = None
        self.shutdown_event: asyncio.Event | None = None
        self._stop_file_task: asyncio.Task[None] | None = None
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
        stop_file = _stop_file_path()
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            stop_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._stop_file_task = asyncio.create_task(self._watch_stop_file(stop_file))
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

    async def _watch_stop_file(self, stop_file: Path) -> None:
        while True:
            if self.shutdown_event and self.shutdown_event.is_set():
                return
            try:
                should_stop = stop_file.exists()
            except OSError:
                should_stop = False
            if should_stop:
                try:
                    stop_file.unlink(missing_ok=True)
                except OSError:
                    pass
                if self.shutdown_event and not self.shutdown_event.is_set():
                    self.shutdown_event.set()
                return
            await asyncio.sleep(1.0)

    def _setup_monitoring_and_signals(self) -> None:
        def signal_handler(sig, _):
            logger.info(
                f"Received signal {signal.Signals(sig).name}; preparing to shut down..."
            )
            if self.shutdown_event and not self.shutdown_event.is_set():
                self.shutdown_event.set()
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(lambda: None)
                except RuntimeError:
                    pass

        for sig in _termination_signals():
            try:
                signal.signal(sig, signal_handler)
            except Exception:
                logger.warning(f"Failed to register signal handler: {sig}")

    async def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        if self._stop_file_task:
            self._stop_file_task.cancel()
            self._stop_file_task = None
        logger.info("Shutting down bot...")
        if self.bot:
            await self.bot.stop()
        logger.info("Bot shut down")


async def _wait_for_termination() -> int:
    stop = asyncio.Event()
    received = signal.SIGTERM

    def handler(sig, _):
        nonlocal received
        received = sig
        stop.set()

    for sig in _termination_signals():
        try:
            signal.signal(sig, handler)
        except Exception:
            pass
    await stop.wait()
    return received


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
        fatal_file = _fatal_file_path()
        try:
            fatal_file.parent.mkdir(parents=True, exist_ok=True)
            fatal_file.write_text(str(e), encoding="utf-8")
        except OSError:
            pass
        return 128 + asyncio.run(_wait_for_termination())
    except APIConnectionError as e:
        logger.error(f"Startup error: {e}")
        return 4
    except Exception:
        logger.exception("Unhandled exception during startup")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

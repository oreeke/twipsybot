import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

from .bot import MisskeyBot
from .config import Config
from .constants import ConfigKeys
from .exceptions import APIConnectionError, AuthenticationError, ConfigurationError


class BotRunner:
    def __init__(self):
        self.bot: Optional[MisskeyBot] = None
        self.shutdown_event: Optional[asyncio.Event] = None
        self._shutdown_called = False

    async def run(self) -> None:
        self.shutdown_event = asyncio.Event()
        load_dotenv()
        config = Config()
        await config.load()
        log_path = Path(config.get(ConfigKeys.LOG_PATH))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_path, level=config.get(ConfigKeys.LOG_LEVEL))
        logger.info("启动机器人...")
        try:
            self.bot = MisskeyBot(config)
            await self.bot.start()
            await self._setup_monitoring_and_signals()
            await self.shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        except (
            ConfigurationError,
            APIConnectionError,
            AuthenticationError,
            OSError,
            ValueError,
        ) as e:
            logger.error(f"启动过程中发生错误: {e}")
            raise
        finally:
            await self.shutdown()
            logger.info("再见~")

    async def _setup_monitoring_and_signals(self) -> None:
        signals = (
            (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
            if sys.platform != "win32"
            else (signal.SIGINT, signal.SIGTERM)
        )

        def signal_handler(sig, _):
            logger.info(f"收到信号 {signal.Signals(sig).name}，准备关闭...")
            if self.shutdown_event and not self.shutdown_event.is_set():
                self.shutdown_event.set()
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(lambda: None)
                except RuntimeError:
                    pass

        for sig in signals:
            try:
                signal.signal(sig, signal_handler)
            except (OSError, ValueError, NotImplementedError):
                logger.warning(f"无法注册信号处理器: {sig}")

    async def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        logger.info("关闭机器人...")
        if self.bot:
            await self.bot.stop()
        logger.info("机器人已关闭")


def main() -> None:
    runner = BotRunner()
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        try:
            asyncio.run(runner.shutdown())
        except (OSError, ValueError, TypeError) as e:
            print(f"关闭时出错: {e}")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        ImportError,
        ConfigurationError,
        APIConnectionError,
        AuthenticationError,
    ) as e:
        print(f"启动时出错: {e}")
        try:
            asyncio.run(runner.shutdown())
        except (OSError, ValueError, TypeError) as shutdown_error:
            print(f"关闭时出错: {shutdown_error}")


if __name__ == "__main__":
    main()

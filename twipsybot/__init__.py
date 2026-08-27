import ssl
from importlib import import_module
from typing import Any


def _patch_broken_windows_cert_store() -> None:
    original = ssl.SSLContext.load_default_certs

    def load_default_certs(
        self: ssl.SSLContext, purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH
    ) -> None:
        try:
            original(self, purpose)
        except ssl.SSLError:
            import certifi

            self.load_verify_locations(cafile=certifi.where())

    ssl.SSLContext.load_default_certs = load_default_certs


_patch_broken_windows_cert_store()

_PLUGIN_MODULE = ".plugin"

_EXPORTS: dict[str, tuple[str, str]] = {
    "MisskeyBot": (".bot.infra.core", "MisskeyBot"),
    "BotRunner": (".app.main", "BotRunner"),
    "BotRuntime": (".bot.infra.runtime", "BotRuntime"),
    "Config": (".shared.config", "Config"),
    "ConfigKeys": (".shared.config_keys", "ConfigKeys"),
    "MisskeyAPI": (".clients.misskey.misskey_api", "MisskeyAPI"),
    "MisskeyDrive": (".clients.misskey.drive", "MisskeyDrive"),
    "OpenAIAPI": (".clients.openai.openai_api", "OpenAIAPI"),
    "StreamingClient": (".clients.misskey.streaming", "StreamingClient"),
    "ChannelType": (".clients.misskey.channels", "ChannelType"),
    "DBManager": (".db.sqlite", "DBManager"),
    "ConnectionPool": (".db.sqlite", "ConnectionPool"),
    "PluginBase": (_PLUGIN_MODULE, "PluginBase"),
    "PluginContext": (_PLUGIN_MODULE, "PluginContext"),
    "PluginManager": (_PLUGIN_MODULE, "PluginManager"),
    "TCPClient": (".clients.misskey.transport", "TCPClient"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(name) from None
    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))

from __future__ import annotations

from pathlib import Path

T2X_SOURCE = (Path(__file__).parents[2] / "plugins" / "t2x" / "plugin.py").read_text(
    encoding="utf-8"
)

CONTEXT_PLUGIN = (
    "async def on_context(self, event):\n"
    "    return {'context': 'CTX', 'text': event.text.replace('/web ', '', 1)}\n"
)

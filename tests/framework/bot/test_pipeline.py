from __future__ import annotations

from twipsybot.bot.engine.pipeline import Augment, Toolbox

_TOOLS = Toolbox(None, {})


def test_augment_content_builds_multimodal_input() -> None:
    part = {"type": "input_image", "image_url": "u"}

    assert Augment(_TOOLS).content(" hi ") == "hi"
    assert Augment(_TOOLS, media=(part,)).content("hi") == [
        {"type": "input_text", "text": "hi"},
        part,
    ]
    assert Augment(_TOOLS, media=(part,)).content(" ") == [part]

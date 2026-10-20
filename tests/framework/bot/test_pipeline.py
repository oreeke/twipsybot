from __future__ import annotations

from twipsybot.bot.engine.pipeline import Augment


def test_augment_content_builds_multimodal_input() -> None:
    part = {"type": "input_image", "image_url": "u"}

    assert Augment().content(" hi ") == "hi"
    assert Augment(media=(part,)).content("hi") == [
        {"type": "input_text", "text": "hi"},
        part,
    ]
    assert Augment(media=(part,)).content(" ") == [part]

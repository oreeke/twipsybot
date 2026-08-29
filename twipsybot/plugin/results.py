from typing import Literal, NotRequired, TypedDict

__all__ = ("AutoPostResult", "HandledResult", "PromptModificationResult")


class HandledResult(TypedDict):
    handled: Literal[True]
    response: str


class AutoPostResult(TypedDict):
    contents: list[str]
    visibility: NotRequired[Literal["public", "home", "followers"]]


class PromptModificationResult(TypedDict):
    prompt: str
    timestamp: NotRequired[int]

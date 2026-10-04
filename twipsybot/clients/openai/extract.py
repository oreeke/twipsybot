from collections.abc import Iterator
from typing import Any

from ...shared.exceptions import APIConnectionError


def extract_responses_text(response: Any) -> str:
    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            raise APIConnectionError("Response truncated: max_output_tokens reached")
    if isinstance((text := getattr(response, "output_text", None)), str) and text:
        return text
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise APIConnectionError("Invalid output type")
    if not (parts := list(_output_texts(output))):
        raise APIConnectionError("Empty output")
    return "".join(parts)


def _output_texts(output: list[Any]) -> Iterator[str]:
    for item in output:
        content = getattr(item, "content", None)
        if getattr(item, "type", None) != "message" or not isinstance(content, list):
            continue
        for c in content:
            if getattr(c, "type", None) == "output_text" and (
                isinstance(text := getattr(c, "text", None), str) and text
            ):
                yield text


def process_chat_completions_response(response: Any, call_type: str) -> str:
    choice = response.choices[0]
    generated_text = choice.message.content
    if not generated_text:
        raise APIConnectionError()
    if getattr(choice, "finish_reason", None) == "length":
        raise APIConnectionError(f"{call_type} response truncated: max_tokens reached")
    return generated_text

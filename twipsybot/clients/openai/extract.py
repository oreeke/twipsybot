from typing import Any

from loguru import logger

from ...shared.exceptions import APIConnectionError


def extract_responses_text(response: Any) -> str:
    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            raise APIConnectionError("Response truncated: max_output_tokens reached")
    if isinstance((text := getattr(response, "output_text", None)), str) and text:
        return text
    parts = collect_responses_output_text(getattr(response, "output", None))
    if not parts:
        raise APIConnectionError("Empty output")
    return "".join(parts)


def collect_responses_output_text(output: Any) -> list[str]:
    if not isinstance(output, list):
        raise APIConnectionError("Invalid output type")
    return list(iter_responses_output_text(output))


def iter_responses_output_text(output: list[Any]):
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        yield from iter_responses_message_content(getattr(item, "content", None))


def iter_responses_message_content(content: Any):
    if not isinstance(content, list):
        return
    for c in content:
        if (
            getattr(c, "type", None) == "output_text"
            and isinstance((t := getattr(c, "text", None)), str)
            and t
        ):
            yield t


def process_chat_completions_response(response: Any, call_type: str) -> str:
    choice = response.choices[0]
    generated_text = choice.message.content
    if not generated_text:
        raise APIConnectionError()
    if getattr(choice, "finish_reason", None) == "length":
        raise APIConnectionError(f"{call_type} response truncated: max_tokens reached")
    logger.debug(
        f"OpenAI API {call_type} call succeeded; output length: {len(generated_text)}"
    )
    return generated_text

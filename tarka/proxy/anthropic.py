"""Translation helpers for the non-streaming Anthropic Messages API subset."""

import json
from collections.abc import Mapping


class AnthropicProtocolError(ValueError):
    """Raised when an Anthropic Messages request or response is unsupported."""


def to_openai_request(request: Mapping[str, object]) -> dict[str, object]:
    """Translate a supported Anthropic Messages request to chat completions."""
    model = request.get("model")
    max_tokens = request.get("max_tokens")
    raw_messages = request.get("messages")
    if not isinstance(model, str) or not model:
        raise AnthropicProtocolError("The Anthropic request requires a model.")
    if type(max_tokens) is not int or max_tokens <= 0:
        raise AnthropicProtocolError("The Anthropic request requires positive max_tokens.")
    if not isinstance(raw_messages, list):
        raise AnthropicProtocolError("The Anthropic request requires a messages array.")

    messages: list[dict[str, object]] = []
    system = request.get("system")
    if system is not None:
        if not isinstance(system, str):
            raise AnthropicProtocolError("Only string system prompts are supported.")
        messages.append({"role": "system", "content": system})
    for index, message in enumerate(raw_messages):
        if not isinstance(message, Mapping) or message.get("role") not in ("user", "assistant"):
            raise AnthropicProtocolError(
                f"Anthropic message {index} must have user or assistant role."
            )
        _append_message_content(messages, message["role"], message.get("content"), index)

    translated: dict[str, object] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    for name in ("temperature", "top_p", "stream", "logic_constraints"):
        if name in request:
            translated[name] = request[name]
    stream = request.get("stream", False)
    if type(stream) is not bool:
        raise AnthropicProtocolError("stream must be a boolean.")
    stop_sequences = request.get("stop_sequences")
    if stop_sequences is not None:
        if not isinstance(stop_sequences, list) or not all(
            isinstance(sequence, str) for sequence in stop_sequences
        ):
            raise AnthropicProtocolError("stop_sequences must be a list of strings.")
        translated["stop"] = stop_sequences

    tools = request.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise AnthropicProtocolError("tools must be an array.")
        translated["tools"] = [_translate_tool(tool, index) for index, tool in enumerate(tools)]
    tool_choice = request.get("tool_choice")
    if tool_choice is not None:
        translated["tool_choice"] = _translate_tool_choice(tool_choice)
    return translated


def from_openai_response(
    completion: Mapping[str, object],
    *,
    requested_model: str,
) -> dict[str, object]:
    """Translate one OpenAI chat completion to an Anthropic message response."""
    choices = completion.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise AnthropicProtocolError("The upstream response must contain exactly one choice.")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise AnthropicProtocolError("The upstream response has no assistant message.")

    blocks: list[dict[str, object]] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    elif content is not None and not isinstance(content, str):
        raise AnthropicProtocolError("The upstream assistant content must be a string or null.")

    tool_calls = message.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        raise AnthropicProtocolError("The upstream tool_calls field must be an array.")
    for index, tool_call in enumerate(tool_calls):
        if not isinstance(tool_call, Mapping):
            raise AnthropicProtocolError(f"Upstream tool call {index} must be an object.")
        function = tool_call.get("function")
        if not isinstance(function, Mapping):
            raise AnthropicProtocolError(f"Upstream tool call {index} has no function.")
        call_id = tool_call.get("id")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(call_id, str) or not isinstance(name, str):
            raise AnthropicProtocolError(f"Upstream tool call {index} is missing id or name.")
        if not isinstance(arguments, str):
            raise AnthropicProtocolError(f"Upstream tool call {index} arguments must be JSON text.")
        try:
            input_value = json.loads(arguments)
        except ValueError as exc:
            raise AnthropicProtocolError(
                f"Upstream tool call {index} arguments are not valid JSON."
            ) from exc
        if not isinstance(input_value, Mapping):
            raise AnthropicProtocolError(f"Upstream tool call {index} input must be an object.")
        blocks.append(
            {"type": "tool_use", "id": call_id, "name": name, "input": dict(input_value)}
        )

    finish_reason = choices[0].get("finish_reason")
    stop_reason = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
    }.get(finish_reason)
    if stop_reason is None:
        raise AnthropicProtocolError(
            f"Unsupported upstream finish_reason for Anthropic translation: {finish_reason!r}."
        )
    response_id = completion.get("id")
    if not isinstance(response_id, str):
        raise AnthropicProtocolError("The upstream response is missing its completion id.")
    response_model = completion.get("model", requested_model)
    if not isinstance(response_model, str):
        raise AnthropicProtocolError("The upstream response model must be a string.")

    translated: dict[str, object] = {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "model": response_model,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
    }
    usage = completion.get("usage")
    if isinstance(usage, Mapping):
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        if (
            type(input_tokens) is int
            and input_tokens >= 0
            and type(output_tokens) is int
            and output_tokens >= 0
        ):
            translated["usage"] = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
    return translated


def _append_message_content(
    messages: list[dict[str, object]],
    role: str,
    content: object,
    message_index: int,
) -> None:
    if isinstance(content, str):
        messages.append({"role": role, "content": content})
        return
    if not isinstance(content, list):
        raise AnthropicProtocolError(
            f"Anthropic message {message_index} content must be a string or array."
        )

    text_parts: list[str] = []
    tool_calls: list[dict[str, object]] = []
    tool_results: list[dict[str, object]] = []
    for block_index, block in enumerate(content):
        if not isinstance(block, Mapping):
            raise AnthropicProtocolError(
                f"Anthropic message {message_index} block {block_index} must be an object."
            )
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif role == "assistant" and block_type == "tool_use":
            tool_id = block.get("id")
            name = block.get("name")
            input_value = block.get("input")
            if (
                not isinstance(tool_id, str)
                or not isinstance(name, str)
                or not isinstance(input_value, Mapping)
            ):
                raise AnthropicProtocolError(
                    f"Anthropic tool_use block {block_index} requires id, name, and object input."
                )
            tool_calls.append(
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(input_value)},
                }
            )
        elif role == "user" and block_type == "tool_result":
            tool_id = block.get("tool_use_id")
            result_content = block.get("content")
            if not isinstance(tool_id, str) or not isinstance(result_content, str):
                raise AnthropicProtocolError(
                    f"Anthropic tool_result block {block_index} requires a string tool_use_id "
                    "and string content."
                )
            tool_results.append(
                {"role": "tool", "tool_call_id": tool_id, "content": result_content}
            )
        else:
            raise AnthropicProtocolError(
                f"Unsupported Anthropic content block {block_type!r} in message {message_index}."
            )

    if role == "user" and text_parts and tool_results:
        raise AnthropicProtocolError(
            f"Anthropic message {message_index} may not mix text and tool_result blocks."
        )
    if text_parts or tool_calls or role == "user" and not tool_results:
        message: dict[str, object] = {
            "role": role,
            "content": "\n".join(text_parts) if text_parts else None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
    messages.extend(tool_results)


def _translate_tool(tool: object, index: int) -> dict[str, object]:
    if not isinstance(tool, Mapping):
        raise AnthropicProtocolError(f"Anthropic tool {index} must be an object.")
    name = tool.get("name")
    input_schema = tool.get("input_schema")
    if not isinstance(name, str) or not name or not isinstance(input_schema, Mapping):
        raise AnthropicProtocolError(
            f"Anthropic tool {index} requires a name and object input_schema."
        )
    function: dict[str, object] = {"name": name, "parameters": dict(input_schema)}
    description = tool.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise AnthropicProtocolError(f"Anthropic tool {index} description must be a string.")
        function["description"] = description
    return {"type": "function", "function": function}


def _translate_tool_choice(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AnthropicProtocolError("tool_choice must be an object.")
    choice_type = value.get("type")
    if choice_type == "auto":
        return {"type": "auto"}
    if choice_type == "any":
        return {"type": "required"}
    if choice_type == "tool":
        name = value.get("name")
        if not isinstance(name, str) or not name:
            raise AnthropicProtocolError("A named tool_choice requires a tool name.")
        return {"type": "function", "function": {"name": name}}
    raise AnthropicProtocolError("tool_choice type must be auto, any, or tool.")

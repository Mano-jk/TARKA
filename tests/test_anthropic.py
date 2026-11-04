import pytest

from tarka.proxy.anthropic import (
    AnthropicProtocolError,
    from_openai_response,
    to_openai_request,
)


def test_anthropic_request_maps_system_tools_and_tool_results() -> None:
    translated = to_openai_request(
        {
            "model": "claude-compatible",
            "max_tokens": 64,
            "system": "Use the declared tools.",
            "messages": [
                {"role": "user", "content": "Update this order."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "authorize",
                            "input": {"order_id": "o-1"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": "authorized",
                        }
                    ],
                },
            ],
            "tools": [
                {
                    "name": "authorize",
                    "description": "Authorize the order.",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": {"type": "tool", "name": "authorize"},
            "logic_constraints": {"rules": ["amount <= limit"]},
        }
    )

    messages = translated["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {"role": "system", "content": "Use the declared tools."}
    assert messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "authorize",
                    "arguments": '{"order_id": "o-1"}',
                },
            }
        ],
    }
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "authorized",
    }
    assert translated["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "authorize",
                "description": "Authorize the order.",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert translated["tool_choice"] == {
        "type": "function",
        "function": {"name": "authorize"},
    }
    assert translated["logic_constraints"] == {"rules": ["amount <= limit"]}


def test_anthropic_response_maps_text_and_tool_call() -> None:
    translated = from_openai_response(
        {
            "id": "chatcmpl-1",
            "model": "upstream-model",
            "choices": [
                {
                    "message": {
                        "content": "Review complete.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "authorize",
                                    "arguments": '{"order_id":"o-1"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        },
        requested_model="client-model",
    )

    assert translated == {
        "id": "chatcmpl-1",
        "type": "message",
        "role": "assistant",
        "model": "upstream-model",
        "content": [
            {"type": "text", "text": "Review complete."},
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "authorize",
                "input": {"order_id": "o-1"},
            },
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }


def test_anthropic_translation_rejects_unsupported_content_without_dropping_it() -> None:
    with pytest.raises(AnthropicProtocolError, match="Unsupported Anthropic content block"):
        to_openai_request(
            {
                "model": "client-model",
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image", "source": {"type": "base64"}}],
                    }
                ],
            }
        )


def test_anthropic_translation_rejects_malformed_stream_and_mixed_tool_results() -> None:
    with pytest.raises(AnthropicProtocolError, match="stream must be a boolean"):
        to_openai_request(
            {
                "model": "client-model",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "Check this."}],
                "stream": "false",
            }
        )

    with pytest.raises(AnthropicProtocolError, match="may not mix text"):
        to_openai_request(
            {
                "model": "client-model",
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Result:"},
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-1",
                                "content": "done",
                            },
                        ],
                    }
                ],
            }
        )

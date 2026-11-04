import asyncio

import pytest

from tarka import ToolValidationError, guard_tool


def test_guarded_tool_blocks_invalid_arguments_before_side_effects() -> None:
    calls: list[int] = []

    @guard_tool(rules=["amount <= limit"], bindings={"limit": 10})
    def charge(amount: int) -> str:
        calls.append(amount)
        return "charged"

    with pytest.raises(ToolValidationError, match="violate declared rules") as error:
        charge(20)

    assert "20" not in str(error.value)
    assert calls == []
    assert charge(10) == "charged"
    assert calls == [10]


def test_guarded_tool_validates_schema_and_fixed_bindings() -> None:
    calls: list[dict[str, object]] = []

    @guard_tool(
        schema={
            "type": "object",
            "properties": {"account_id": {"type": "string"}, "amount": {"type": "number"}},
            "required": ["account_id", "amount"],
            "additionalProperties": False,
        },
        bindings={"account_id": "acct-1"},
    )
    def transfer(account_id: str, amount: float) -> None:
        calls.append({"account_id": account_id, "amount": amount})

    with pytest.raises(ToolValidationError, match="fixed binding"):
        transfer("acct-2", 5)
    with pytest.raises(ToolValidationError, match="JSON Schema"):
        transfer("acct-1", "5")

    assert calls == []
    transfer("acct-1", 5)
    assert calls == [{"account_id": "acct-1", "amount": 5}]


def test_guarded_async_tool_validates_before_awaiting_function() -> None:
    calls: list[int] = []

    @guard_tool(schema={"type": "object", "properties": {"amount": {"maximum": 10}}})
    async def execute(amount: int) -> str:
        calls.append(amount)
        return "done"

    async def run() -> str:
        with pytest.raises(ToolValidationError, match="JSON Schema"):
            await execute(11)
        return await execute(10)

    assert asyncio.run(run()) == "done"
    assert calls == [10]


def test_guard_tool_requires_explicit_constraints() -> None:
    with pytest.raises(ValueError, match="requires at least one"):

        @guard_tool
        def unsafe_action(amount: int) -> None:
            pass

        unsafe_action


def test_guarded_tool_rejects_cyclic_and_non_finite_arguments() -> None:
    @guard_tool(schema={"type": "object"})
    def inspect_payload(payload: object) -> None:
        raise AssertionError("Invalid arguments must not reach the tool.")

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ToolValidationError, match="cycles"):
        inspect_payload(cyclic)
    with pytest.raises(ToolValidationError, match="finite"):
        inspect_payload(float("nan"))

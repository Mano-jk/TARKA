"""Bounded JSON Schema validation helpers safe for untrusted contracts."""

from collections.abc import Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.protocols import Validator

_MAX_SCHEMA_NODES = 10_000
_MAX_SCHEMA_DEPTH = 64


def build_schema_validator(schema: object) -> Validator:
    """Validate a bounded Draft 2020-12 schema without network reference loading."""
    if not isinstance(schema, Mapping) and type(schema) is not bool:
        raise ValueError("JSON Schema must be an object or boolean.")
    _check_schema_complexity(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"JSON Schema is invalid: {exc.message}") from exc
    return Draft202012Validator(schema)


def _check_schema_complexity(schema: object) -> None:
    pending: list[tuple[object, int, bool]] = [(schema, 0, False)]
    active_containers: set[int] = set()
    nodes = 0

    while pending:
        value, depth, exiting = pending.pop()
        identity = id(value)
        if exiting:
            active_containers.remove(identity)
            continue
        nodes += 1
        if nodes > _MAX_SCHEMA_NODES:
            raise ValueError(f"JSON Schema may not exceed {_MAX_SCHEMA_NODES} values.")
        if depth > _MAX_SCHEMA_DEPTH:
            raise ValueError(f"JSON Schema may not exceed {_MAX_SCHEMA_DEPTH} nesting levels.")
        if isinstance(value, Mapping):
            if identity in active_containers:
                raise ValueError("JSON Schema must not contain cyclic mappings.")
            active_containers.add(identity)
            if "$ref" in value or "$dynamicRef" in value:
                raise ValueError(
                    "JSON Schema references are disabled; use an inlined schema or OpenAPI "
                    "with local references."
                )
            pending.append((value, depth, True))
            pending.extend((child, depth + 1, False) for child in value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if identity in active_containers:
                raise ValueError("JSON Schema must not contain cyclic arrays.")
            active_containers.add(identity)
            pending.append((value, depth, True))
            pending.extend((child, depth + 1, False) for child in value)

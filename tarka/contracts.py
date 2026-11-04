"""Helpers for extracting bounded JSON contracts from OpenAPI documents."""

from collections.abc import Mapping

_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_MAX_REFERENCE_EXPANSIONS = 256
_MAX_REFERENCE_DEPTH = 64


class ContractError(ValueError):
    """Raised when an OpenAPI document cannot provide a supported plan contract."""


def _resolve_local_references(document: Mapping[str, object], value: object) -> object:
    expansions = 0

    def resolve(current: object, stack: tuple[str, ...], depth: int) -> object:
        nonlocal expansions
        if depth > _MAX_REFERENCE_DEPTH:
            raise ContractError("OpenAPI schema references exceed the nesting limit.")
        if isinstance(current, Mapping):
            reference = current.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    raise ContractError("Only local OpenAPI JSON Pointer references are supported.")
                if reference in stack:
                    raise ContractError("Recursive OpenAPI schema references are not supported.")
                expansions += 1
                if expansions > _MAX_REFERENCE_EXPANSIONS:
                    raise ContractError("OpenAPI schema references exceed the expansion limit.")
                target: object = document
                for encoded_part in reference[2:].split("/"):
                    if "~" in encoded_part.replace("~0", "").replace("~1", ""):
                        raise ContractError("The OpenAPI schema contains an invalid JSON Pointer.")
                    part = encoded_part.replace("~1", "/").replace("~0", "~")
                    if not isinstance(target, Mapping) or part not in target:
                        raise ContractError(f"OpenAPI reference {reference!r} does not exist.")
                    target = target[part]
                resolved = resolve(target, (*stack, reference), depth + 1)
                siblings = {key: item for key, item in current.items() if key != "$ref"}
                if not siblings:
                    return resolved
                return {
                    "allOf": [
                        resolved,
                        resolve(siblings, stack, depth + 1),
                    ]
                }
            return {key: resolve(item, stack, depth + 1) for key, item in current.items()}
        if isinstance(current, list):
            return [resolve(item, stack, depth + 1) for item in current]
        return current

    return resolve(value, (), 0)


def operation_request_schema(
    document: Mapping[str, object],
    operation_id: str,
) -> object:
    """Extract and inline the JSON request-body schema for one OpenAPI operation."""
    if document.get("openapi") not in ("3.1.0", "3.1.1"):
        raise ContractError("Only OpenAPI 3.1.0 and 3.1.1 documents are supported.")
    if not operation_id:
        raise ContractError("An OpenAPI operation_id is required.")

    matches: list[Mapping[str, object]] = []
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise ContractError("The OpenAPI document must contain a paths object.")
    for path_item in paths.values():
        if not isinstance(path_item, Mapping):
            continue
        for method, operation in path_item.items():
            if (
                isinstance(method, str)
                and method.lower() in _HTTP_METHODS
                and isinstance(operation, Mapping)
                and operation.get("operationId") == operation_id
            ):
                matches.append(operation)

    if not matches:
        raise ContractError(f"OpenAPI operation_id {operation_id!r} was not found.")
    if len(matches) != 1:
        raise ContractError(f"OpenAPI operation_id {operation_id!r} is not unique.")

    request_body = _resolve_local_references(document, matches[0].get("requestBody"))
    if not isinstance(request_body, Mapping):
        raise ContractError(
            f"OpenAPI operation {operation_id!r} has no supported requestBody."
        )
    content = request_body.get("content")
    if not isinstance(content, Mapping):
        raise ContractError(
            f"OpenAPI operation {operation_id!r} has no requestBody content."
        )
    media_type = content.get("application/json")
    if not isinstance(media_type, Mapping):
        media_type = next(
            (
                value
                for key, value in content.items()
                if isinstance(key, str)
                and key.endswith("+json")
                and isinstance(value, Mapping)
            ),
            None,
        )
    if not isinstance(media_type, Mapping) or "schema" not in media_type:
        raise ContractError(
            f"OpenAPI operation {operation_id!r} has no JSON request-body schema."
        )

    return _resolve_local_references(document, media_type["schema"])

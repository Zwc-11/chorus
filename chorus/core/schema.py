"""Small JSON-schema subset for step-boundary checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    field: str
    expected: str
    got: str


_TYPE_MAP = {
    "array": list,
    "boolean": bool,
    "integer": int,
    "null": type(None),
    "number": (int, float),
    "object": dict,
    "string": str,
}


def validate_json_schema(value: Any, schema: dict[str, Any] | None) -> list[SchemaIssue]:
    """Validate a practical JSON-schema subset used by Chorus contracts."""

    if not schema:
        return []
    return _validate(value, schema, "$")


def _validate(value: Any, schema: dict[str, Any], field: str) -> list[SchemaIssue]:
    issues: list[SchemaIssue] = []
    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        py_type = _TYPE_MAP.get(expected_type)
        if py_type is not None and not isinstance(value, py_type):
            return [SchemaIssue(field=field, expected=expected_type, got=type(value).__name__)]

    if expected_type == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return [SchemaIssue(field=field, expected="object", got=type(value).__name__)]
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                issues.append(
                    SchemaIssue(field=f"{field}.{key}", expected="present", got="missing")
                )
        properties = schema.get("properties", {})
        for key, subschema in properties.items():
            if key in value and isinstance(subschema, dict):
                issues.extend(_validate(value[key], subschema, f"{field}.{key}"))
    return issues

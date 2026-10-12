"""A deliberately small JSON-Schema subset for tool arguments.

Tool arguments proposed by a language model are untrusted. Every tool that the agent may
call declares an explicit schema (``ToolSpec.parameters``); arguments are checked against
it *before* the registry sees them, and the tool itself validates them again.

Supported keywords (anything else is rejected when the catalog is built, so a schema can
never claim a constraint that is not enforced):

* ``object``: ``properties``, ``required``, ``additionalProperties`` (must be ``false``),
  ``description``
* ``string``: ``minLength``, ``maxLength`` (required), ``pattern`` (full match), ``enum``
* ``integer``: ``minimum``, ``maximum``, ``enum`` (``true``/``false`` are not integers)
* ``boolean``

No dependency is needed for this; ``jsonschema`` would be far larger than the subset used.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_COMMON = {"type", "description"}
_ALLOWED = {
    "object": _COMMON | {"properties", "required", "additionalProperties"},
    "string": _COMMON | {"minLength", "maxLength", "pattern", "enum"},
    "integer": _COMMON | {"minimum", "maximum", "enum"},
    "boolean": _COMMON,
}
MAX_ERRORS = 5


class SchemaDefinitionError(ValueError):
    """A tool declared a schema that uses unsupported or unsafe keywords."""


def check_schema(schema: Mapping[str, Any], path: str = "$") -> None:
    """Raise :class:`SchemaDefinitionError` unless ``schema`` only uses the supported subset."""
    if not isinstance(schema, Mapping):
        raise SchemaDefinitionError(f"{path}: schema must be an object")
    kind = schema.get("type")
    if kind not in _ALLOWED:
        raise SchemaDefinitionError(f"{path}: unsupported type {kind!r}")
    unknown = set(schema) - _ALLOWED[kind]
    if unknown:
        raise SchemaDefinitionError(f"{path}: unsupported keywords {sorted(unknown)}")
    if kind == "object":
        if schema.get("additionalProperties", None) is not False:
            raise SchemaDefinitionError(f"{path}: objects must set additionalProperties: false")
        props = schema.get("properties", {})
        if not isinstance(props, Mapping):
            raise SchemaDefinitionError(f"{path}: properties must be an object")
        for name, sub in props.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", name):
                raise SchemaDefinitionError(f"{path}: invalid property name {name!r}")
            if sub.get("type") == "object":
                raise SchemaDefinitionError(f"{path}.{name}: nested objects are not supported")
            check_schema(sub, f"{path}.{name}")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(r not in props for r in required):
            raise SchemaDefinitionError(f"{path}: required lists unknown properties")
    elif kind == "string":
        if not isinstance(schema.get("maxLength"), int):
            raise SchemaDefinitionError(f"{path}: strings must declare maxLength")
        if "pattern" in schema:
            re.compile(schema["pattern"])


def validate(schema: Mapping[str, Any], value: Any) -> list[str]:
    """Return human-readable problems (empty if ``value`` conforms). Never echoes values."""
    errors: list[str] = []
    _validate(schema, value, "arguments", errors)
    return errors[:MAX_ERRORS]


def _validate(schema: Mapping[str, Any], value: Any, path: str, errors: list[str]) -> None:
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            errors.append(f"{path} must be an object")
            return
        props: Mapping[str, Any] = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name} is required")
        for name, item in value.items():
            if name not in props:
                errors.append(f"{path}.{str(name)[:40]} is not an allowed argument")
                continue
            _validate(props[name], item, f"{path}.{name}", errors)
    elif kind == "string":
        if not isinstance(value, str):
            errors.append(f"{path} must be a string")
            return
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path} is too short (minimum {schema['minLength']} characters)")
        if len(value) > schema["maxLength"]:
            errors.append(f"{path} is too long (maximum {schema['maxLength']} characters)")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path} must be one of {schema['enum']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path} has an invalid format")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path} must be an integer")
            return
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be at most {schema['maximum']}")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path} must be one of {schema['enum']}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path} must be true or false")
    else:  # pragma: no cover - check_schema rejects this at catalog build time
        errors.append(f"{path} has an unsupported schema")

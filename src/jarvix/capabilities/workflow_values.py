"""Bounded workflow values: JSON references, never Python/string interpolation."""
import copy
import json
import re

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,59}\Z")


def bounded(value):
    if len(json.dumps(value, allow_nan=False)) > 64000:
        raise ValueError("Workflow values exceed the 64 KB limit.")
    return copy.deepcopy(value)


def validate_references(value, result_ids=(), variables=(), item=False):
    found = False
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("Use a single structured $ref field.")
            parts = value["$ref"].split(".")
            if len(parts) > 12 or not all(part and len(part) <= 160 for part in parts):
                raise ValueError("Invalid workflow reference path.")
            if ((parts[0] == "results" and len(parts) > 1 and parts[1] in result_ids)
                    or (parts[0] == "variables" and len(parts) > 1 and parts[1] in variables)
                    or (parts[0] == "item" and item)):
                return True
            raise ValueError("A reference must identify a known variable, earlier output or loop item.")
        for child in value.values():
            found = validate_references(child, result_ids, variables, item) or found
    elif isinstance(value, list):
        for child in value:
            found = validate_references(child, result_ids, variables, item) or found
    return found


def resolve(value, values):
    if isinstance(value, dict):
        if "$ref" in value:
            source = values
            for key in value["$ref"].split("."):
                if isinstance(source, list) and key.isdigit():
                    source = source[int(key)]
                elif isinstance(source, dict):
                    source = source[key]
                else:
                    raise ValueError("Workflow reference no longer identifies a value.")
            return bounded(source)
        return {key: resolve(child, values) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve(child, values) for child in value]
    return value

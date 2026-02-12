from __future__ import annotations

import re


def extract_context_value(message: str, key: str) -> str | None:
    if not isinstance(message, str) or not isinstance(key, str):
        return None

    match = re.search(r"<context>(.*?)</context>", message, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None

    key_prefix = f"{key.lower()}="
    context_block = match.group(1)
    for raw_line in context_block.splitlines():
        line = raw_line.strip()
        if line.startswith("-"):
            line = line[1:].strip()
        if not line.lower().startswith(key_prefix):
            continue
        return line.split("=", 1)[1].strip()

    return None


def extract_selected_external_aliases(message: str) -> list[str] | None:
    raw_value = extract_context_value(message, "connectors")
    if raw_value is None:
        return None

    if not raw_value or raw_value.lower() == "none":
        return []

    selected: list[str] = []
    for token in raw_value.split(","):
        alias = token.strip()
        if alias and alias not in selected:
            selected.append(alias)
    return selected


def extract_output_type(message: str) -> str | None:
    raw_value = extract_context_value(message, "output_type")
    if raw_value is None:
        return None

    value = raw_value.strip().lower()
    if value in {"none", "auto"}:
        return None
    if value in {"report", "dashboard"}:
        return value
    return None

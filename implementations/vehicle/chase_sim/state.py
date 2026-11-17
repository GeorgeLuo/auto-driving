"""Pure lookups of Chase debug state and play-sidebar rows."""

from __future__ import annotations


def nested_get(record: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def play_sidebar_values(state: dict[str, object]) -> dict[str, object]:
    values: dict[str, object] = {}
    sections = state.get("playSidebarSections")
    if not isinstance(sections, list):
        return values
    for section in sections:
        if not isinstance(section, dict):
            continue
        rows = section.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if isinstance(row_id, str) and "value" in row:
                values[row_id] = row.get("value")
    return values

"""Pulls hidden form fields out of the two pages the login sequence reads:
the router's hotspot login page, and the HTML fragment the portal returns.

This deliberately does not parse HTML in general. Both pages are known, and the
only thing wanted from either is the attributes of their ``<input>`` tags, which
is a job a pattern match does correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INPUT_TAG = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
_ATTRIBUTE = re.compile(
    r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>=`]+))"""
)

_ENTITIES = (
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    ("&apos;", "'"),
    ("&nbsp;", " "),
)


@dataclass(frozen=True)
class HTMLInput:
    """One ``<input>`` tag lifted off a page."""

    name: str | None
    id: str | None
    value: str | None

    @property
    def key(self) -> str | None:
        """The key to send this field under: its name, or its id when unnamed.

        This is the rule the router's own page relies on.
        """
        if self.name:
            return self.name
        if self.id:
            return self.id
        return None


def inputs_in(html: str) -> list[HTMLInput]:
    """Every ``<input>`` on the page, in the order it appears."""
    found: list[HTMLInput] = []
    for match in _INPUT_TAG.finditer(html):
        attributes = _attributes_in(match.group(0))
        found.append(
            HTMLInput(
                name=attributes.get("name"),
                id=attributes.get("id"),
                value=attributes.get("value"),
            )
        )
    return found


def value_named(name: str, html: str) -> str | None:
    """The value of the input with this ``name``."""
    for candidate in inputs_in(html):
        if candidate.name == name:
            return candidate.value
    return None


def value_identified_by(identifier: str, html: str) -> str | None:
    """The value of the input with this id, falling back to its name.

    The router labels the two CHAP fields inconsistently across firmware
    versions, so both spellings have to be tried.
    """
    found = inputs_in(html)
    for candidate in found:
        if candidate.id == identifier:
            return candidate.value
    for candidate in found:
        if candidate.name == identifier:
            return candidate.value
    return None


def query_items(html: str) -> list[tuple[str, str]]:
    """Every field that has both a key and a value, ready to hang off a URL.

    A key that appears more than once keeps the position of its first
    appearance and the value of its last.
    """
    order: list[str] = []
    values: dict[str, str] = {}

    for candidate in inputs_in(html):
        key = candidate.key
        value = candidate.value
        if not key or not value:
            continue
        if key not in values:
            order.append(key)
        values[key] = value

    return [(key, values[key]) for key in order]


def form_action(html: str) -> str | None:
    """Where the page's first form posts to, if it says."""
    match = _FORM_TAG.search(html)
    if match is None:
        return None
    action = _attributes_in(match.group(0)).get("action")
    return action or None


def _attributes_in(tag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in _ATTRIBUTE.finditer(tag):
        quoted = match.group(2) or match.group(3) or match.group(4) or ""
        result[match.group(1).lower()] = _decode_entities(quoted)
    return result


def _decode_entities(text: str) -> str:
    """The handful of entities that actually turn up in these values."""
    if "&" not in text:
        return text
    for entity, character in _ENTITIES:
        text = text.replace(entity, character)
    # Ampersand goes last, so `&amp;lt;` ends up as `&lt;` and not as `<`.
    return text.replace("&amp;", "&")

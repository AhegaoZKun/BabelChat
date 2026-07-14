"""Text utilities for handling WoW chat edge cases."""

from __future__ import annotations

import re

from app.parser import RE_WOW_LINK

# WoW raid target markers: {rt1} through {rt8}, {star}, {circle}, {diamond}, {triangle},
# {moon}, {square}, {cross}, {skull}
_RE_WOW_MARKERS = re.compile(
    r"\{(?:rt[1-8]|star|circle|diamond|triangle|moon|square|cross|skull)\}",
    re.IGNORECASE,
)

# URLs (with scheme or www. prefix)
_RE_URL = re.compile(
    r"https?://[^\s<>\"]+|www\.[^\s<>\"]+",
    re.IGNORECASE,
)

# Schemeless links that DeepL otherwise mangles — most notably "discord.gg/xyz",
# where the ".gg" gets read as the gaming abbreviation and translated to
# "good game". Two cases:
#   1. Known link/invite domains — matched even without a path (e.g. "discord.gg").
#   2. Any "domain.tld/path" — requires a slash+path so we don't swallow ordinary
#      sentences that merely contain a period.
_LINK_DOMAINS = (
    r"discord\.gg|discord\.com|discordapp\.com|t\.me|bit\.ly|"
    r"youtu\.be|twitch\.tv|tinyurl\.com"
)
_RE_BARE_URL = re.compile(
    rf"\b(?:{_LINK_DOMAINS})(?:/[^\s<>\"]*)?"
    rf"|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{{2,}}/[^\s<>\"]+",
    re.IGNORECASE,
)


def strip_for_translation(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Strip non-translatable tokens from text, returning cleaned text and replacements.

    Returns:
        (cleaned_text, replacements) where replacements is a list of (placeholder, original).
    """
    replacements: list[tuple[str, str]] = []
    counter = 0

    def replace_token(match: re.Match[str]) -> str:
        nonlocal counter
        placeholder = f"__WCT{counter}__"
        counter += 1
        replacements.append((placeholder, match.group(0)))
        return placeholder

    result = text
    # Order matters: WoW links first (they contain special chars), then scheme'd
    # URLs, then schemeless link domains (discord.gg etc.), then markers
    result = RE_WOW_LINK.sub(replace_token, result)
    result = _RE_URL.sub(replace_token, result)
    result = _RE_BARE_URL.sub(replace_token, result)
    result = _RE_WOW_MARKERS.sub(replace_token, result)

    return result, replacements


def restore_tokens(text: str, replacements: list[tuple[str, str]]) -> str:
    """Restore original tokens after translation."""
    result = text
    for placeholder, original in replacements:
        result = result.replace(placeholder, original)
    return result


def clean_message_text(text: str) -> str:
    """Clean message text of control characters but preserve emoji and unicode."""
    # Remove WoW color codes that leak into chat log
    cleaned = re.sub(r"\|c[0-9a-fA-F]{8}", "", text)
    cleaned = re.sub(r"\|r", "", cleaned)
    return cleaned.strip()

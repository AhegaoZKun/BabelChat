"""Overlay theming: presets and resolution of user customization.

Pure Python (no GTK imports) so it can be unit-tested headlessly. The GTK
overlay maps WoW channels onto the color *slots* defined here; the settings
window edits one color per slot rather than one per channel enum member.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Ordered color slots shown in the settings UI. Several related channels share
# one slot (e.g. Raid + Raid Leader), mirroring how WoW colors them.
SLOT_ORDER: list[str] = [
    "say",
    "yell",
    "party",
    "raid",
    "raid_warning",
    "guild",
    "officer",
    "whisper",
    "instance",
    "public",
]

SLOT_LABELS: dict[str, str] = {
    "say": "Say",
    "yell": "Yell",
    "party": "Party",
    "raid": "Raid",
    "raid_warning": "Raid Warning",
    "guild": "Guild",
    "officer": "Officer",
    "whisper": "Whisper",
    "instance": "Instance",
    "public": "Trade / General / Services / LFG",
}


@dataclass(frozen=True)
class OverlayTheme:
    """Resolved visual style for the overlay."""

    bg_color: str
    text_color: str          # untranslated / plain message text
    original_color: str      # original text once a translation is shown
    translation_color: str   # translated text
    timestamp_color: str     # dim leading "21:30" timestamp
    tl_on_color: str         # TR: ON toggle (text/border; background derived)
    tl_off_color: str        # TR: OFF toggle
    close_color: str         # ✕ close button
    tool_color: str          # neutral bar buttons (settings)
    corner_radius: int
    channel_colors: dict[str, str] = field(default_factory=dict)


# The "wow" preset matches the Windows (PyQt6) overlay and classic WoW chat.
_WOW_CHANNELS = {
    "say": "#FFFFFF",
    "yell": "#FF4040",
    "party": "#AAAAFF",
    "raid": "#FF7F00",
    "raid_warning": "#FF4800",
    "guild": "#40FF40",
    "officer": "#40C040",
    "whisper": "#FF80FF",
    "instance": "#FF7F00",
    "public": "#FFC0C0",
}

PRESETS: dict[str, OverlayTheme] = {
    "wow": OverlayTheme(
        bg_color="#000000",
        text_color="#FFFFFF",
        original_color="#888888",
        translation_color="#FFD200",
        timestamp_color="#666666",
        tl_on_color="#40FF40",
        tl_off_color="#FF4040",
        close_color="#FF4040",
        tool_color="#CCCCCC",
        corner_radius=8,
        channel_colors=dict(_WOW_CHANNELS),
    ),
    "midnight": OverlayTheme(
        bg_color="#0B1220",
        text_color="#E2E8F0",
        original_color="#94A3B8",
        translation_color="#7DD3FC",
        timestamp_color="#475569",
        tl_on_color="#6EE7B7",
        tl_off_color="#FB7185",
        close_color="#FB7185",
        tool_color="#94A3B8",
        corner_radius=10,
        channel_colors={
            "say": "#E2E8F0",
            "yell": "#F87171",
            "party": "#93C5FD",
            "raid": "#FBBF24",
            "raid_warning": "#FB7185",
            "guild": "#6EE7B7",
            "officer": "#34D399",
            "whisper": "#C4B5FD",
            "instance": "#FBBF24",
            "public": "#F9A8D4",
        },
    ),
    "minimal": OverlayTheme(
        bg_color="#101010",
        text_color="#D0D0D0",
        original_color="#7A7A7A",
        translation_color="#E8E8E8",
        timestamp_color="#5A5A5A",
        tl_on_color="#A9E0B4",
        tl_off_color="#E0A9A9",
        close_color="#E0A9A9",
        tool_color="#9A9A9A",
        corner_radius=6,
        channel_colors={
            "say": "#D0D0D0",
            "yell": "#E0A9A9",
            "party": "#A9B8E0",
            "raid": "#E0C9A9",
            "raid_warning": "#E0A9A9",
            "guild": "#A9E0B4",
            "officer": "#A9E0B4",
            "whisper": "#D3A9E0",
            "instance": "#E0C9A9",
            "public": "#BFBFBF",
        },
    ),
    "light": OverlayTheme(
        bg_color="#F2F2F2",
        text_color="#111111",
        original_color="#555555",
        translation_color="#8A6D00",
        timestamp_color="#999999",
        tl_on_color="#15803D",
        tl_off_color="#B91C1C",
        close_color="#B91C1C",
        tool_color="#444444",
        corner_radius=8,
        channel_colors={
            "say": "#111111",
            "yell": "#B91C1C",
            "party": "#1D4ED8",
            "raid": "#B45309",
            "raid_warning": "#DC2626",
            "guild": "#15803D",
            "officer": "#166534",
            "whisper": "#A21CAF",
            "instance": "#B45309",
            "public": "#9D174D",
        },
    ),
    "high_contrast": OverlayTheme(
        bg_color="#000000",
        text_color="#FFFFFF",
        original_color="#FFFFFF",
        translation_color="#FFFF00",
        timestamp_color="#BBBBBB",
        tl_on_color="#00FF00",
        tl_off_color="#FF0000",
        close_color="#FF0000",
        tool_color="#FFFFFF",
        corner_radius=0,
        channel_colors={
            "say": "#FFFFFF",
            "yell": "#FF0000",
            "party": "#00BFFF",
            "raid": "#FFA500",
            "raid_warning": "#FF3300",
            "guild": "#00FF00",
            "officer": "#00CC00",
            "whisper": "#FF66FF",
            "instance": "#FFA500",
            "public": "#FFCCCC",
        },
    ),
}

# Order + labels for the settings dropdown. "custom" always last.
PRESET_ORDER: list[str] = ["wow", "midnight", "minimal", "light", "high_contrast", "custom"]
PRESET_LABELS: dict[str, str] = {
    "wow": "WoW Classic",
    "midnight": "Midnight",
    "minimal": "Minimal Dark",
    "light": "Light",
    "high_contrast": "High Contrast",
    "custom": "Custom",
}

DEFAULT_PRESET = "wow"


def resolve_theme(config) -> OverlayTheme:
    """Resolve the effective theme from an AppConfig.

    A named preset is used as-is; "custom" (or an unknown name) takes the
    stored per-field values, falling back to the default preset for anything
    missing (e.g. channel slots the user never touched).
    """
    name = getattr(config, "overlay_theme", DEFAULT_PRESET) or DEFAULT_PRESET
    if name != "custom" and name in PRESETS:
        return PRESETS[name]

    base = PRESETS[DEFAULT_PRESET]
    channels = dict(base.channel_colors)
    overrides = getattr(config, "overlay_channel_colors", None) or {}
    channels.update({k: v for k, v in overrides.items() if k in SLOT_ORDER and _is_hex(v)})

    def _color(attr: str, fallback: str) -> str:
        val = getattr(config, attr, "") or ""
        return val if _is_hex(val) else fallback

    return replace(
        base,
        bg_color=_color("overlay_bg_color", base.bg_color),
        text_color=_color("overlay_text_color", base.text_color),
        original_color=_color("overlay_original_color", base.original_color),
        translation_color=_color("overlay_translation_color", base.translation_color),
        timestamp_color=_color("overlay_timestamp_color", base.timestamp_color),
        tl_on_color=_color("overlay_tl_on_color", base.tl_on_color),
        tl_off_color=_color("overlay_tl_off_color", base.tl_off_color),
        close_color=_color("overlay_close_color", base.close_color),
        tool_color=_color("overlay_tool_color", base.tool_color),
        corner_radius=max(0, min(32, int(getattr(config, "overlay_corner_radius", base.corner_radius)))),
        channel_colors=channels,
    )


def _is_hex(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def dim(value: str, factor: float) -> str:
    """Darken '#RRGGBB' by factor (0..1) — used to derive button backgrounds."""
    r, g, b = hex_to_rgb(value)
    return f"rgba({int(r * factor)},{int(g * factor)},{int(b * factor)},0.85)"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """'#RRGGBB' → (r, g, b). Falls back to black on bad input."""
    if not _is_hex(value):
        return (0, 0, 0)
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))

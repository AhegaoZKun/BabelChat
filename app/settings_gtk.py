"""GTK4 settings window for BabelChat.

A normal (non-layer-shell) window — it needs to be freely movable, closable, and
able to take keyboard input, which a regular GTK window does natively. Edits are
written to config.json on Save; an on_saved callback lets the app apply changes
to the running pipeline/overlay live (channels, languages, etc.).

Covers: channels, languages (own/target/UI), translator priority + API keys,
overlay opacity/font, and the skip-own-messages toggle.
"""

from __future__ import annotations

from collections.abc import Callable  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.config import AppConfig  # noqa: E402

# (label, attribute) pairs for the channel checkboxes.
_CHANNELS: list[tuple[str, str]] = [
    ("Say", "channels_say"),
    ("Yell", "channels_yell"),
    ("Party", "channels_party"),
    ("Raid", "channels_raid"),
    ("Guild", "channels_guild"),
    ("Whisper", "channels_whisper"),
    ("Instance", "channels_instance"),
    ("Trade", "channels_trade"),
    ("General", "channels_general"),
    ("Services", "channels_services"),
    ("LFG", "channels_lfg"),
]

_LANGS = ["EN", "RU", "ES", "DE", "FR", "PT", "IT", "PL", "ZH", "KO", "JA"]


class SettingsWindowGtk:
    """Settings editor. Construct with the live AppConfig and an on_saved cb."""

    def __init__(
        self,
        config: AppConfig,
        on_saved: Callable[[AppConfig], None] | None = None,
        app: Gtk.Application | None = None,
    ) -> None:
        self._config = config
        self._on_saved = on_saved
        self._checks: dict[str, Gtk.CheckButton] = {}

        self._win = Gtk.Window()
        if app is not None:
            self._win.set_application(app)
        self._win.set_title("BabelChat Settings")
        self._win.set_default_size(460, 640)
        self._build()

    def present(self) -> None:
        self._win.present()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(16)
        root.set_margin_end(16)

        # Channels
        root.append(self._section("Channels"))
        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(16)
        for i, (label, attr) in enumerate(_CHANNELS):
            cb = Gtk.CheckButton(label=label)
            cb.set_active(bool(getattr(self._config, attr)))
            self._checks[attr] = cb
            grid.attach(cb, i % 2, i // 2, 1, 1)
        root.append(grid)

        # Languages
        root.append(self._section("Languages"))
        self._own = self._combo_row(root, "Own language", self._config.own_language)
        self._target = self._combo_row(root, "Target language", self._config.target_language)
        self._ui = self._combo_row(root, "UI language", self._config.ui_language)

        # Translation API
        root.append(self._section("Translation API"))
        self._priority = self._combo_row(
            root, "Priority", self._config.translator_priority, options=["deepl", "microsoft"]
        )
        self._deepl = self._entry_row(root, "DeepL API key", self._config.deepl_api_key, secret=True)
        self._ms_key = self._entry_row(root, "Microsoft API key", self._config.microsoft_api_key, secret=True)
        self._ms_region = self._entry_row(root, "Microsoft region", self._config.microsoft_region)

        # Appearance
        root.append(self._section("Appearance"))
        self._opacity = self._scale_row(root, "Opacity", self._config.overlay_opacity, 40, 255)
        self._font = self._scale_row(root, "Font size", self._config.overlay_font_size, 8, 28)

        # Behavior
        root.append(self._section("Behavior"))
        self._skip_own = Gtk.CheckButton(label="Skip my own messages")
        self._skip_own.set_active(bool(self._config.skip_own_messages))
        root.append(self._skip_own)

        # Actions
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save = Gtk.Button(label="Save")
        save.connect("clicked", self._on_save)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _b: self._win.close())
        self._status = Gtk.Label(label="")
        self._status.set_hexpand(True)
        self._status.set_xalign(0.0)
        actions.append(save)
        actions.append(close)
        actions.append(self._status)
        root.append(actions)

        scroller.set_child(root)
        self._win.set_child(scroller)

    def _section(self, text: str) -> Gtk.Label:
        lbl = Gtk.Label()
        lbl.set_markup(f"<b>{text}</b>")
        lbl.set_xalign(0.0)
        lbl.set_margin_top(6)
        return lbl

    def _entry_row(self, parent: Gtk.Box, label: str, value: str, secret: bool = False) -> Gtk.Entry:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label)
        lbl.set_width_chars(18)
        lbl.set_xalign(0.0)
        entry = Gtk.Entry()
        entry.set_text(value or "")
        entry.set_hexpand(True)
        if secret:
            entry.set_visibility(False)
        row.append(lbl)
        row.append(entry)
        parent.append(row)
        return entry

    def _combo_row(self, parent: Gtk.Box, label: str, value: str, options: list[str] | None = None) -> Gtk.DropDown:
        opts = options or _LANGS
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label)
        lbl.set_width_chars(18)
        lbl.set_xalign(0.0)
        model = Gtk.StringList()
        for o in opts:
            model.append(o)
        dd = Gtk.DropDown(model=model)
        try:
            dd.set_selected(opts.index(value))
        except ValueError:
            dd.set_selected(0)
        dd._opts = opts  # stash for read-back
        row.append(lbl)
        row.append(dd)
        parent.append(row)
        return dd

    def _scale_row(self, parent: Gtk.Box, label: str, value: int, lo: int, hi: int) -> Gtk.Scale:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label)
        lbl.set_width_chars(18)
        lbl.set_xalign(0.0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        scale.set_value(value)
        scale.set_hexpand(True)
        scale.set_draw_value(True)
        row.append(lbl)
        row.append(scale)
        parent.append(row)
        return scale

    # ── save ──────────────────────────────────────────────────────────────
    def _dd_value(self, dd: Gtk.DropDown) -> str:
        opts = getattr(dd, "_opts", _LANGS)
        idx = dd.get_selected()
        return opts[idx] if 0 <= idx < len(opts) else opts[0]

    def _on_save(self, _btn: Gtk.Button) -> None:
        c = self._config
        for attr, cb in self._checks.items():
            setattr(c, attr, cb.get_active())
        c.own_language = self._dd_value(self._own)
        c.target_language = self._dd_value(self._target)
        c.ui_language = self._dd_value(self._ui)
        c.translator_priority = self._dd_value(self._priority)
        c.deepl_api_key = self._deepl.get_text()
        c.microsoft_api_key = self._ms_key.get_text()
        c.microsoft_region = self._ms_region.get_text()
        c.overlay_opacity = int(self._opacity.get_value())
        c.overlay_font_size = int(self._font.get_value())
        c.skip_own_messages = self._skip_own.get_active()

        try:
            c.save()
            self._status.set_markup('<span foreground="#33aa33">Saved.</span>')
        except Exception as exc:  # noqa: BLE001
            self._status.set_markup(f'<span foreground="#cc3333">Save failed: {exc}</span>')
            return

        if self._on_saved is not None:
            self._on_saved(c)

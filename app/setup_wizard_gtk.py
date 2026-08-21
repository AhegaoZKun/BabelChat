"""GTK4 first-run setup wizard for BabelChat (Linux frontend).

Mirrors the PyQt SetupWizard: Welcome → API keys → WoW path → Languages →
Ready. Runs as its OWN Gtk.Application main loop before normal startup
(sequential GTK loops in one process are fine), so the overlay/main wiring
stays untouched.

Usage (from main_gtk):
    cfg = run_setup_wizard(config)
    if cfg is None:   # user cancelled/closed
        return 0
"""

from __future__ import annotations

import threading  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from app.config import AppConfig, detect_wow_path  # noqa: E402
from app.translators import all_providers  # noqa: E402
from app.translators import get as provider_get  # noqa: E402

_LANGS = [
    ("EN", "English"),
    ("RU", "Русский"),
    ("ES", "Español"),
    ("DE", "Deutsch"),
    ("FR", "Français"),
    ("PT", "Português"),
    ("IT", "Italiano"),
    ("PL", "Polski"),
    ("ZH", "中文"),
    ("KO", "한국어"),
    ("JA", "日本語"),
]
_UI_LANGS = [("EN", "English"), ("RU", "Русский"), ("ES", "Español")]


class _WizardWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, config: AppConfig, result: dict) -> None:
        super().__init__(application=app, title="BabelChat Setup")
        self._config = config
        self._result = result  # {"config": AppConfig|None}
        self.set_default_size(520, 480)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._pages: list[Gtk.Widget] = []
        for builder in (self._page_welcome, self._page_api, self._page_wow, self._page_langs, self._page_ready):
            page = builder()
            self._pages.append(page)
            self._stack.add_child(page)
        self._index = 0

        # Nav bar
        self._back = Gtk.Button(label="Back")
        self._back.connect("clicked", lambda _b: self._go(-1))
        self._next = Gtk.Button(label="Next")
        self._next.add_css_class("suggested-action")
        self._next.connect("clicked", self._on_next)
        self._step_lbl = Gtk.Label()
        self._step_lbl.set_hexpand(True)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_margin_top(8)
        nav.append(self._back)
        nav.append(self._step_lbl)
        nav.append(self._next)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(root, m)(16)
        root.append(self._stack)
        root.append(nav)
        self.set_child(root)
        self._sync_nav()

    # ── navigation ────────────────────────────────────────────────────────
    def _go(self, delta: int) -> None:
        self._index = max(0, min(len(self._pages) - 1, self._index + delta))
        self._stack.set_visible_child(self._pages[self._index])
        self._sync_nav()

    def _sync_nav(self) -> None:
        last = self._index == len(self._pages) - 1
        self._back.set_sensitive(self._index > 0)
        self._next.set_label("Finish" if last else "Next")
        self._step_lbl.set_markup(f'<span foreground="#888">Step {self._index + 1} of {len(self._pages)}</span>')
        if last:
            self._refresh_summary()

    def _on_next(self, _btn: Gtk.Button) -> None:
        if self._index == len(self._pages) - 1:
            self._finish()
            return
        self._go(+1)

    # ── pages ─────────────────────────────────────────────────────────────
    @staticmethod
    def _title(text: str) -> Gtk.Label:
        lbl = Gtk.Label()
        lbl.set_markup(f'<span size="x-large" weight="bold">{text}</span>')
        lbl.set_xalign(0.0)
        return lbl

    @staticmethod
    def _body(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_wrap(True)
        lbl.set_xalign(0.0)
        return lbl

    def _dropdown(self, pairs: list[tuple[str, str]], selected_code: str) -> Gtk.DropDown:
        model = Gtk.StringList()
        for code, name in pairs:
            model.append(f"{name} ({code})")
        dd = Gtk.DropDown(model=model)
        dd._codes = [c for c, _ in pairs]
        try:
            dd.set_selected(dd._codes.index(selected_code))
        except ValueError:
            dd.set_selected(0)
        return dd

    @staticmethod
    def _dd_code(dd: Gtk.DropDown) -> str:
        return dd._codes[dd.get_selected()]

    def _page_welcome(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self._title("Welcome to BabelChat"))
        box.append(
            self._body(
                "Real-time WoW chat translation overlay.\n\n"
                "This wizard sets up your translation API, game path, and "
                "languages. It only takes a minute."
            )
        )
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(self._body("Interface language:"))
        self._ui_lang = self._dropdown(_UI_LANGS, self._config.ui_language or "EN")
        row.append(self._ui_lang)
        box.append(row)
        return box

    def _page_api(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self._title("Translation API"))
        box.append(
            self._body(
                "BabelChat needs a translation API key. DeepL's free tier "
                "(500k chars/month) is recommended — deepl.com/pro-api. "
                "Microsoft Translator is supported as well."
            )
        )

        def key_row(label: str, value: str, validate_cb, secret: bool = True) -> tuple[Gtk.Entry, Gtk.Button]:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=label)
            lbl.set_width_chars(14)
            lbl.set_xalign(0.0)
            entry = Gtk.Entry()
            entry.set_text(value or "")
            # Only credentials are masked. A region or an endpoint is not a
            # secret, and hiding it just makes it harder to check for typos.
            entry.set_visibility(not secret)
            entry.set_hexpand(True)
            btn = Gtk.Button(label="Validate")
            btn.connect("clicked", validate_cb)
            r.append(lbl)
            r.append(entry)
            r.append(btn)
            box.append(r)
            return entry, btn

        # One row per credential each registered provider declares. Nothing here
        # names a provider, so adding one shows up in the wizard by itself.
        saved = self._config.providers or {}
        self._provider_entries: dict[str, dict[str, Gtk.Entry]] = {}
        for spec in all_providers():
            values = saved.get(spec.id, {})
            fields: dict[str, Gtk.Entry] = {}
            for index, pfield in enumerate(spec.fields):
                label = spec.display_name if index == 0 else pfield.label_text()
                entry, _btn = key_row(
                    label,
                    values.get(pfield.key, ""),
                    lambda _b, pid=spec.id: self._validate_provider(_b, pid),
                    secret=pfield.secret,
                )
                fields[pfield.key] = entry
            self._provider_entries[spec.id] = fields

        prio_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prio_lbl = Gtk.Label(label="Prefer")
        prio_lbl.set_width_chars(14)
        prio_lbl.set_xalign(0.0)
        self._priority = self._dropdown(
            [(spec.id, spec.display_name) for spec in all_providers()], self._config.translator_priority or ""
        )
        prio_row.append(prio_lbl)
        prio_row.append(self._priority)
        box.append(prio_row)

        self._api_status = Gtk.Label(label="")
        self._api_status.set_xalign(0.0)
        self._api_status.set_wrap(True)
        box.append(self._api_status)
        return box

    def _page_wow(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self._title("World of Warcraft path"))
        box.append(
            self._body(
                "Needed to install the companion addon and find the chat log. "
                "Auto-detect scans common Steam/Lutris/Wine prefixes."
            )
        )
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._wow_entry = Gtk.Entry()
        self._wow_entry.set_text(self._config.wow_path or "")
        self._wow_entry.set_placeholder_text("…/World of Warcraft")
        self._wow_entry.set_hexpand(True)
        detect_btn = Gtk.Button(label="Auto-detect")
        detect_btn.connect("clicked", self._auto_detect)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._browse)
        row.append(self._wow_entry)
        row.append(detect_btn)
        row.append(browse_btn)
        box.append(row)
        self._wow_status = Gtk.Label(label="")
        self._wow_status.set_xalign(0.0)
        box.append(self._wow_status)
        return box

    def _page_langs(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self._title("Languages"))
        box.append(self._body("Your language — incoming chat in it is left untranslated."))
        self._own_lang = self._dropdown(_LANGS, self._config.own_language or "EN")
        box.append(self._own_lang)
        box.append(self._body("Translate incoming chat into:"))
        self._target_lang = self._dropdown(_LANGS, self._config.target_language or "EN")
        box.append(self._target_lang)
        return box

    def _page_ready(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self._title("Ready"))
        self._summary = self._body("")
        box.append(self._summary)
        box.append(self._body("Click Finish to save and start BabelChat."))
        return box

    def _entered(self, provider_id: str) -> dict[str, str]:
        return {key: entry.get_text().strip() for key, entry in self._provider_entries[provider_id].items()}

    def _refresh_summary(self) -> None:
        rows = [
            f"{GLib.markup_escape_text(spec.display_name)}: "
            f"<b>{'set' if spec.is_configured(self._entered(spec.id)) else 'not set'}</b>"
            for spec in all_providers()
        ]
        rows.append(f"WoW path: <b>{GLib.markup_escape_text(self._wow_entry.get_text().strip() or 'not set')}</b>")
        rows.append(f"Own language: <b>{self._dd_code(self._own_lang)}</b>")
        rows.append(f"Target language: <b>{self._dd_code(self._target_lang)}</b>")
        self._summary.set_markup("\n".join(rows))

    # ── API validation (off-thread) ───────────────────────────────────────
    def _has_any_key(self) -> bool:
        return any(spec.is_configured(self._entered(spec.id)) for spec in all_providers())

    def _validate_provider(self, btn: Gtk.Button, provider_id: str) -> None:
        spec = provider_get(provider_id)
        if spec is None:
            return
        values = self._entered(provider_id)
        if not spec.is_configured(values):
            name = GLib.markup_escape_text(spec.display_name)
            self._api_status.set_markup(f'<span foreground="#cc6666">Enter a {name} key first.</span>')
            return
        self._run_validation(btn, lambda: spec.validate(values), spec.display_name)

    def _run_validation(self, btn: Gtk.Button, fn, name: str) -> None:
        btn.set_sensitive(False)
        self._api_status.set_markup(f'<span foreground="#cccc66">Validating {name} key…</span>')

        def worker() -> None:
            try:
                valid, msg = fn()
            except Exception as exc:  # noqa: BLE001
                valid, msg = False, str(exc)
            GLib.idle_add(done, valid, msg)

        def done(valid: bool, msg: str) -> bool:
            btn.set_sensitive(True)
            if valid:
                extra = f" — {msg}" if msg and msg != "valid" else ""
                self._api_status.set_markup(f'<span foreground="#66cc66">✓ {name} key valid{extra}</span>')
            else:
                nice = {"auth_failed": "invalid key", "no_key": "no key entered"}.get(msg, msg)
                self._api_status.set_markup(
                    f'<span foreground="#cc6666">✗ {name}: {GLib.markup_escape_text(nice)}</span>'
                )
            return False

        threading.Thread(target=worker, daemon=True).start()

    # ── WoW path helpers ──────────────────────────────────────────────────
    def _auto_detect(self, _btn: Gtk.Button) -> None:
        path = detect_wow_path()
        if path:
            self._wow_entry.set_text(path)
            self._wow_status.set_markup('<span foreground="#66cc66">✓ Found installation.</span>')
        else:
            self._wow_status.set_markup(
                '<span foreground="#cc6666">Not found — set it manually (can be changed later).</span>'
            )

    def _browse(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Select World of Warcraft folder")

        def picked(dlg: Gtk.FileDialog, res) -> None:
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if folder is not None:
                self._wow_entry.set_text(folder.get_path() or "")

        dialog.select_folder(self, None, picked)

    # ── finish ────────────────────────────────────────────────────────────
    def _finish(self) -> None:
        c = self._config
        providers: dict[str, dict[str, str]] = {}
        for spec in all_providers():
            values = {key: value for key, value in self._entered(spec.id).items() if value}
            # Keyless providers are configured by existing — see the Qt copy.
            if values or spec.keyless:
                providers[spec.id] = values
        c.providers = providers
        c.translator_priority = self._dd_code(self._priority)
        c.wow_path = self._wow_entry.get_text().strip()
        c.own_language = self._dd_code(self._own_lang)
        c.target_language = self._dd_code(self._target_lang)
        c.ui_language = self._dd_code(self._ui_lang)
        c.save()
        self._result["config"] = c
        self.close()


def run_setup_wizard(config: AppConfig) -> AppConfig | None:
    """Run the wizard in its own blocking GTK loop.

    Returns the saved config, or None if the user closed without finishing.
    """
    result: dict = {"config": None}
    app = Gtk.Application(application_id="com.babelchat.SetupWizard")

    def on_activate(a: Gtk.Application) -> None:
        _WizardWindow(a, config, result).present()

    app.connect("activate", on_activate)
    app.run(None)
    return result["config"]

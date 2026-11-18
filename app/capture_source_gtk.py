"""The "where does chat come from" control, shared by the wizard and settings.

One widget so the two places cannot disagree about what choosing the screen
source involves: pick the chat area, test that it reads, and see which capture
method this machine will use. The owner keeps the state (source, region, portal
token) in its own attributes and is told about every change through callbacks;
that way the state survives the owner rebuilding its pages, which the wizard
does whenever the interface language changes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from app.i18n import tr  # noqa: E402
from app.ocr_capture import OcrRegion, describe_backend  # noqa: E402
from app.ocr_engine import SCRIPTS  # noqa: E402
from app.ocr_engine import is_available as ocr_engine_available  # noqa: E402
from app.ocr_reader import OcrSettings, read_region_report  # noqa: E402
from app.ocr_report import format_report  # noqa: E402


def _body(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_wrap(True)
    label.set_xalign(0.0)
    return label


class CaptureSourceWidget(Gtk.Box):
    def __init__(
        self,
        parent_window: Gtk.Window,
        source: str,
        region: OcrRegion | None,
        portal_token: str,
        script_for_test: Callable[[], str],
        on_source: Callable[[str], None],
        on_region: Callable[[OcrRegion], None],
        on_token: Callable[[str], None],
        interval: float | None = None,
        script: str | None = None,
    ) -> None:
        """`interval` / `script` of None hide those rows (the wizard keeps it simple)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._parent = parent_window
        self._source = source
        self._region = region
        self._portal_token = portal_token
        self._script_for_test = script_for_test
        self._on_source = on_source
        self._on_region = on_region
        self._on_token = on_token

        self._ocr_radio = Gtk.CheckButton(label=tr("wizard.source.ocr"))
        self._ocr_radio.set_active(source == "ocr")
        self._memory_radio = Gtk.CheckButton(label=tr("wizard.source.memory"))
        self._memory_radio.set_group(self._ocr_radio)
        self._memory_radio.set_active(source == "memory")

        self.append(self._described(self._ocr_radio, tr("wizard.source.ocr.desc")))
        self.append(self._build_panel(interval, script))
        self.append(self._described(self._memory_radio, tr("wizard.source.memory.desc")))

        self._panel.set_visible(source == "ocr")
        self._ocr_radio.connect("toggled", self._on_toggled)

    # -- values the owner reads on save -------------------------------------

    @property
    def interval(self) -> float:
        return self._spin.get_value() if self._spin is not None else 1.0

    @property
    def script(self) -> str:
        if self._script_dd is None:
            return "auto"
        return self._script_dd._codes[self._script_dd.get_selected()]

    # -- construction --------------------------------------------------------

    @staticmethod
    def _described(radio: Gtk.CheckButton, text: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.append(radio)
        desc = _body(text)
        desc.set_margin_start(26)
        desc.add_css_class("dim-label")
        row.append(desc)
        return row

    def _build_panel(self, interval: float | None, script: str | None) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_margin_start(26)
        self._panel = panel

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._pick_btn = Gtk.Button(label=tr("wizard.source.repick" if self._region else "wizard.source.pick"))
        self._test_btn = Gtk.Button(label=tr("wizard.source.test"))
        self._test_btn.set_sensitive(self._region is not None)
        buttons.append(self._pick_btn)
        buttons.append(self._test_btn)
        panel.append(buttons)

        self._area_lbl = _body(tr("wizard.source.area_set" if self._region else "wizard.source.area_none"))
        self._result_lbl = _body("")
        self._result_lbl.set_selectable(True)
        panel.append(self._area_lbl)
        panel.append(self._result_lbl)
        panel.append(_body(tr("wizard.source.method", method=describe_backend())))
        panel.append(_body(tr("wizard.source.tips")))

        self._script_dd: Gtk.DropDown | None = None
        if script is not None:
            codes = ["auto", *SCRIPTS]
            model = Gtk.StringList()
            for code in codes:
                model.append(tr(f"ocr.script.{code}"))
            dd = Gtk.DropDown(model=model)
            dd._codes = codes
            dd.set_selected(codes.index(script) if script in codes else 0)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(_body(tr("settings.capture.script")))
            row.append(dd)
            panel.append(row)
            self._script_dd = dd

        self._spin: Gtk.SpinButton | None = None
        if interval is not None:
            adjustment = Gtk.Adjustment(value=interval, lower=0.25, upper=5.0, step_increment=0.25, page_increment=1.0)
            spin = Gtk.SpinButton(adjustment=adjustment, digits=2)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(_body(tr("settings.capture.interval")))
            row.append(spin)
            panel.append(row)
            self._spin = spin

        if not ocr_engine_available():
            warning = _body(tr("wizard.source.no_engine"))
            warning.add_css_class("error")
            panel.append(warning)

        self._pick_btn.connect("clicked", self._on_pick)
        self._test_btn.connect("clicked", self._on_test)
        return panel

    # -- behaviour -------------------------------------------------------------

    def _on_toggled(self, _radio: Gtk.CheckButton) -> None:
        self._source = "ocr" if self._ocr_radio.get_active() else "memory"
        self._panel.set_visible(self._source == "ocr")
        self._on_source(self._source)

    def _on_pick(self, _btn: Gtk.Button) -> None:
        from app.region_picker_gtk import RegionPicker

        RegionPicker(self._picked).show()

    def _picked(self, region: OcrRegion | None) -> None:
        self._parent.present()
        if region is None:
            return
        self._region = region
        self._pick_btn.set_label(tr("wizard.source.repick"))
        self._test_btn.set_sensitive(True)
        self._area_lbl.set_label(tr("wizard.source.area_set"))
        self._result_lbl.set_label("")
        self._on_region(region)

    def _remember_token(self, token: str) -> None:
        self._portal_token = token
        self._on_token(token)

    def _on_test(self, _btn: Gtk.Button) -> None:
        if self._region is None:
            return
        settings = OcrSettings(
            region=self._region,
            script=self._script_for_test(),
            restore_token=self._portal_token,
        )
        self._test_btn.set_sensitive(False)
        self._result_lbl.set_label(tr("wizard.source.testing"))

        def worker() -> None:
            try:
                report = read_region_report(settings, on_token=self._remember_token)
                text = format_report(report)
            except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
                text = tr("wizard.source.test_fail", e=exc)
            GLib.idle_add(done, text)

        def done(text: str) -> bool:
            self._test_btn.set_sensitive(True)
            self._result_lbl.set_label(text)
            return False

        threading.Thread(target=worker, daemon=True).start()

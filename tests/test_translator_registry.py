"""The provider registry, the fallback chain, and the config migration.

`TranslatorService` used to be built from four hardcoded keyword arguments and
knew both provider names by heart. What matters now is that it knows none: it
reads whatever the registry holds, and a config it cannot fully understand
degrades instead of failing.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.config import AppConfig
from app.translators import base
from app.translators.base import (
    FAILURE,
    ProviderField,
    ProviderSpec,
    TranslationResult,
    any_configured,
    configured_ids,
    resolve_order,
)
from app.translators.service import TranslatorService


class FakeBackend:
    """A backend that answers however the test needs it to."""

    def __init__(self, name: str, succeeds: bool = True, error: str = "boom") -> None:
        self.name = name
        self.succeeds = succeeds
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def translate(self, text: str, target_lang: str, source_lang: str | None = None) -> TranslationResult:
        self.calls.append((text, target_lang))
        return TranslationResult(
            original=text,
            translated=f"{self.name}:{text}" if self.succeeds else text,
            source_lang=source_lang or "EN",
            target_lang=target_lang,
            success=self.succeeds,
            error=None if self.succeeds else self.error,
            backend=self.name,
        )

    def validate(self) -> tuple[bool, str]:
        return self.succeeds, "valid" if self.succeeds else self.error


@pytest.fixture
def registry(monkeypatch):
    """An empty registry for the duration of one test.

    The real one is populated at import time by the provider modules; replacing
    its innards keeps tests from leaking fake providers into each other.
    """
    monkeypatch.setattr(base, "_REGISTRY", {})
    monkeypatch.setattr(base, "_ORDER", [])
    return base


def register_fake(registry, provider_id: str, backend: FakeBackend | None = None, **kwargs) -> FakeBackend:
    backend = backend or FakeBackend(provider_id)
    registry.register(
        ProviderSpec(
            id=provider_id,
            display_name=provider_id.title(),
            fields=(ProviderField(key="api_key", label="Key"),),
            build=lambda _settings, b=backend: b,
            validate=lambda _settings, b=backend: b.validate(),
            **kwargs,
        )
    )
    return backend


# ── registration ─────────────────────────────────────────────────────────────


def test_registration_order_is_the_listing_order(registry):
    register_fake(registry, "second")
    register_fake(registry, "first")
    assert registry.known_ids() == ("second", "first")


def test_registering_the_same_id_twice_is_refused(registry):
    register_fake(registry, "dup")
    with pytest.raises(ValueError, match="already registered"):
        register_fake(registry, "dup")


def test_a_provider_with_a_blank_required_field_is_not_configured(registry):
    register_fake(registry, "acme")
    spec = registry.get("acme")
    assert spec.is_configured({"api_key": "k"}) is True
    assert spec.is_configured({"api_key": "   "}) is False
    assert spec.is_configured({}) is False


def test_a_keyless_provider_is_always_configured(registry):
    register_fake(registry, "free", keyless=True)
    assert registry.get("free").is_configured({}) is True


# ── the fallback chain ───────────────────────────────────────────────────────


def test_the_preferred_provider_is_tried_first(registry):
    first = register_fake(registry, "alpha")
    second = register_fake(registry, "beta")
    service = TranslatorService({"alpha": {"api_key": "a"}, "beta": {"api_key": "b"}}, priority="beta")

    result = service.translate("hi", "RU")

    assert result.backend == "beta"
    assert second.calls and not first.calls


def test_a_failure_falls_through_to_the_next_provider(registry):
    failing = register_fake(registry, "alpha", FakeBackend("alpha", succeeds=False))
    working = register_fake(registry, "beta")
    service = TranslatorService({"alpha": {"api_key": "a"}, "beta": {"api_key": "b"}}, priority="alpha")

    result = service.translate("hi", "RU")

    assert result.success is True
    assert result.backend == "beta"
    assert failing.calls and working.calls


def test_when_every_provider_fails_the_last_failure_is_returned_with_the_original(registry):
    register_fake(registry, "alpha", FakeBackend("alpha", succeeds=False, error="alpha_down"))
    register_fake(registry, "beta", FakeBackend("beta", succeeds=False, error="beta_down"))
    service = TranslatorService({"alpha": {"api_key": "a"}, "beta": {"api_key": "b"}}, priority="alpha")

    result = service.translate("hello", "RU")

    assert result.success is False
    assert result.error == "beta_down"
    assert result.translated == "hello", "a failed translation still shows the message"


def test_blank_input_is_returned_without_calling_a_provider(registry):
    backend = register_fake(registry, "alpha")
    service = TranslatorService({"alpha": {"api_key": "a"}})

    result = service.translate("   ", "RU")

    assert result.success is True
    assert backend.calls == []


# ── configs this build cannot fully understand ───────────────────────────────


def test_an_unknown_provider_id_is_skipped_and_the_rest_still_work(registry, caplog):
    register_fake(registry, "alpha")
    service = TranslatorService({"from_the_future": {"token": "x"}, "alpha": {"api_key": "a"}})

    assert service.has_backend is True
    assert service.translate("hi", "RU").backend == "alpha"
    assert "from_the_future" in caplog.text


def test_an_unknown_priority_falls_back_to_listing_order(registry):
    register_fake(registry, "alpha")
    register_fake(registry, "beta")
    service = TranslatorService({"alpha": {"api_key": "a"}, "beta": {"api_key": "b"}}, priority="nonesuch")

    assert service.active_ids == ("alpha", "beta")


def test_an_empty_registry_reports_no_backend_rather_than_raising(registry):
    service = TranslatorService({}, priority="deepl")

    result = service.translate("hi", "RU")

    assert service.has_backend is False
    assert result.success is False
    assert result.error == FAILURE.NO_BACKEND
    assert result.translated == "hi"


def test_a_provider_that_cannot_be_built_does_not_take_the_others_down(registry, caplog):
    def explode(_settings):
        raise RuntimeError("bad credentials shape")

    registry.register(
        ProviderSpec(
            id="broken",
            display_name="Broken",
            fields=(ProviderField(key="api_key", label="Key"),),
            build=explode,
            validate=lambda _s: (False, "no"),
        )
    )
    register_fake(registry, "alpha")
    service = TranslatorService({"broken": {"api_key": "x"}, "alpha": {"api_key": "a"}})

    assert service.active_ids == ("alpha",)
    assert "Broken" in caplog.text


def test_resolve_order_puts_the_preferred_provider_first(registry):
    register_fake(registry, "alpha")
    register_fake(registry, "beta")
    register_fake(registry, "gamma")

    assert resolve_order("gamma", ["alpha", "beta", "gamma"]) == ["gamma", "alpha", "beta"]


def test_resolve_order_ignores_a_preference_for_a_provider_that_is_not_set_up(registry):
    """Asserting `resolve_order("beta", ["alpha"]) == ["alpha"]` was output
    equals input — the second argument IS the configured list. A preference for
    an unconfigured provider has to leave the others in their own order and add
    nothing."""
    register_fake(registry, "alpha")
    register_fake(registry, "beta")
    register_fake(registry, "gamma")

    order = resolve_order("beta", ["alpha", "gamma"])

    assert order == ["alpha", "gamma"]
    assert "beta" not in order


# ── "is the app set up?" ─────────────────────────────────────────────────────


def test_any_configured_is_false_for_blank_and_unknown_entries(registry):
    register_fake(registry, "alpha")
    assert any_configured(None) is False
    assert any_configured({}) is False
    assert any_configured({"alpha": {"api_key": ""}}) is False
    assert any_configured({"stranger": {"api_key": "x"}}) is False
    assert any_configured({"alpha": {"api_key": "x"}}) is True


def test_configured_ids_keeps_only_complete_known_providers(registry):
    register_fake(registry, "alpha")
    register_fake(registry, "beta")
    ids = configured_ids({"alpha": {"api_key": "x"}, "beta": {}, "stranger": {"api_key": "y"}})
    assert ids == ["alpha"]


# ── config migration ─────────────────────────────────────────────────────────


def write_config(path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_pre_registry_keys_are_migrated_without_loss(tmp_path):
    config_file = tmp_path / "config.json"
    write_config(
        config_file,
        {
            "deepl_api_key": "deepl-secret:fx",
            "microsoft_api_key": "ms-secret",
            "microsoft_region": "westeurope",
            "translator_priority": "microsoft",
        },
    )

    loaded = AppConfig.load(str(config_file))

    assert loaded.providers == {
        "deepl": {"api_key": "deepl-secret:fx"},
        "microsoft": {"api_key": "ms-secret", "region": "westeurope"},
    }
    assert loaded.translator_priority == "microsoft"


def test_migration_keeps_a_copy_of_the_original_config(tmp_path):
    """The ordinary .bak is overwritten by the next save, so upgrading would
    otherwise leave no way back to the pre-migration file."""
    config_file = tmp_path / "config.json"
    write_config(config_file, {"deepl_api_key": "deepl-secret:fx"})

    AppConfig.load(str(config_file))

    backup = tmp_path / "config.json.pre-providers.bak"
    assert backup.exists()
    assert "deepl-secret:fx" in backup.read_text(encoding="utf-8")


def test_migration_does_not_overwrite_a_value_already_in_the_new_shape(tmp_path):
    config_file = tmp_path / "config.json"
    write_config(
        config_file,
        {"deepl_api_key": "old-key", "providers": {"deepl": {"api_key": "new-key"}}},
    )

    loaded = AppConfig.load(str(config_file))

    assert loaded.providers["deepl"]["api_key"] == "new-key"


def test_a_config_with_no_legacy_keys_is_untouched(tmp_path):
    config_file = tmp_path / "config.json"
    write_config(config_file, {"providers": {"deepl": {"api_key": "k"}}})

    loaded = AppConfig.load(str(config_file))

    assert loaded.providers == {"deepl": {"api_key": "k"}}
    assert not (tmp_path / "config.json.pre-providers.bak").exists()


def test_a_blank_legacy_key_does_not_create_an_empty_provider(tmp_path):
    config_file = tmp_path / "config.json"
    write_config(config_file, {"deepl_api_key": "", "microsoft_api_key": "  "})

    loaded = AppConfig.load(str(config_file))

    assert loaded.providers == {}


def test_a_malformed_providers_section_is_replaced_rather_than_crashing(tmp_path, caplog):
    config_file = tmp_path / "config.json"
    write_config(config_file, {"deepl_api_key": "k", "providers": "not-a-dict"})

    loaded = AppConfig.load(str(config_file))

    assert loaded.providers == {"deepl": {"api_key": "k"}}
    assert "malformed" in caplog.text.lower()


def test_providers_survive_a_save_and_load_round_trip(tmp_path):
    config_file = tmp_path / "config.json"
    original = AppConfig(providers={"deepl": {"api_key": "k"}}, translator_priority="deepl")
    original.save(str(config_file))

    assert AppConfig.load(str(config_file)).providers == {"deepl": {"api_key": "k"}}


# ── defects the second review found ──────────────────────────────────────────


def test_a_keyless_provider_survives_being_saved(registry, tmp_path):
    """Its only field is optional, so "has a non-blank value" dropped it from
    the config entirely — and the fallback that needs no account never existed.

    The defect was in the SAVE, so this writes a config and reads it back. The
    three in-memory helpers below were all it used to check, and none of them
    touches the file."""
    register_fake(registry, "freebie", keyless=True)

    config_file = tmp_path / "config.json"
    AppConfig(providers={"freebie": {}}, translator_priority="freebie").save(str(config_file))
    reloaded = AppConfig.load(str(config_file))

    assert "freebie" in reloaded.providers, "the keyless provider did not survive the round trip"
    assert configured_ids(reloaded.providers) == ["freebie"]
    assert any_configured(reloaded.providers) is True
    assert TranslatorService(reloaded.providers).active_ids == ("freebie",)


def test_a_null_provider_entry_is_ignored_and_said_so_without_its_contents(registry, caplog):
    """A hand-edited or newer-build config can carry `"deepl": null`. It must
    not crash, and the complaint must be diagnosable — but a config entry can
    hold a key, so the log names the provider and not the value."""
    register_fake(registry, "alpha")

    with caplog.at_level(logging.WARNING):
        service = TranslatorService({"alpha": None, "beta": {"api_key": "s3cret-value"}})

    assert service.active_ids == ()
    assert configured_ids({"alpha": None}) == []
    assert any_configured({"alpha": None}) is False
    assert "s3cret-value" not in caplog.text


def test_a_provider_entry_of_the_wrong_type_is_ignored(registry):
    register_fake(registry, "alpha")
    for junk in ("a string", 42, ["a", "list"]):
        assert TranslatorService({"alpha": junk}).active_ids == ()


def test_the_failing_backend_id_is_logged_without_its_error_text(registry, caplog):
    """A GET-based provider puts the request URL in its error, and that URL
    carries the message text and any identifying parameter."""
    leaky_error = "url=?q=secret+whisper&de=me@example.com"
    register_fake(registry, "leaky", FakeBackend("leaky", succeeds=False, error=leaky_error))
    register_fake(registry, "good")
    service = TranslatorService({"leaky": {"api_key": "a"}, "good": {"api_key": "b"}}, priority="leaky")

    with caplog.at_level(logging.INFO):
        service.translate("hi", "RU")

    assert "secret+whisper" not in caplog.text
    assert "me@example.com" not in caplog.text
    assert "leaky" in caplog.text


def test_set_listing_order_ignores_names_it_does_not_know(registry):
    register_fake(registry, "alpha")
    register_fake(registry, "beta")

    base.set_listing_order(("beta", "nonesuch", "alpha"))

    assert base.known_ids() == ("beta", "alpha")


def test_set_listing_order_keeps_unlisted_providers_at_the_end(registry):
    register_fake(registry, "alpha")
    register_fake(registry, "beta")
    register_fake(registry, "gamma")

    base.set_listing_order(("gamma",))

    assert base.known_ids() == ("gamma", "alpha", "beta")


def test_set_listing_order_replaces_the_previous_order_rather_than_adding_to_it(registry):
    """Calling it twice with the same argument could not tell idempotence from
    determinism — no intermediate state was ever captured. Two DIFFERENT orders
    can: the second must win outright, with nothing duplicated."""
    register_fake(registry, "alpha")
    register_fake(registry, "beta")
    register_fake(registry, "gamma")

    base.set_listing_order(("gamma", "beta", "alpha"))
    first = base.known_ids()
    base.set_listing_order(("alpha", "gamma", "beta"))
    second = base.known_ids()

    assert first == ("gamma", "beta", "alpha")
    assert second == ("alpha", "gamma", "beta")
    assert len(second) == len(set(second)), "a provider appeared twice"


def test_a_keyless_provider_is_available_without_being_named_in_the_config(registry):
    """The point of a provider that needs no account is that it works before
    anyone configures anything. Requiring an entry meant a user upgrading from a
    DeepL-only config had no fallback at all until they opened Settings and
    pressed Save — and they had no reason to."""
    register_fake(registry, "paid")
    register_fake(registry, "freebie", keyless=True)

    service = TranslatorService({"paid": {"api_key": "k"}})

    assert "freebie" in service.active_ids
    assert service.has_backend is True


def test_a_config_with_nothing_at_all_still_has_the_keyless_fallback(registry):
    register_fake(registry, "freebie", keyless=True)

    assert TranslatorService({}).active_ids == ("freebie",)
    assert TranslatorService(None).has_backend is True


def test_the_preferred_provider_still_wins_over_the_keyless_one(registry):
    """Being always-available must not mean always-first: it is the fallback
    because its quality is below the others."""
    register_fake(registry, "paid")
    register_fake(registry, "freebie", keyless=True)

    service = TranslatorService({"paid": {"api_key": "k"}}, priority="paid")

    assert service.active_ids[0] == "paid"

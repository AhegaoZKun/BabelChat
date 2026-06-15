"""Tests for text utilities (edge cases)."""

from app.text_utils import (
    clean_message_text,
    restore_tokens,
    strip_for_translation,
)


class TestStripForTranslation:
    """Test stripping non-translatable tokens."""

    def test_url_preserved(self):
        text = "Check https://wowhead.com/item/123 for info"
        cleaned, replacements = strip_for_translation(text)
        assert "https://wowhead.com/item/123" not in cleaned
        assert len(replacements) == 1
        restored = restore_tokens("Проверьте __WCT0__ для инфо", replacements)
        assert "https://wowhead.com/item/123" in restored

    def test_wow_marker_preserved(self):
        text = "Focus {skull} and CC {star}"
        cleaned, replacements = strip_for_translation(text)
        assert "{skull}" not in cleaned
        assert "{star}" not in cleaned
        assert len(replacements) == 2

    def test_wow_link_preserved(self):
        text = "Equip |cFFFF8800|Hitem:12345|h[Sword of Truth]|h|r please"
        cleaned, replacements = strip_for_translation(text)
        assert len(replacements) == 1
        assert "|cFFFF8800" not in cleaned

    def test_no_tokens(self):
        text = "Simple message without tokens"
        cleaned, replacements = strip_for_translation(text)
        assert cleaned == text
        assert len(replacements) == 0

    def test_restore_multiple(self):
        text = "{star} kill {skull} then go to https://example.com"
        cleaned, replacements = strip_for_translation(text)
        translated = cleaned.replace("kill", "убить").replace("then go to", "потом идём на")
        restored = restore_tokens(translated, replacements)
        assert "{star}" in restored
        assert "{skull}" in restored
        assert "https://example.com" in restored

    def test_rt_markers(self):
        text = "Focus {rt1} first, then {rt8}"
        cleaned, replacements = strip_for_translation(text)
        assert len(replacements) == 2

    def test_discord_invite_preserved(self):
        # "discord.gg/xyz" must not reach DeepL — ".gg" gets mistranslated as
        # the "good game" abbreviation otherwise.
        text = "join our discord.gg/wowru for raids"
        cleaned, replacements = strip_for_translation(text)
        assert "discord.gg/wowru" not in cleaned
        assert len(replacements) == 1
        restored = restore_tokens(cleaned, replacements)
        assert "discord.gg/wowru" in restored

    def test_bare_discord_domain_preserved(self):
        text = "discord.gg"
        cleaned, replacements = strip_for_translation(text)
        assert "discord.gg" not in cleaned
        assert len(replacements) == 1

    def test_schemeless_domain_with_path_preserved(self):
        text = "guide at wowhead.com/guide/123 here"
        cleaned, replacements = strip_for_translation(text)
        assert "wowhead.com/guide/123" not in cleaned
        assert len(replacements) == 1

    def test_plain_sentence_with_period_not_stripped(self):
        # A period inside a normal sentence must NOT be mistaken for a URL.
        text = "gg everyone. that was fun"
        cleaned, replacements = strip_for_translation(text)
        assert cleaned == text
        assert len(replacements) == 0


class TestCleanMessageText:
    """Test cleaning WoW color codes."""

    def test_removes_color_codes(self):
        text = "|cFF40FF40Green text|r normal"
        cleaned = clean_message_text(text)
        assert "|c" not in cleaned
        assert "|r" not in cleaned
        assert "Green text" in cleaned

    def test_preserves_normal_text(self):
        assert clean_message_text("Normal message") == "Normal message"

    def test_strips_whitespace(self):
        assert clean_message_text("  hello  ") == "hello"



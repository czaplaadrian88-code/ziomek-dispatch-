"""Tests for /pin + /instrukcja_gps commands (sprint 07.05.2026 #5).

Covers:
- courier_info.resolve_courier_query (cid / canonical / dotted / ambig / not-found)
- format_pin_response shape
- format_gps_instruction (sections, length, personalization)
- _handle_pin_command + _handle_gps_instruction_command edge cases
"""
from __future__ import annotations

from dispatch_v2 import courier_info as ci
from dispatch_v2 import telegram_approver as ta


class TestResolveCourier:
    def test_canonical_dotless_match(self):
        n, c, p, a = ci.resolve_courier_query("Bartek O")
        assert n == "Bartek O"
        assert c == 123
        assert p == "4657"
        assert a == []

    def test_dotted_legacy_query_normalizes(self):
        n, c, p, a = ci.resolve_courier_query("Bartek O.")
        assert n == "Bartek O"
        assert c == 123

    def test_cid_lookup(self):
        n, c, p, a = ci.resolve_courier_query("393")
        assert n == "Michał K"
        assert c == 393
        assert p == "9279"

    def test_ambiguous_partial(self):
        n, c, p, a = ci.resolve_courier_query("Dawid")
        assert n is None
        assert len(a) >= 2
        assert any("Dawid" in x for x in a)

    def test_not_found(self):
        n, c, p, a = ci.resolve_courier_query("XyzNotreal999")
        assert n is None
        assert a == []

    def test_empty_query(self):
        n, c, p, a = ci.resolve_courier_query("")
        assert (n, c, p, a) == (None, None, None, [])


class TestFormatPin:
    def test_pin_response_contains_essentials(self):
        out = ci.format_pin_response("Michał K", 393, "9279")
        assert "Michał K" in out
        assert "393" in out
        assert "9279" in out
        assert "Aplikacja" in out

    def test_pin_response_missing_pin_warns(self):
        out = ci.format_pin_response("Ghost", 999, None)
        assert "brak PIN" in out


class TestFormatGpsInstruction:
    def test_generic_template_under_telegram_limit(self):
        out = ci.format_gps_instruction()
        assert len(out.encode("utf-8")) < 4000
        assert "[WSTAW PIN]" in out

    def test_personalized_with_name_and_pin(self):
        out = ci.format_gps_instruction(name="Marcin By", pin="2623")
        assert "Marcin By" in out
        assert "2623" in out
        assert "[WSTAW PIN]" not in out

    def test_all_three_rom_sections_present(self):
        out = ci.format_gps_instruction()
        upper = out.upper()
        assert "XIAOMI" in upper
        assert "COLOROS" in upper or "OPPO" in upper
        assert "HUAWEI" in upper

    def test_critical_steps_present(self):
        out = ci.format_gps_instruction()
        for marker in ["KROK 1", "KROK 2", "KROK 3", "KROK 4", "KROK 5"]:
            assert marker in out

    def test_apk_url_present(self):
        out = ci.format_gps_instruction()
        assert ci.APK_URL in out


class TestPinCommandHandler:
    def test_pin_with_name(self):
        r = ta._handle_pin_command("/pin Bartek O")
        assert "Bartek O" in r
        assert "4657" in r

    def test_pin_with_cid(self):
        r = ta._handle_pin_command("/pin 393")
        assert "Michał K" in r
        assert "9279" in r

    def test_pin_no_arg_returns_usage(self):
        r = ta._handle_pin_command("/pin")
        assert "Użycie" in r or "użycie" in r.lower()

    def test_pin_not_found(self):
        r = ta._handle_pin_command("/pin XyzNotreal")
        assert "Nie znaleziono" in r

    def test_pin_ambiguous_lists_options(self):
        r = ta._handle_pin_command("/pin Dawid")
        assert "pasuje do kilku" in r


class TestGpsInstructionHandler:
    def test_no_arg_template(self):
        r = ta._handle_gps_instruction_command("/instrukcja_gps")
        assert "KROK 1" in r
        assert "[WSTAW PIN]" in r

    def test_with_name_personalized(self):
        r = ta._handle_gps_instruction_command("/instrukcja_gps Bartek O")
        assert "Bartek O" in r
        assert "4657" in r
        assert "[WSTAW PIN]" not in r

    def test_unknown_courier_falls_back_to_template(self):
        r = ta._handle_gps_instruction_command("/instrukcja_gps Xyz999")
        assert "Nie znaleziono" in r
        assert "KROK 1" in r

    def test_ambiguous_courier_returns_disambig(self):
        r = ta._handle_gps_instruction_command("/instrukcja_gps Dawid")
        assert "pasuje do kilku" in r

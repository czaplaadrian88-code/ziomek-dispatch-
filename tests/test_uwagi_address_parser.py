"""Tests for uwagi_address_parser. Pure unit tests + empirical fixture replay."""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from dispatch_v2.uwagi_address_parser import ParsedPickup, parse_pickup_from_uwagi


_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "uwagi_firmowe.jsonl",
)


# ---------------------------------------------------------------------------
# Group 1 — P1 STRUCTURED
# ---------------------------------------------------------------------------

def test_p1_company_first_street_second():
    r = parse_pickup_from_uwagi("Odbiór: Drtusz, Wyszyńskiego 2/75")
    assert r is not None
    assert r.street == "Wyszyńskiego"
    assert r.number == "2/75"
    assert r.company is None  # Drtusz is stoplisted
    assert r.confidence == 1.0


def test_p1_street_first_company_second():
    r = parse_pickup_from_uwagi("Odbiór: Wyszyńskiego 2/75, Drtusz")
    assert r is not None
    assert r.street == "Wyszyńskiego"
    assert r.number == "2/75"
    assert r.company is None  # Drtusz is stoplisted
    assert r.confidence == 1.0


def test_p1_with_time_prefix_simple():
    r = parse_pickup_from_uwagi(
        "Odbiór 11:50: Mickiewicza 50, Dzielne Zuchy, "
        "za szlabanem 20 m schody po lewej."
    )
    assert r is not None
    assert r.street == "Mickiewicza"
    assert r.number == "50"
    assert r.company is None  # Dzielne Zuchy stoplisted
    assert r.confidence == 0.8


def test_p1_with_time_range():
    r = parse_pickup_from_uwagi(
        "Odbiór 13:30-14:00: MALI WOJOWNICY\r\n"
        "Dostawa: Mickiewicza 50, Dzielne Zuchy"
    )
    assert r is None


def test_p1_with_time_range_short_form():
    r = parse_pickup_from_uwagi(
        "Odbiór 11-12: Boruty 17, Magazyn FLM, Street Sportm 7 kick."
    )
    assert r is not None
    assert r.street == "Boruty"
    assert r.number == "17"
    assert r.company is None


def test_p1_with_deadline_prefix():
    r = parse_pickup_from_uwagi(
        "Odbiór do 16:00: Gen. Gustawa Orlicz-Dreszera 3 Lokal 1, "
        "Matka Polka Hybrydowa. dopytaj się przy odbiorze czy jest pobranie"
    )
    assert r is not None
    assert r.street == "Gen. Gustawa Orlicz-Dreszera"
    assert r.number == "3 Lokal 1"
    assert r.company is None


def test_p1_lokal_suffix_in_number():
    r = parse_pickup_from_uwagi("Odbiór: Kijowska 7/lok.1, 7 kick")
    assert r is not None
    assert r.street == "Kijowska"
    assert r.number == "7/lok.1"
    assert r.company is None


def test_p1_letter_suffix_in_number():
    # Empirical fixture — narrative form (Odbierasz z ...).
    r = parse_pickup_from_uwagi(
        "Odbierasz z Mickiewicza 43C i wieziesz na mickiewicza 50) - "
        "Zerknij na grupę"
    )
    assert r is not None
    assert r.street == "Mickiewicza"
    assert r.number == "43C"


def test_p1_slash_in_number():
    r = parse_pickup_from_uwagi("Odbiór: Drtusz, Wyszyńskiego 2/75")
    assert r is not None
    assert r.number == "2/75"


def test_p1_simple_no_company():
    r = parse_pickup_from_uwagi("Odbiór: Drtusz, Wyszyńskiego 2/75")
    assert r is not None
    assert r.company is None


# ---------------------------------------------------------------------------
# Group 2 — P2 NARRATIVE
# ---------------------------------------------------------------------------

def test_p2_odbierasz_z_construction():
    r = parse_pickup_from_uwagi(
        "Odbierasz z Mickiewicza 43C i wieziesz na mickiewicza 50) - "
        "Zerknij na grupę"
    )
    assert r is not None
    assert r.street == "Mickiewicza"
    assert r.number == "43C"
    assert r.confidence == 0.5


def test_p2_ze_sklepu_construction():
    r = parse_pickup_from_uwagi(
        "Odbiór ze sklepu Drapieżnik, ul. Gen. Stanisława Maczka 64, "
        "doręczenie do Grzegorz Szymański, Kleosin, Zambrowska 86"
    )
    assert r is not None
    assert r.street == "Gen. Stanisława Maczka"
    assert r.number == "64"
    assert r.confidence in (0.5, 0.8, 1.0)


def test_p2_walizki_z_adresu():
    r = parse_pickup_from_uwagi(
        "Odbiór walizki z adresu Mieszka I 1/51, doręczenie paczki do epaki"
    )
    assert r is not None
    assert r.street == "Mieszka I"
    assert r.number == "1/51"


# ---------------------------------------------------------------------------
# Group 3 — P3 COMPANY-ONLY returns None
# ---------------------------------------------------------------------------

def test_p3_mali_wojownicy_only():
    r = parse_pickup_from_uwagi(
        "Odbiór 13:30-14:00: MALI WOJOWNICY\r\nDostawa: Mickiewicza 50"
    )
    assert r is None


def test_p3_only_stoplisted_company():
    r = parse_pickup_from_uwagi("Odbiór: Drtusz\r\nDostawa: ...")
    assert r is None


# ---------------------------------------------------------------------------
# Group 4 — Edge cases
# ---------------------------------------------------------------------------

def test_none_input():
    assert parse_pickup_from_uwagi(None) is None


def test_empty_string():
    assert parse_pickup_from_uwagi("") is None


def test_whitespace_only():
    assert parse_pickup_from_uwagi("   \r\n  ") is None


def test_no_odbior_keyword():
    assert parse_pickup_from_uwagi("Hello world") is None


def test_pickup_line_truncates_at_dostawa():
    r = parse_pickup_from_uwagi(
        "Odbiór: Mickiewicza 50\r\nDostawa: Senatorska 99"
    )
    assert r is not None
    assert r.street == "Mickiewicza"
    assert r.number == "50"


# ---------------------------------------------------------------------------
# Group 5 — Plausibility
# ---------------------------------------------------------------------------

def test_stoplist_rejects_company_name():
    # "MALI WOJOWNICY 5" — multi-word stoplist entry should reject.
    r = parse_pickup_from_uwagi("Odbiór: MALI WOJOWNICY 5")
    if r is not None:
        # Acceptable alternative: street rejected, no street candidate.
        assert r.street.casefold() != "mali wojownicy"


def test_too_short_street_rejected():
    r = parse_pickup_from_uwagi("Odbiór: A 5")
    assert r is None


def test_pure_numeric_rejected():
    r = parse_pickup_from_uwagi("Odbiór: 5 5")
    assert r is None


# ---------------------------------------------------------------------------
# Group 6 — Canonicalization
# ---------------------------------------------------------------------------

def test_strip_ul_prefix():
    r = parse_pickup_from_uwagi("Odbiór: ul. Mickiewicza 50")
    assert r is not None
    assert r.street == "Mickiewicza"
    assert "ul." not in r.street.lower()


def test_preserve_gen_prefix():
    r = parse_pickup_from_uwagi("Odbiór: Gen. Maczka 5")
    assert r is not None
    assert r.street.startswith("Gen.")


def test_titlecase_uppercase_street():
    r = parse_pickup_from_uwagi("Odbiór: BORUTY 17")
    assert r is not None
    assert r.street == "Boruty"


# ---------------------------------------------------------------------------
# Group 7 — Empirical fixture replay (parametrized)
# ---------------------------------------------------------------------------

def _load_fixtures():
    fixtures = []
    if not os.path.exists(_FIXTURE_PATH):
        return fixtures
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            fixtures.append(json.loads(line))
    return fixtures


@pytest.mark.parametrize("fx", _load_fixtures())
def test_fixture_no_crash(fx):
    uwagi = fx.get("uwagi", "")
    result = parse_pickup_from_uwagi(uwagi)
    assert result is None or isinstance(result, ParsedPickup)


# ---------------------------------------------------------------------------
# Group 8 — ParsedPickup dataclass
# ---------------------------------------------------------------------------

def test_parsed_pickup_frozen():
    assert dataclasses.is_dataclass(ParsedPickup)
    inst = ParsedPickup(
        street="Test",
        number="1",
        company=None,
        raw_pickup_line="Test 1",
        confidence=1.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        inst.street = "Other"  # type: ignore[misc]


def test_parsed_pickup_confidence_in_range():
    samples = [
        "Odbiór: Drtusz, Wyszyńskiego 2/75",
        "Odbiór 11:50: Mickiewicza 50, Dzielne Zuchy",
        "Odbierasz z Mickiewicza 43C i wieziesz",
        "Odbiór: Boruty 17",
        "Odbiór walizki z adresu Mieszka I 1/51, doręczenie do epaki",
    ]
    for s in samples:
        r = parse_pickup_from_uwagi(s)
        if r is not None:
            assert 0.0 <= r.confidence <= 1.0

"""Regresja matchera grafiku — kolizje IMIENIA + pierwszej litery nazwiska (2026-06-19, #195).

Kontekst: `schedule_utils.match_courier_strict` dopasowywał nazwisko po SAMYM
pierwszym inicjale. Gdy dwie osoby dzieliły imię + pierwszą literę nazwiska, a w
grafiku była tylko JEDNA z nich, matcher fałszywie przypisywał tożsamość tej jednej:
  - "Marcin Bystrowski" → "Marcin Puszko"  (B≠P, ale jedyny "Marcin" w grafiku)
  - "Dawid Krajewski"  → "Dawid Kalinowski" (Kr vs Ka)
  - "Gabriel Jedynak"  → "Gabriel Januszko" (Je vs Ja)
  - "Michał Rogucki"   → "Michał Romańczuk" (Ro vs Ro)
Skutek: kurier SPOZA dzisiejszego grafiku dziedziczył cudzą zmianę → wpadał do
floty/propozycji (incydent Bystrowskiego w propozycjach Ziomka).

Fix: dopasowanie nazwiska po PREFIKSIE całego podanego członu (z ASCII-fold polskich
znaków), nie po inicjale. Prefiks jest ściśle bardziej dyskryminujący niż inicjał:
skróty planszy gastro ("Michał K", "Dawid Kr", "Paweł SC") nadal trafiają, a pełne
nazwiska spoza grafiku → None (poprawne wykluczenie zamiast kradzieży tożsamości).

Ten test używa SYNTETYCZNYCH grafików (nie żywych plików produkcyjnych) → deterministyczny.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2")

import pytest

import schedule_utils as su


def _sched(*names, start="10:00", end="20:00"):
    """Buduje grafik {pełna_nazwa: {start,end}} z listy nazw."""
    return {n: {"start": start, "end": end} for n in names}


# ---------------------------------------------------------------------------
# 1) Kolizja inicjału, tylko JEDNA osoba w grafiku → druga NIE kradnie tożsamości
#    (rdzeń buga; każdy przypadek MUSI dać None, nie cudzą nazwę)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("absent, present", [
    ("Marcin Bystrowski", "Marcin Puszko"),      # B vs P
    ("Dawid Krajewski",   "Dawid Kalinowski"),   # Kr vs Ka
    ("Dawid Charytoniuk", "Dawid Kalinowski"),   # Ch vs Ka
    ("Gabriel Jedynak",   "Gabriel Januszko"),   # Je vs Ja
    ("Michał Rogucki",    "Michał Romańczuk"),   # Ro vs Ro (ten sam inicjał!)
    ("Jakub Wysocki",     "Jakub Leoniuk"),       # W vs L
    ("Jakub Olchowik",    "Jakub Leoniuk"),       # O vs L
])
def test_absent_courier_does_not_steal_present_identity(absent, present):
    sched = _sched(present)
    assert su.match_courier(absent, sched) is None, (
        f"{absent!r} NIE może zmapować się na {present!r} (różne nazwisko, "
        f"nieobecny w grafiku)"
    )
    # osoba obecna trafia siebie
    assert su.match_courier(present, sched) == present


# ---------------------------------------------------------------------------
# 2) OBIE kolidujące osoby w grafiku → każda trafia SIEBIE (nie pierwszą z brzegu)
#    (żywy grafik ma zwykle jedną — ten case łapie regresję, której produkcja nie pokaże)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("Gabriel Jedynak",  "Gabriel Januszko"),
    ("Dawid Krajewski",  "Dawid Kalinowski"),
    ("Michał Rogucki",   "Michał Romańczuk"),
    ("Marcin Bystrowski", "Marcin Puszko"),
])
def test_both_present_each_matches_self(a, b):
    sched = _sched(a, b)
    assert su.match_courier(a, sched) == a
    assert su.match_courier(b, sched) == b


# ---------------------------------------------------------------------------
# 3) Skróty planszy gastro rozróżniają poprawnie GDY obie osoby obecne
# ---------------------------------------------------------------------------

def test_panel_abbreviations_disambiguate_when_both_present():
    sched = _sched("Dawid Krajewski", "Dawid Kalinowski")
    assert su.match_courier("Dawid Kr", sched) == "Dawid Krajewski"
    assert su.match_courier("Dawid Kal", sched) == "Dawid Kalinowski"

    sched2 = _sched("Gabriel Jedynak", "Gabriel Januszko")
    assert su.match_courier("Gabriel Je", sched2) == "Gabriel Jedynak"
    assert su.match_courier("Gabriel Ja", sched2) == "Gabriel Januszko"

    sched3 = _sched("Marcin Bystrowski", "Marcin Puszko")
    assert su.match_courier("Marcin By", sched3) == "Marcin Bystrowski"
    assert su.match_courier("Marcin Pu", sched3) == "Marcin Puszko"


def test_abbreviation_is_none_when_owner_absent():
    # "Marcin By" (skrót Bystrowskiego) NIE może spaść na Puszkę
    assert su.match_courier("Marcin By", _sched("Marcin Puszko")) is None


# ---------------------------------------------------------------------------
# 4) ASCII-fold polskich diakrytyków — skrót ASCII vs nazwisko z diakrytykiem
#    ("Paweł SC" → "Paweł Ściepko"); regresja 1. wersji prefiksu
# ---------------------------------------------------------------------------

def test_ascii_fold_abbreviation_matches_diacritic_surname():
    sched = _sched("Paweł Ściepko")
    assert su.match_courier("Paweł SC", sched) == "Paweł Ściepko"
    assert su.match_courier("Paweł Ściepko", sched) == "Paweł Ściepko"


def test_ascii_fold_does_not_overmatch_different_surname():
    # ASCII-fold NIE może wskrzesić buga: "Marcin By" vs fold("Puszko")="puszko" → None
    assert su.match_courier("Marcin By", _sched("Marcin Puszko")) is None


def test_ascii_fold_helper():
    assert su._ascii_fold("Ściepko") == "sciepko"
    assert su._ascii_fold("Jabłoński") == "jablonski"
    assert su._ascii_fold("Łukasz") == "lukasz"
    assert su._ascii_fold("Romańczuk") == "romanczuk"


# ---------------------------------------------------------------------------
# 5) Klucz jednoczłonowy grafiku ("Adrian") — zachowany (zero regresji skrótów)
# ---------------------------------------------------------------------------

def test_single_token_schedule_key_preserved():
    sched = _sched("Adrian")  # grafik trzyma samo imię
    assert su.match_courier("Adrian Rutkowski", sched) == "Adrian"
    assert su.match_courier("Adrian", sched) == "Adrian"


def test_direct_full_key_hit_wins():
    sched = _sched("Adrian", "Adrian Rutkowski")
    # pełna nazwa jest kluczem → zwraca siebie (direct hit przed fuzzy)
    assert su.match_courier("Adrian Rutkowski", sched) == "Adrian Rutkowski"


# ---------------------------------------------------------------------------
# 6) Prawdziwa wieloznaczność (imię + ta sama litera nazwiska, OBIE obecne,
#    podany tylko inicjał) → None, NIE ciche wybranie pierwszej (landmine "Jakub OL")
# ---------------------------------------------------------------------------

def test_ambiguous_bare_initial_returns_none():
    sched = _sched("Rafał Jabłoński", "Rafał Jankowski")  # oba na "Ja..."
    assert su.match_courier("Rafał J", sched) is None        # 'j' pasuje do obu
    # dłuższy skrót rozstrzyga
    assert su.match_courier("Rafał Jab", sched) == "Rafał Jabłoński"
    assert su.match_courier("Rafał Jan", sched) == "Rafał Jankowski"


def test_ambiguous_bare_firstname_returns_none():
    # UWAGA: imię musi być SPOZA PANEL_TO_SCHEDULE (np. "Gabriel" jest tam zmapowane
    # ręcznie → override wygrywa, nie ambiguity). "Bartosz" nie jest override'owany.
    assert "Bartosz" not in su.PANEL_TO_SCHEDULE
    sched = _sched("Bartosz Klejna", "Bartosz Choiński")
    assert su.match_courier("Bartosz", sched) is None  # samo imię, 2 Bartoszów


def test_overridden_bare_firstname_resolves_via_panel_map():
    # Właściwość dopełniająca: bare-imię które JEST w PANEL_TO_SCHEDULE rozwiązuje
    # się przez mapę ręczną PRZED jakimkolwiek dopasowaniem do grafiku.
    if "Gabriel" in su.PANEL_TO_SCHEDULE:
        target = su.PANEL_TO_SCHEDULE["Gabriel"]
        # override wygrywa nawet gdy w grafiku są inni Gabriele
        sched = _sched("Gabriel Jedynak", "Gabriel Januszko")
        assert su.match_courier("Gabriel", sched) == target


def test_bare_firstname_unique_match():
    sched = _sched("Marcin Puszko")
    assert su.match_courier("Marcin", sched) == "Marcin Puszko"


# ---------------------------------------------------------------------------
# 7) PANEL_TO_SCHEDULE override ma pierwszeństwo (nie ruszony fixem)
# ---------------------------------------------------------------------------

def test_panel_to_schedule_override_wins():
    # "Jakub OL" → "Kuba Olchowik" jest w PANEL_TO_SCHEDULE (mapowanie ręczne)
    if "Jakub OL" in su.PANEL_TO_SCHEDULE:
        target = su.PANEL_TO_SCHEDULE["Jakub OL"]
        sched = _sched(target, "Jakub Leoniuk")
        assert su.match_courier("Jakub OL", sched) == target


# ---------------------------------------------------------------------------
# 8) Twarda regresja produkcyjna: Bystrowski NIE wpada, Puszko wpada (snapshot 19.06)
# ---------------------------------------------------------------------------

def test_live_incident_snapshot_2026_06_19():
    # Grafik z incydentu: jedyny "Marcin" = Puszko (Bystrowski spoza grafiku)
    sched = _sched("Marcin Puszko", "Michał Karpiuk", "Dariusz Maruszak")
    assert su.match_courier("Marcin Bystrowski", sched) is None
    assert su.match_courier("Marcin Puszko", sched) == "Marcin Puszko"

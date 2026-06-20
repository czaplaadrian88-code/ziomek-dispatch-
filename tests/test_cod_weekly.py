"""F2.1d COD Weekly — testy jednostkowe.

Uruchomienie:
    /root/.openclaw/venvs/sheets/bin/python3 \\
        -m dispatch_v2.tests.test_cod_weekly

Nie używa pytest (konsystencja ze stylem testów w dispatch_v2/tests/).
"""
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

WARSAW = ZoneInfo("Europe/Warsaw")

_passed = 0
_failed = 0


def _ok(name):
    global _passed
    _passed += 1
    print(f"  [OK] {name}")


def _fail(name, detail=""):
    global _failed
    _failed += 1
    print(f"  [FAIL] {name}: {detail}")


def _hdr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# -------------------------------------------------------------------
# TEST 1: get_previous_closed_week (Warsaw TZ)
# -------------------------------------------------------------------
def test_get_previous_closed_week():
    _hdr("TEST 1: get_previous_closed_week")
    from dispatch_v2.cod_weekly.week_calculator import (
        get_previous_closed_week,
        format_week_for_header,
        parse_override,
    )
    # Piątek 17.04.2026 → previous = 06-12.04
    fri = datetime(2026, 4, 17, 12, 0, tzinfo=WARSAW)
    s, e = get_previous_closed_week(fri)
    assert (s, e) == (date(2026, 4, 6), date(2026, 4, 12)), f"fri: {s},{e}"
    _ok("fri 17.04 → 06-12.04")
    # Poniedziałek 20.04 → previous = 13-19.04
    mon = datetime(2026, 4, 20, 8, 0, tzinfo=WARSAW)
    s, e = get_previous_closed_week(mon)
    assert (s, e) == (date(2026, 4, 13), date(2026, 4, 19)), f"mon: {s},{e}"
    _ok("mon 20.04 → 13-19.04")
    # Niedziela 19.04 → previous = 06-12.04 (dzisiaj nie liczy się jako zamknięty)
    sun = datetime(2026, 4, 19, 22, 0, tzinfo=WARSAW)
    s, e = get_previous_closed_week(sun)
    assert (s, e) == (date(2026, 4, 6), date(2026, 4, 12)), f"sun: {s},{e}"
    _ok("sun 19.04 → 06-12.04")
    # Cross-month: Piątek 01.05 → previous = 20-26.04
    fri_may = datetime(2026, 5, 1, 10, 0, tzinfo=WARSAW)
    s, e = get_previous_closed_week(fri_may)
    assert (s, e) == (date(2026, 4, 20), date(2026, 4, 26)), f"fri_may: {s},{e}"
    _ok("fri 01.05 → 20-26.04")
    # format_week_for_header
    assert format_week_for_header(date(2026, 4, 6), date(2026, 4, 12)) == "06-12.04.2026"
    assert format_week_for_header(date(2026, 3, 30), date(2026, 4, 5)) == "30.03-05.04.2026"
    _ok("format_week_for_header single + cross-month")
    # parse_override validation
    assert parse_override("2026-04-13:2026-04-19") == (date(2026, 4, 13), date(2026, 4, 19))
    try:
        parse_override("2026-04-14:2026-04-20")  # start = wtorek
        _fail("parse_override reject non-Mon")
    except ValueError:
        _ok("parse_override reject non-Mon")
    try:
        parse_override("2026-04-13:2026-04-18")  # 6 dni, nie 7
        _fail("parse_override reject wrong length")
    except ValueError:
        _ok("parse_override reject 6-day range")


# -------------------------------------------------------------------
# TEST 2: find_target_cod_columns
# -------------------------------------------------------------------
def test_find_target_cod_columns():
    _hdr("TEST 2: find_target_cod_columns")
    import pytest
    pytest.importorskip("gspread")  # sheet_writer wymaga gspread (venv dispatch bez)
    from dispatch_v2.cod_weekly.sheet_writer import (
        find_target_cod_columns,
        NoTargetColumnError,
        AmbiguousTargetError,
    )
    # Happy path: target 06-12.04, payday 15-04-2026 → BC (col 54 0-based)
    row1 = [""] * 65
    row2 = [""] * 65
    row1[54] = "Tydzień 2"
    row2[54] = "COD - Transport"
    row1[56] = "15-04-2026"
    row2[56] = "Wypłata"
    row1[57] = "06-12.04.2026"
    row2[57] = "Saldo do przen."
    targets = find_target_cod_columns(row1, row2, date(2026, 4, 6), date(2026, 4, 12))
    assert len(targets) == 1 and targets[0]["col_letter"] == "BC", targets
    assert targets[0]["segment_start"] == date(2026, 4, 6)
    assert targets[0]["segment_end"] == date(2026, 4, 12)
    _ok("happy path 06-12.04 → BC")
    # Split week: 30.03-05.04 → AT (45) + AY (50)
    row1 = [""] * 65
    row2 = [""] * 65
    row2[45] = "COD - Transport"
    row1[47] = "08-04-2026"
    row1[48] = "30-31.03.2026"
    row2[48] = "Saldo do przen."
    row2[50] = "COD - Transport"
    row1[52] = "08-04-2026"
    row1[53] = "01-05.04.2026"
    row2[53] = "Saldo do przen."
    targets = find_target_cod_columns(row1, row2, date(2026, 3, 30), date(2026, 4, 5))
    assert len(targets) == 2, targets
    at = next(t for t in targets if t["col_letter"] == "AT")
    ay = next(t for t in targets if t["col_letter"] == "AY")
    assert at["segment_end"] == date(2026, 3, 31)
    assert ay["segment_start"] == date(2026, 4, 1) and ay["segment_end"] == date(2026, 4, 5)
    _ok("split week 30.03-05.04 → AT + AY")
    # No payday match → NoTargetColumnError
    row1 = [""] * 65
    row2 = [""] * 65
    row2[54] = "COD - Transport"
    row1[56] = "99-99-9999"
    try:
        find_target_cod_columns(row1, row2, date(2026, 4, 13), date(2026, 4, 19))
        _fail("missing payday → expected NoTargetColumnError")
    except NoTargetColumnError:
        _ok("NoTargetColumnError raised when payday absent")
    # Ambiguous: 2 kandydatów dla single-segment
    row1 = [""] * 65
    row2 = [""] * 65
    for col in (50, 54):
        row2[col] = "COD - Transport"
        row1[col + 2] = "15-04-2026"
    try:
        find_target_cod_columns(row1, row2, date(2026, 4, 6), date(2026, 4, 12))
        _fail("2 kandydatów → expected AmbiguousTargetError")
    except AmbiguousTargetError:
        _ok("AmbiguousTargetError raised for 2 candidates single-segment")


# -------------------------------------------------------------------
# TEST 2b: find_target_column_auto + find_target_cod_columns_resilient (E5)
#
# Reprodukcja porażki 2026-05-17/18 ("TARGET COLUMN FAIL" / NoTargetColumnError
# "Brak bloku z payday=…") — człowiek nie zdążył dodać ręcznie daty wypłaty.
# Auto-detect rozpoznaje kolumnę po ZAKRESIE tygodnia (row1 pos+3), który jest
# wypełniany pewniej niż payday. Plus guardy fail-safe: NIGDY zła kolumna.
# -------------------------------------------------------------------
def test_find_target_column_auto():
    _hdr("TEST 2b: find_target_column_auto + resilient (E5 auto-detect)")
    # Venv-agnostic guard (jak w test_cod_weekly_preflight.py): pod pytest
    # (dispatch venv, bez gspread) → skip; pod custom runner (sheets venv,
    # bez pytest, z gspread) → leć dalej i odpal logikę.
    try:
        import pytest
        pytest.importorskip("gspread")  # venv dispatch bez gspread → skip
    except ModuleNotFoundError:
        pass
    from dispatch_v2.cod_weekly.sheet_writer import (
        find_target_column_auto,
        find_target_cod_columns,
        col_idx_to_letter,
        NoTargetColumnError,
        AmbiguousTargetError,
    )
    from dispatch_v2.cod_weekly.run_weekly import find_target_cod_columns_resilient

    def _block(row1, row2, anchor, tydzien, payday, rng):
        """Wstaw blok COD-Transport jak w arkuszu 'Wynagrodzenia Gastro':
        anchor=COD-Transport, +1=Korekty, +2=Wypłata(payday), +3=Saldo(zakres).
        payday/rng = '' symuluje komórkę jeszcze NIE wypełnioną ręcznie.
        """
        row1[anchor] = tydzien
        row2[anchor] = "COD - Transport"
        row1[anchor + 1] = "wypłata z dn."
        row2[anchor + 1] = "Korekty"
        row1[anchor + 2] = payday
        row2[anchor + 2] = "Wypłata"
        row1[anchor + 3] = rng
        row2[anchor + 3] = "Saldo do przen."

    # --- Scenariusz 1: PORAŻKA 2026-05-17/18 — payday-cell PUSTY, zakres OBECNY.
    # Tydzień 11-17.05.2026, payday 20-05-2026 (środa). Sheet ma zakres '11-17.05.2026'
    # w pos+3 ale pos+2 (Wypłata) jest pusty → primary NoTargetColumnError, auto OK.
    row1 = [""] * 90
    row2 = [""] * 90
    _block(row1, row2, 79, "Tydzień 3", "", "11-17.05.2026")  # CB idx 79, payday BLANK
    # primary musi failować (brak payday)
    try:
        find_target_cod_columns(row1, row2, date(2026, 5, 11), date(2026, 5, 17))
        _fail("primary should fail when payday blank")
    except NoTargetColumnError:
        _ok("primary NoTargetColumnError gdy payday-cell pusty (repro 05-17/18)")
    # auto-detect po zakresie → CB
    targets = find_target_column_auto(row1, row2, date(2026, 5, 11), date(2026, 5, 17))
    if len(targets) == 1 and targets[0]["col_letter"] == "CB":
        _ok("auto-detect rozwiązuje 11-17.05 → CB (po zakresie)")
    else:
        _fail("auto-detect 11-17.05 → CB", targets)
    assert targets[0]["segment_start"] == date(2026, 5, 11)
    assert targets[0]["segment_end"] == date(2026, 5, 17)
    assert targets[0]["payday"] == date(2026, 5, 20), targets[0]["payday"]
    _ok("auto-detect payday policzony 20-05-2026 (środa po niedzieli)")
    # resilient łapie to samo (fallback po porażce primary)
    rtargets = find_target_cod_columns_resilient(
        row1, row2, date(2026, 5, 11), date(2026, 5, 17)
    )
    if len(rtargets) == 1 and rtargets[0]["col_letter"] == "CB":
        _ok("resilient fallback → CB (primary fail → auto)")
    else:
        _fail("resilient fallback → CB", rtargets)

    # --- Scenariusz 2: BEHAVIOR-PRESERVING — gdy payday OBECNY, resilient ==
    # primary (identyczny wynik, auto-detect NIE odpala).
    row1 = [""] * 90
    row2 = [""] * 90
    _block(row1, row2, 79, "Tydzień 3", "20-05-2026", "11-17.05.2026")
    prim = find_target_cod_columns(row1, row2, date(2026, 5, 11), date(2026, 5, 17))
    resi = find_target_cod_columns_resilient(
        row1, row2, date(2026, 5, 11), date(2026, 5, 17)
    )
    if prim == resi and resi[0]["col_letter"] == "CB":
        _ok("resilient == primary gdy payday obecny (zero zmian zachowania)")
    else:
        _fail("resilient == primary", f"prim={prim} resi={resi}")

    # --- Scenariusz 3: SPLIT-MONTH auto-detect. Tydzień 30.03-05.04.2026
    # → segmenty '30-31.03.2026' + '01-05.04.2026', OBA payday 08-04-2026.
    # payday-cells PUSTE, ale zakresy obecne → auto rozróżnia po zakresie.
    row1 = [""] * 90
    row2 = [""] * 90
    _block(row1, row2, 45, "Tydzień 6", "", "30-31.03.2026")  # AT idx 45
    _block(row1, row2, 50, "Tydzień 1", "", "01-05.04.2026")  # AY idx 50
    targets = find_target_column_auto(row1, row2, date(2026, 3, 30), date(2026, 4, 5))
    if len(targets) == 2:
        at = next((t for t in targets if t["col_letter"] == "AT"), None)
        ay = next((t for t in targets if t["col_letter"] == "AY"), None)
        if at and ay and at["segment_end"] == date(2026, 3, 31) \
                and ay["segment_start"] == date(2026, 4, 1):
            _ok("split-month auto-detect → AT(30-31.03) + AY(01-05.04)")
        else:
            _fail("split-month columns", targets)
    else:
        _fail("split-month: expected 2 targets", targets)

    # --- Scenariusz 4 (FAIL-SAFE): blok NIEPRZYGOTOWANY — payday I zakres PUSTE.
    # Brak treściowego sygnału → auto MUSI raise (nie zgaduje po pozycji).
    row1 = [""] * 90
    row2 = [""] * 90
    _block(row1, row2, 79, "Tydzień 3", "", "")  # CB anchor istnieje, ale puste +2/+3
    try:
        find_target_column_auto(row1, row2, date(2026, 5, 11), date(2026, 5, 17))
        _fail("unprepared block → expected NoTargetColumnError")
    except NoTargetColumnError:
        _ok("FAIL-SAFE: pusty payday+zakres → NoTargetColumnError (nie zgaduje)")
    # resilient też musi propagować błąd (nie znajdzie nic)
    try:
        find_target_cod_columns_resilient(
            row1, row2, date(2026, 5, 11), date(2026, 5, 17)
        )
        _fail("resilient unprepared → expected NoTargetColumnError")
    except NoTargetColumnError:
        _ok("FAIL-SAFE: resilient propaguje NoTargetColumnError")

    # --- Scenariusz 5 (FAIL-SAFE): zakres pasuje, ale payday-cell trzyma INNĄ
    # ważną datę (blok rozjechany / nie nasz tydzień). NIE wolno nadpisać po cichu.
    row1 = [""] * 90
    row2 = [""] * 90
    # zakres 11-17.05 ale payday wpisany BŁĘDNIE 13-05-2026 (≠ oczekiwany 20-05)
    _block(row1, row2, 79, "Tydzień 3", "13-05-2026", "11-17.05.2026")
    try:
        find_target_column_auto(row1, row2, date(2026, 5, 11), date(2026, 5, 17))
        _fail("misaligned payday → expected NoTargetColumnError")
    except NoTargetColumnError:
        _ok("FAIL-SAFE: zakres OK ale payday inny → odrzucony → NoTargetColumnError")

    # --- Scenariusz 6 (FAIL-SAFE): DWIE kolumny z tym samym zakresem → ambiguous.
    row1 = [""] * 90
    row2 = [""] * 90
    _block(row1, row2, 75, "Tydzień 2", "", "11-17.05.2026")  # BX idx 75
    _block(row1, row2, 79, "Tydzień 3", "", "11-17.05.2026")  # CB idx 79 — duplikat
    try:
        find_target_column_auto(row1, row2, date(2026, 5, 11), date(2026, 5, 17))
        _fail("duplicate range → expected AmbiguousTargetError")
    except AmbiguousTargetError:
        _ok("FAIL-SAFE: 2 kolumny z tym samym zakresem → AmbiguousTargetError")

    # --- Scenariusz 7 (FAIL-SAFE): payday obecny ale BŁĘDNY + zakres też nie ma
    # w arkuszu → ambiguous z primary NIE jest tłumiony (resilient nie łapie
    # AmbiguousTargetError, tylko NoTargetColumnError).
    row1 = [""] * 90
    row2 = [""] * 90
    # 2 bloki z tym samym payday (single-segment week) → primary AmbiguousTargetError
    for anchor in (75, 79):
        _block(row1, row2, anchor, "T", "20-05-2026", "")
    try:
        find_target_cod_columns_resilient(
            row1, row2, date(2026, 5, 11), date(2026, 5, 17)
        )
        _fail("resilient should NOT swallow AmbiguousTargetError")
    except AmbiguousTargetError:
        _ok("FAIL-SAFE: resilient NIE tłumi AmbiguousTargetError z primary")


# -------------------------------------------------------------------
# TEST 3: _parse_zl (regex + polish/eng format)
# -------------------------------------------------------------------
def test_parse_panel_sums():
    _hdr("TEST 3: _parse_zl (panel number formats)")
    from dispatch_v2.cod_weekly.panel_scraper import _parse_zl
    cases = [
        ("343.96", 343.96),
        ("87.00", 87.0),
        ("0,87", 0.87),
        ("1 234,56", 1234.56),
        ("2.408,44", 2408.44),
        ("9.286,43", 9286.43),
        ("54,93", 54.93),
        ("1,234.56", 1234.56),
        (" 0,87 ", 0.87),
        ("0.00", 0.0),
        ("", 0.0),
        ("10 000,00", 10000.0),
    ]
    for raw, exp in cases:
        got = _parse_zl(raw)
        if got == exp:
            _ok(f"_parse_zl({raw!r}) = {got}")
        else:
            _fail(f"_parse_zl({raw!r})", f"got {got}, exp {exp}")


# -------------------------------------------------------------------
# TEST 4: restaurant matching (A/B/C/D strategy)
# -------------------------------------------------------------------
def test_fuzzy_restaurant_match():
    _hdr("TEST 4: match_restaurants (alias / strict / token / startswith)")
    from dispatch_v2.cod_weekly.restaurant_mapper import match_restaurants, normalize
    panel = {
        "Arsenal Panteon": 14,
        "Baanko": 66,
        "Restauracja Eatally": 182,
        "Restauracja Kumar&#039;s": 106,
        "Nago Kwestia Czasu": 228,
        # De-erozja 2026-06-13: panel cid=207 przemianowany "HoNoTu" → "Sztuka Dzika"
        # (Adrian 25.05), dodano ALIAS_MAP["HoNoTu"]="Sztuka Dzika". Fixture panelu
        # musi mieć NOWĄ nazwę, inaczej _resolve_alias("Sztuka Dzika") → KeyError.
        "Sztuka Dzika": 207,
        "_350 Stopni KILIŃSKIEGO": 114,
        "_500 stopni": 28,
        "Mama Thai Bistro": 154,
        "Miejska Miska": 215,
        "Trzy Po Trzy Mickiewicza": 177,
        "Trzy Po Trzy Sienkiewicza": 190,
        "Pruszynka NIEAKTYWNE": 189,
        "Pruszynka Restauracja": 196,
        "Goodboy": 191,
    }
    sheet = [
        (3, "Arsenał Panteon"),
        (4, "Bankoo"),              # alias literówka
        (5, "Eatally"),             # strip "Restauracja "
        (6, "Kumar's"),             # HTML entity + prefix
        (7, "Nago"),                # token
        (8, "HoNoTu"),              # startswith
        (9, "350 stopni"),          # alias (fuzzy pomyłka by zmapował do _500)
        (10, "Mama Thai Bistro i Miejska Miska"),  # alias multi-company
        (11, "Trzy po trzy MIC"),   # alias skrót
        (12, "Pruszynka"),          # prefer non-NIEAKTYWNE
        (13, "Good Boy"),           # alias spacja
    ]
    res = match_restaurants(sheet, panel)
    mapping = res["mapping"]
    method = res["method_per_entry"]
    expectations = [
        ("Arsenał Panteon", 14, "strict"),
        ("Bankoo", 66, "alias"),
        ("Eatally", 182, "strict"),
        ("Kumar's", 106, "strict"),
        ("Nago", 228, "token"),
        # De-erozja 2026-06-13: "HoNoTu" mapuje się teraz przez ALIAS_MAP→"Sztuka Dzika"
        # (cid=207), nie strict (panel przemianowany 25.05).
        ("HoNoTu", 207, "alias"),
        ("350 stopni", 114, "alias"),
        ("Mama Thai Bistro i Miejska Miska", [154, 215], "alias"),
        ("Trzy po trzy MIC", 177, "alias"),
        ("Pruszynka", 196, "strict"),
        ("Good Boy", 191, "alias"),
    ]
    for name, exp_val, exp_method in expectations:
        got_val = mapping.get(name, "MISSING")
        got_method = method.get(name, "MISSING")
        if got_val == exp_val:
            _ok(f"match {name!r} → {got_val} via {got_method}")
        else:
            _fail(f"match {name!r}", f"got {got_val}, exp {exp_val}")
    # Dodatkowo: normalize stability
    assert normalize("Arsenał Panteon") == normalize("Arsenal Panteon")
    assert normalize("Sweet Fit & Eat") == normalize("Sweet Fit &amp; Eat")
    _ok("normalize stable across diacritics + HTML entities")


# -------------------------------------------------------------------
# TEST 5: compute_cod (Arsenał, Chicago, zero-case)
# -------------------------------------------------------------------
def test_cod_formula():
    _hdr("TEST 5: compute_cod formula")
    from dispatch_v2.cod_weekly.panel_scraper import compute_cod
    # Arsenał 06-12.04: 87 - 343.96 - 0.87 = -257.83
    arsenal = {"przesylki": 343.96, "pobrania": 87.0, "prowizja": 0.87}
    r = compute_cod(arsenal)
    assert r == -257.83, f"Arsenał: {r}"
    _ok(f"Arsenał Panteon: {r} == -257.83")
    # Chicago 06-12.04: 9286.43 - 2408.44 - 54.93 = 6823.06
    chicago = {"przesylki": 2408.44, "pobrania": 9286.43, "prowizja": 54.93}
    r = compute_cod(chicago)
    assert r == 6823.06, f"Chicago: {r}"
    _ok(f"Chicago Pizza: {r} == +6823.06")
    # Zero (0 orders)
    zero = {"przesylki": 0.0, "pobrania": 0.0, "prowizja": 0.0}
    assert compute_cod(zero) == 0.0
    _ok("Zero case: COD = 0")


# -------------------------------------------------------------------
# TEST 6: sheet_writer — skip-filled + empty validation
# -------------------------------------------------------------------
def test_validate_target_column():
    _hdr("TEST 6: write_cod_column_skip_filled + validate_column_empty_ratio")
    import pytest
    pytest.importorskip("gspread")  # sheet_writer wymaga gspread (venv dispatch bez)
    from dispatch_v2.cod_weekly.sheet_writer import (
        write_cod_column_skip_filled,
        validate_column_empty_ratio,
    )

    # Mock worksheet
    class FakeWS:
        def __init__(self, existing: dict):
            """existing: {row_1based: value}."""
            self._data = existing
            self.batch_update_called = False
            self.last_updates = None

        def batch_get(self, ranges):
            out = []
            for rng in ranges:
                # Parse "BC3:BC68"
                import re as _re
                m = _re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
                if not m:
                    out.append([])
                    continue
                lo = int(m.group(2))
                hi = int(m.group(4))
                rows = []
                for r in range(lo, hi + 1):
                    v = self._data.get(r, "")
                    rows.append([v] if v else [])
                out.append(rows)
            return out

        def batch_update(self, updates, value_input_option=None):
            self.batch_update_called = True
            self.last_updates = updates
            for u in updates:
                import re as _re
                m = _re.match(r"([A-Z]+)(\d+)", u["range"])
                row = int(m.group(2))
                self._data[row] = u["values"][0][0]

    # Scenariusz 1: write 3 nowe, 1 skip (user input istnieje)
    ws = FakeWS(existing={5: "999,99"})
    row_to_value = {3: -257.83, 4: 100.50, 5: 888.88, 6: 42.00}
    res = write_cod_column_skip_filled(ws, "BC", row_to_value, dry_run=False)
    assert len(res["written_rows"]) == 3, res
    assert len(res["skipped_filled"]) == 1, res
    assert res["skipped_filled"][0]["row"] == 5
    assert ws._data[5] == "999,99"  # nie nadpisano
    assert ws._data[3] == -257.83
    _ok("skip-already-filled: 3 written, 1 skipped")

    # Scenariusz 2: dry_run=True → nic nie zapisane
    ws = FakeWS(existing={})
    res = write_cod_column_skip_filled(ws, "BC", {3: 1.0, 4: 2.0}, dry_run=True)
    assert not ws.batch_update_called
    assert len(res["written_rows"]) == 2  # planned
    _ok("dry_run=True: planned but NOT executed")

    # Scenariusz 3: validate_column_empty_ratio
    # Arkusz z 2 wypełnionymi na 10 wierszy → 80% pustych (OK)
    ws = FakeWS(existing={3: "x", 4: "y"})
    v = validate_column_empty_ratio(ws, "BC", list(range(3, 13)), threshold=0.8)
    assert v["ok"] is True, v
    assert v["empty_count"] == 8 and v["total"] == 10
    _ok(f"80%% empty → OK ({v['ratio']:.0%})")

    # 3 wypełnione / 10 = 70% pustych → FAIL
    ws = FakeWS(existing={3: "x", 4: "y", 5: "z"})
    v = validate_column_empty_ratio(ws, "BC", list(range(3, 13)), threshold=0.8)
    assert v["ok"] is False, v
    _ok(f"70%% empty → FAIL ({v['ratio']:.0%})")


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
def main():
    for fn in [
        test_get_previous_closed_week,
        test_find_target_cod_columns,
        test_find_target_column_auto,
        test_parse_panel_sums,
        test_fuzzy_restaurant_match,
        test_cod_formula,
        test_validate_target_column,
    ]:
        try:
            fn()
        except AssertionError as e:
            _fail(fn.__name__, f"AssertionError: {e}")
        except Exception as e:
            _fail(fn.__name__, f"{type(e).__name__}: {e}")
    print(f"\n{'=' * 70}")
    print(f"PASSED: {_passed}    FAILED: {_failed}")
    print(f"{'=' * 70}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

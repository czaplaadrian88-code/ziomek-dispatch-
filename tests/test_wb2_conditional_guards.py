"""WB2 — guardy WARUNKOWE warstwy P-1 okna odbioru (incydent CZASY 492).

Bramka jakości dla `docs/WB2_CONDITIONAL_GUARDS.md`. Trzon to NEGATYWNY ORACLE
na zamrożonym incydencie z 2026-07-27 16:09:12Z: fixture
`tests/fixtures/wb2_incident_492_20260727T160912Z.json` odtwarza wejścia warstwy
i jest SAMOWALIDUJĄCY — pierwszy test dowodzi, że przy guardach OFF silnik
produkuje dokładnie ten wiersz ledgera, który zapisał się na żywo (ta sama
sekwencja, te same `viol`, ta sama delta jazdy, ta sama świeżość). Dopiero na
tak zweryfikowanym wejściu ma sens twierdzenie „guardy zabijają incydent".

Fixture jest REKONSTRUKCJĄ, nie kopią: macierz OSRM z 16:09 nie jest nigdzie
retencjonowana. Współrzędne i znaczniki czasu pochodzą z zamrożonego
`orders_state`, a czasy przejazdów zostały wyprowadzone z dwóch równań
narzuconych przez zamrożony wiersz (`base_max_carry` 24,3 i `d_drive` −2,7).
Wniosek uboczny tej rekonstrukcji jest sam w sobie dowodem: zlecenie 490612 NIE
MOGŁO mieć w tamtej chwili `czas_kuriera` (klamra `committed` na pierwszym stopie
dawałaby świeżość ~54 min zamiast zapisanych 28,5) — slot 18:39, który zobaczył
kurier, dokleiła dopiero warstwa prezentacji.
"""
import copy
import json
import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import osrm_client
from dispatch_v2 import plan_recheck as P
from dispatch_v2.core import carry_freshness as CF
from dispatch_v2.core import lex_window_guards as G
from dispatch_v2.core import lex_window_ledger as LWL
from dispatch_v2.core import loadgov_snapshot as LG

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "wb2_incident_492_20260727T160912Z.json")


def _load():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def fx():
    return _load()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Zero dotknięć żywego stanu: ledger na tmp, OSRM zamockowany per test.

    Kill-switch ledgera przypięty do STAŁEJ MODUŁU. `ENABLE_LEX_WINDOW_LEDGER_V2`
    nie należy do `ETAP4_DECISION_FLAGS` (spec WB1 §6.1), a `_isolate_flags_json`
    wycina z tmp-kopii wyłącznie `ETAP4_DECISION_FLAGS`,
    `FLAGS_JSON_NUMERIC_OVERRIDES` i `TEST_ISOLATED_INFRA_FLAGS` — więc żywa
    wartość przechodziła przez sito i wygrywała nad stałą ustawianą przez test.
    Pin idzie na `ledger_v2_enabled()`, bo to jedyny czytnik tej flagi w module.
    Efekt: wynik nie zależy od stanu `flags.json` ownera (zielone przy `True`
    i przy `False`), a testy sterują wersją ledgera przez stałą, jak zamierzono.
    """
    monkeypatch.setattr(LWL, "LEGACY_V1_PATH", str(tmp_path / "v1.jsonl"))
    monkeypatch.setattr(LWL, "CANONICAL_PATH", str(tmp_path / "v2.jsonl"))
    monkeypatch.setattr(LWL, "OBSERVATION_PATH", str(tmp_path / "v2_obs.jsonl"))
    monkeypatch.setattr(LG, "SNAPSHOT_PATH", str(tmp_path / "loadgov.json"))
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False, raising=False)
    monkeypatch.setattr(
        LWL, "ledger_v2_enabled",
        lambda: bool(getattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False)))
    LWL.reset_state()
    # Progi startowe = decyzja ownera D2 (2026-07-27).
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", True)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", True)
    monkeypatch.setattr(P, "ENABLE_CARRIED_AGE_TZ_FIX", True)
    monkeypatch.setattr(P, "LEX_WINDOW_TOL_MIN", 5.0)
    monkeypatch.setattr(P, "LEX_WINDOW_DELAY_TOL_MIN", 3.0)
    monkeypatch.setattr(P, "LEX_WINDOW_MAX_STOPS", 8)
    monkeypatch.setattr(C, "LEX_WINDOW_DELAY_TOL_MIN", 3.0)
    monkeypatch.setattr(C, "LEX_WINDOW_CARRY_CAP_MIN", 35.0)
    monkeypatch.setattr(C, "LEX_WINDOW_CARRY_CAP_ALARM_MIN", 40.0)
    monkeypatch.setattr(C, "LEX_WINDOW_MIN_GAIN_MIN", 1.0)


def _mock_osrm(monkeypatch, fx):
    """Zamrożona macierz przejazdów — indeks 0 = pozycja kuriera, dalej stopy."""
    m = fx["leg_matrix_min"]

    def table(pts_a, pts_b):
        return [[{"duration_s": m[i][j] * 60.0} for j in range(len(pts_b))]
                for i in range(len(pts_a))]

    monkeypatch.setattr(osrm_client, "table", table)


def _run(monkeypatch, fx, *, guards, stops=None, orders=None):
    _mock_osrm(monkeypatch, fx)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", guards)
    return P._lex_committed_window_reorder(
        [dict(s) for s in (stops or fx["stops"])],
        orders or fx["orders_state"],
        tuple(fx["start_pos"]),
        datetime.fromisoformat(fx["now"]))


def _seq(stops):
    return [[s["order_id"], "P" if s["type"] == "pickup" else "D"] for s in stops]


# ─────────────────────────── 1. Fixture sam się waliduje ───────────────────────


def test_fixture_odtwarza_zamrozony_wiersz_ledgera(monkeypatch, fx, caplog):
    """Bez guardów silnik MUSI wyprodukować dokładnie incydent z 27.07.

    To jest warunek wstępny całej reszty pliku: gdyby fixture nie odtwarzał
    zdarzenia, „guardy zabijają incydent" byłoby zdaniem o niczym.
    """
    frozen = fx["frozen_ledger_row"]
    with caplog.at_level("INFO", logger="dispatch.plan_recheck"):
        out = _run(monkeypatch, fx, guards=False)

    assert _seq(out) == frozen["lex_seq"], "kolejność inna niż zapisana na żywo"

    line = [r.getMessage() for r in caplog.records
            if "LEX_COMMITTED_WINDOW base_viol" in r.getMessage()]
    assert line, "warstwa nie zalogowała rozjazdu"
    assert f"base_viol={frozen['base_window_viol']}" in line[0]
    assert f"lex_viol={frozen['lex_window_viol']}" in line[0]
    assert f"d_drive={frozen['d_drive_min']:.1f}" in line[0]


def test_fixture_odtwarza_swiezosc_z_zamrozonego_wiersza(monkeypatch, fx):
    """Świeżość baseline/kandydata co do 0,1 min jak w ledgerze (24,3 → 28,5)."""
    frozen = fx["frozen_ledger_row"]
    _mock_osrm(monkeypatch, fx)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", False)
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False)
    ctx = LWL.writer_context("test", "tick").for_courier("492", 1)
    P._lex_committed_window_reorder(
        [dict(s) for s in fx["stops"]], fx["orders_state"],
        tuple(fx["start_pos"]), datetime.fromisoformat(fx["now"]),
        ledger_ctx=ctx)

    rows = LWL.read_records(LWL.LEGACY_V1_PATH)
    assert rows, "brak wiersza v1"
    got = rows[-1]
    assert got["base_max_carry"] == frozen["base_max_carry"]
    assert got["lex_max_carry"] == frozen["lex_max_carry"]
    assert got["d_drive_min"] == frozen["d_drive_min"]
    assert got["base_seq"] == frozen["base_seq"]
    assert got["lex_seq"] == frozen["lex_seq"]


# ─────────────────────── 2. Negatywny oracle: incydent ginie ───────────────────


def test_incydent_ginie_przy_progach_D2(monkeypatch, fx):
    """Guardy ON przy defaultach ownera ⇒ zostaje baseline (identity).

    Zabójcą jest wspólny guard delty: świeżość 490595 rośnie o 4,2 min przy
    tolerancji 3,0, a wyjątek D1 nie działa, bo okno się NIE poprawia (0→0).
    """
    out = _run(monkeypatch, fx, guards=True)
    assert _seq(out) == fx["frozen_ledger_row"]["base_seq"], \
        "guardy przepuściły przestawienie bez poprawy okna"


def test_powod_odrzucenia_to_delta_a_nie_zysk_jazdy(monkeypatch, fx):
    """Incydent miał zysk 2,7 min > progu 1,0 — więc G3 go NIE zabija.

    Test pilnuje, żeby nikt później nie „naprawił" incydentu podkręcając
    MIN_GAIN: przy wyłączonym guardzie delty przestawienie WRACA.
    """
    monkeypatch.setattr(C, "LEX_WINDOW_DELAY_TOL_MIN", 999.0)
    out = _run(monkeypatch, fx, guards=True)
    assert _seq(out) == fx["frozen_ledger_row"]["lex_seq"], \
        "po zdjęciu guardu delty incydent musi wrócić (dowód, że to on go zabija)"


# ─────────────────── 3. Wyjątek D1 — ścisła poprawa okna przeżywa ──────────────


def _strict_improvement(fx, committed_rel_min=5.0):
    """Wariant fixture, w którym baseline ŁAMIE okno, a przestawienie je naprawia.

    Ta sama geometria; zleceniu 490612 nadajemy `czas_kuriera` tak, by w
    kolejności bazowej odbiór wypadał grubo po oknie, a w przestawionej mieścił
    się w nim. Δświeżość rośnie wtedy do +6,6 min, czyli ponad tolerancję —
    kandydat przeżywa WYŁĄCZNIE dzięki wyjątkowi D1.
    """
    fx = copy.deepcopy(fx)
    now = datetime.fromisoformat(fx["now"])
    ck = now + timedelta(minutes=committed_rel_min)
    fx["orders_state"]["490612"]["czas_kuriera_warsaw"] = \
        ck.astimezone(ZoneInfo("Europe/Warsaw")).isoformat()
    return fx


def test_wyjatek_D1_przepuszcza_scisla_poprawe_okna(monkeypatch, fx):
    fx2 = _strict_improvement(fx)
    out_off = _run(monkeypatch, fx2, guards=False)
    assert _seq(out_off) != _seq(fx2["stops"]), "wariant nie jest przestawieniem"
    out_on = _run(monkeypatch, fx2, guards=True)
    assert _seq(out_on) == _seq(out_off), \
        "guardy zabiły ŚCISŁĄ naprawę okna — dokładnie to, czego zakazuje D1"


def test_wyjatek_D1_nie_zdejmuje_absolutnego_capa(monkeypatch, fx):
    """D1 zawiesza deltę i zysk jazdy, ale NIGDY capa świeżości trybu."""
    fx2 = _strict_improvement(fx)
    monkeypatch.setattr(C, "LEX_WINDOW_CARRY_CAP_MIN", 25.0)  # cap poniżej kandydata
    out = _run(monkeypatch, fx2, guards=True)
    assert _seq(out) == _seq(fx2["stops"]), \
        "kandydat przekroczył absolutny cap i mimo to przeszedł"


def test_wyjatek_D1_wymaga_SCISLEJ_poprawy_a_nie_remisu(monkeypatch, fx):
    """`dviol == bviol` to NIE jest przesłanka — to jest właśnie incydent 492."""
    base = G.Facts(window_viol=2, drive_min=10.0,
                   handoff_by_order={"x": 10.0}, carry_by_order={"x": 10.0})
    remis = G.Facts(window_viol=2, drive_min=9.5,
                    handoff_by_order={"x": 20.0}, carry_by_order={"x": 20.0})
    scislej = G.Facts(window_viol=1, drive_min=9.5,
                      handoff_by_order={"x": 20.0}, carry_by_order={"x": 20.0})
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    r_remis = G.evaluate(base, remis, assigned_ids=[], carried_ids=["x"],
                         thresholds=thr)
    r_scisle = G.evaluate(base, scislej, assigned_ids=[], carried_ids=["x"],
                          thresholds=thr)
    assert not r_remis.admissible and not r_remis.exemption
    assert r_scisle.admissible and r_scisle.exemption


def test_okno_nie_moze_sie_pogorszyc_nigdy(monkeypatch):
    """`W(kand) ≤ W(baseline)` obowiązuje bezwarunkowo (jawny warunek D1)."""
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=0, drive_min=10.0,
                   handoff_by_order={"x": 10.0}, carry_by_order={})
    gorzej = G.Facts(window_viol=1, drive_min=1.0,
                     handoff_by_order={"x": 10.0}, carry_by_order={})
    r = G.evaluate(base, gorzej, assigned_ids=["x"], carried_ids=[], thresholds=thr)
    assert not r.admissible and r.reason == "window_regression"


# ─────────────────────────── 4. G3 — materialny zysk ───────────────────────────


def test_G3_odrzuca_niematerialny_zysk_bez_poprawy_okna(monkeypatch):
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=1, drive_min=10.0,
                   handoff_by_order={"x": 10.0}, carry_by_order={})
    maly = G.Facts(window_viol=1, drive_min=9.5,
                   handoff_by_order={"x": 10.0}, carry_by_order={})
    r = G.evaluate(base, maly, assigned_ids=["x"], carried_ids=[], thresholds=thr)
    assert not r.admissible and r.reason == "g3_gain"


def test_G3_jest_inclusive_na_progu(monkeypatch):
    """`gain >= 1.0` — dokładnie próg PRZECHODZI (spec Sola RUN3-b)."""
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=1, drive_min=10.0,
                   handoff_by_order={"x": 10.0}, carry_by_order={})
    rowno = G.Facts(window_viol=1, drive_min=9.0,
                    handoff_by_order={"x": 10.0}, carry_by_order={})
    assert G.evaluate(base, rowno, assigned_ids=["x"], carried_ids=[],
                      thresholds=thr).admissible


def test_G3_zawieszony_przy_scislej_poprawie_okna():
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=2, drive_min=10.0,
                   handoff_by_order={"x": 10.0}, carry_by_order={})
    gorsza_jazda = G.Facts(window_viol=1, drive_min=14.0,
                           handoff_by_order={"x": 10.0}, carry_by_order={})
    r = G.evaluate(base, gorsza_jazda, assigned_ids=["x"], carried_ids=[],
                   thresholds=thr)
    assert r.admissible and r.verdicts["G3"]["verdict"] == G.EXEMPT


# ───────────────────── 5. G1/G2 — jedna metryka, dwie kohorty ──────────────────


def test_G1_chroni_takze_niesione_a_nie_tylko_assigned():
    """Luka #3 incydentu: stara pętla iterowała WYŁĄCZNIE `assigned`."""
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=1, drive_min=10.0,
                   handoff_by_order={"carried": 5.0}, carry_by_order={"carried": 20.0})
    kand = G.Facts(window_viol=1, drive_min=1.0,
                   handoff_by_order={"carried": 12.0}, carry_by_order={"carried": 27.0})
    r = G.evaluate(base, kand, assigned_ids=[], carried_ids=["carried"],
                   thresholds=thr)
    assert not r.admissible, "niesione znów bez ochrony przed opóźnieniem"


def test_G2_baseline_nad_capem_dopuszcza_tylko_niepogorszenie():
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=2, drive_min=10.0,
                   handoff_by_order={}, carry_by_order={"x": 40.0})
    lepszy = G.Facts(window_viol=1, drive_min=10.0,
                     handoff_by_order={}, carry_by_order={"x": 38.0})
    gorszy = G.Facts(window_viol=1, drive_min=10.0,
                     handoff_by_order={}, carry_by_order={"x": 41.0})
    assert G.evaluate(base, lepszy, assigned_ids=[], carried_ids=["x"],
                      thresholds=thr).admissible
    assert not G.evaluate(base, gorszy, assigned_ids=[], carried_ids=["x"],
                          thresholds=thr).admissible


def test_brak_danych_odrzuca_kandydata_fail_closed():
    thr = G.Thresholds(delay_tol_min=3.0, carry_cap_min=35.0, min_gain_min=1.0)
    base = G.Facts(window_viol=2, drive_min=10.0,
                   handoff_by_order={}, carry_by_order={"x": 10.0})
    bez = G.Facts(window_viol=1, drive_min=1.0,
                  handoff_by_order={}, carry_by_order={"x": None})
    r = G.evaluate(base, bez, assigned_ids=[], carried_ids=["x"], thresholds=thr)
    assert not r.admissible, "brak pomiaru NIE MOŻE oznaczać zgody"


def test_jedna_metryka_handoff_wspolna_z_capz():
    """Koniunkcja G2 i cap-Z: obie warstwy liczą świeżość TYM SAMYM kodem.

    Mutation-surface: `_capz_bag_metrics` i `_facts_of` muszą wołać
    `carry_freshness`. Podmiana agregatu psuje OBIE naraz — to jest dowód, że
    jest jeden predykat, a nie dwa zgodne przypadkiem.
    """
    import inspect

    from dispatch_v2 import route_simulator_v2 as RS

    assert "carry_freshness" in inspect.getsource(RS._capz_bag_metrics)
    assert "_cfresh" in inspect.getsource(P._facts_of)
    # handoff = przyjazd + dwell dostawy (a nie sam przyjazd) — kontrakt metryki
    assert CF.HANDOFF_INCLUDES_DROPOFF_DWELL is True
    assert CF.handoff_min(10.0, 3.5) == 13.5
    assert CF.carry_min(13.5, -17.0) == 30.5
    assert CF.max_carry_min({"a": 3.0, "b": None}) == 3.0
    assert CF.max_carry_min({}) == 0.0


def test_swiezosc_liczona_po_dwellu_nie_na_przyjezdzie(monkeypatch, fx):
    """Ratchet: gdyby G2 wróciło do liczenia na przyjeździe, incydent ożyje.

    Δprzyjazd = 4,2 min i Δhandoff = 4,2 min są tu równe, więc test pilnuje
    samego kontraktu metryki (ten sam zegar co cap-Z), nie liczby.
    """
    import inspect
    src = inspect.getsource(P._facts_of)
    assert "handoff_min" in src and "carry_min" in src


# ───────────────────────── 6. Mutation — każdy guard nośny ────────────────────


@pytest.mark.parametrize("stala,wartosc", [
    ("LEX_WINDOW_DELAY_TOL_MIN", 999.0),   # zdejmij G1/G2-delta
    ("LEX_WINDOW_CARRY_CAP_MIN", 999.0),   # zdejmij cap (sam nie wystarcza)
])
def test_mutation_rozluznienie_progu_przywraca_lub_nie_incydent(
        monkeypatch, fx, stala, wartosc):
    """Rozluźnienie progu delty MUSI przywrócić incydent; capa — NIE.

    Rozróżnienie jest istotne: dowodzi, że incydent ginie na guardzie DELTY,
    a nie „jakoś przy okazji" na absolutnym suficie 35 (kandydat mieścił się
    pod nim: 28,5 < 35 — dokładnie dlatego stary `max(35, bcarry)` go przepuścił).
    """
    monkeypatch.setattr(C, stala, wartosc)
    out = _seq(_run(monkeypatch, fx, guards=True))
    if stala == "LEX_WINDOW_DELAY_TOL_MIN":
        assert out == fx["frozen_ledger_row"]["lex_seq"]
    else:
        assert out == fx["frozen_ledger_row"]["base_seq"]


def test_mutation_usuniecie_wyjatku_D1_zabija_naprawy_okna(monkeypatch, fx):
    """Odwrócenie D1 (guardy bezwarunkowe) MUSI zaczerwienić naprawę okna.

    Symulacja Sola na 561 wierszach: bezwarunkowa delta zabija ≥257/313 ścisłych
    napraw. Tu ten sam efekt na jednym, konkretnym przypadku.
    """
    fx2 = _strict_improvement(fx)
    przed = _seq(_run(monkeypatch, fx2, guards=True))
    assert przed != _seq(fx2["stops"]), "wariant nie testuje wyjątku"

    prawdziwe = G.evaluate

    def bez_wyjatku(baseline, candidate, **kw):
        udawany = G.Facts(window_viol=baseline.window_viol,
                          drive_min=candidate.drive_min,
                          handoff_by_order=candidate.handoff_by_order,
                          carry_by_order=candidate.carry_by_order)
        return prawdziwe(baseline, udawany, **kw)

    monkeypatch.setattr(G, "evaluate", bez_wyjatku)
    monkeypatch.setattr(P._lex_guards, "evaluate", bez_wyjatku)
    po = _seq(_run(monkeypatch, fx2, guards=True))
    assert po == _seq(fx2["stops"]), \
        "bez wyjątku D1 ścisła naprawa okna musi zginąć (to jest cała stawka D1)"


def test_flaga_OFF_jest_bajt_w_bajt_stara_warstwa(monkeypatch, fx):
    """OFF ⇒ zachowanie sprzed WB2 — rollback jest flipem, nie rewertem."""
    assert _seq(_run(monkeypatch, fx, guards=False)) == \
        fx["frozen_ledger_row"]["lex_seq"]


# ─────────────────────── 7. Test 2×2 NONCARRIED × LEX ─────────────────────────


def test_2x2_noncarried_reorder_x_lex_kohorta_carried_bez_zmian(monkeypatch, fx):
    """Carried cohort bajt-identyczna w 4 kombinacjach flag (wymóg 13.2 p.6).

    `_reorder_noncarried_min_drive` ma z definicji nie dotykać worków z
    niesionymi; test pilnuje, żeby guardy WB2 tego nie zmieniły w żadnej
    kombinacji — inaczej mielibyśmy dwa reordery mieszające się o tę samą trasę.
    """
    wyniki = {}
    for noncarried in (False, True):
        for lex in (False, True):
            monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", noncarried)
            monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", lex)
            wyniki[(noncarried, lex)] = _seq(_run(monkeypatch, fx, guards=True))
    for lex in (False, True):
        assert wyniki[(False, lex)] == wyniki[(True, lex)], \
            f"NONCARRIED zmienił worek z niesionymi (lex={lex})"


# ──────────────────────────── 8. G5 — strict-stub ─────────────────────────────


def test_G5_brak_snapshotu_to_strict(monkeypatch):
    now = datetime.now(timezone.utc)
    snap, meta = LG.read_snapshot(now)
    assert snap is None and meta["source"] == "absent"
    tol, src = LG.window_tol_min(now, snapshot=None)
    assert tol == 5.0 and src == "strict_no_snapshot"


def test_G5_snapshot_niekompletny_to_strict(monkeypatch, tmp_path):
    p = tmp_path / "loadgov.json"
    p.write_text(json.dumps({"ewma": 9.9}), encoding="utf-8")
    snap, meta = LG.read_snapshot(datetime.now(timezone.utc), path=str(p))
    assert snap is None and meta["source"] == "incomplete"


def test_G5_snapshot_przeterminowany_to_strict(tmp_path):
    now = datetime.now(timezone.utc)
    p = tmp_path / "loadgov.json"
    p.write_text(json.dumps({
        "ewma": 9.9, "generation": 1, "fingerprint": "abc",
        "observed_at": (now - timedelta(minutes=30)).isoformat(),
        "valid_until": (now - timedelta(minutes=25)).isoformat(),
    }), encoding="utf-8")
    snap, meta = LG.read_snapshot(now, path=str(p))
    assert snap is None and meta["source"] == "expired"
    assert LG.window_tol_min(now, snapshot=snap)[0] == 5.0


def test_G5_ratchet_bez_certyfikatu_alarmu_nigdy_loose(tmp_path):
    """OD-04: samo wysokie EWMA NIE uprawnia do tolerancji 10."""
    now = datetime.now(timezone.utc)
    p = tmp_path / "loadgov.json"
    p.write_text(json.dumps({
        "ewma": 99.0, "generation": 7, "fingerprint": "abc",
        "observed_at": now.isoformat(),
        "valid_until": (now + timedelta(minutes=5)).isoformat(),
    }), encoding="utf-8")
    snap, meta = LG.read_snapshot(now, path=str(p))
    assert snap is not None and meta["source"] == "snapshot"
    tol, src = LG.window_tol_min(now, snapshot=snap)
    assert tol == 5.0 and src == "strict_no_alarm_certificate"
    assert LG.alarm_certified(None) is False


def test_cap_alarmowy_40_tylko_z_certyfikatem():
    assert G.load_thresholds().carry_cap_min == 35.0
    assert G.load_thresholds(alarm_certificate={"id": "x"}).carry_cap_min == 40.0


# ─────────────────────── 9. G4 — jedna granica commitu ────────────────────────


def _stop(oid, kind, minute, dwell=3.5):
    t = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return {"order_id": oid, "type": kind, "predicted_at": t.isoformat(),
            "dwell_min": dwell, "coords": {"lat": 53.1, "lng": 23.1}}


NOW_G4 = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)


def test_G4_walidator_lapie_niemonotoniczne_czasy():
    """Sygnatura zapisu „przestawiona kolejność + stare ETA" u samego wyjścia."""
    # Oba niesione, żeby o werdykcie decydowała monotoniczność, nie precedencja.
    orders = {"a": {"status": "picked_up"}, "b": {"status": "picked_up"}}
    stops = [_stop("a", "dropoff", 10), _stop("b", "dropoff", 4)]
    r = P._g4_final_validator(stops, stops, orders, NOW_G4)
    assert not r["ok"] and r["reason"] == "non_monotonic"


def test_G4_walidator_lapie_brak_czasu():
    stops = [_stop("a", "dropoff", 5)]
    stops[0]["predicted_at"] = None
    r = P._g4_final_validator(stops, stops, {"a": {}}, NOW_G4)
    assert not r["ok"] and r["reason"] == "coverage_gap"


def test_G4_walidator_lapie_dostawe_przed_odbiorem():
    stops = [_stop("a", "dropoff", 5), _stop("a", "pickup", 8, dwell=1.0)]
    r = P._g4_final_validator(stops, stops, {"a": {"status": "assigned"}}, NOW_G4)
    assert not r["ok"] and r["reason"] == "precedence"


def test_G4_walidator_przepuszcza_poprawny_plan():
    stops = [_stop("a", "pickup", 2, dwell=1.0), _stop("a", "dropoff", 9)]
    r = P._g4_final_validator(stops, stops, {"a": {"status": "assigned"}}, NOW_G4)
    assert r["ok"] and r["reason"] is None


def test_G4_koperta_swiezosci_blokuje_kumulacje():
    orders = {"c": {"status": "picked_up", "picked_up_at": "2026-07-27 17:20:00"}}
    # 18:00 UTC now; possession 15:20 UTC ⇒ dostawa o 16:40 daje carry ~83 min
    zly = [_stop("c", "dropoff", 40)]
    koperta = [_stop("c", "dropoff", 1)]
    r = P._g4_final_validator(zly, koperta, orders, NOW_G4)
    assert not r["ok"] and r["reason"] == "freshness_envelope"


def test_G4_koperta_juz_nad_capem_dopuszcza_niepogorszenie():
    orders = {"c": {"status": "picked_up", "picked_up_at": "2026-07-27 17:20:00"}}
    koperta = [_stop("c", "dropoff", 40)]
    lepszy = [_stop("c", "dropoff", 30)]
    assert P._g4_final_validator(lepszy, koperta, orders, NOW_G4)["ok"]


def test_G4_no_current_valid_plan_przy_innym_worku(caplog):
    plan = {"plan_version": 3, "bag_signature": "INNA",
            "stops": [_stop("z", "dropoff", 5)]}
    with caplog.at_level("WARNING", logger="dispatch.plan_recheck"):
        ok = P._g4_assert_current_valid_plan("492", plan, ["a"], {}, "retime_none")
    assert ok is False
    assert any("NO_CURRENT_VALID_PLAN" in r.getMessage() for r in caplog.records)


def test_G4_no_current_valid_plan_przy_braku_tokenu_generacji():
    plan = {"bag_signature": None, "stops": [_stop("a", "dropoff", 5)]}
    assert P._g4_assert_current_valid_plan("492", plan, ["a"], {}, "x") is False


def test_G4_ratchet_retime_none_nie_moze_wywolac_save_plan():
    """Przy `retime=None` gałąź kończy się `return False` PRZED `save_plan`.

    Statyczny ratchet na źródle: między `if new_stops is None:` a jego `return`
    nie może pojawić się zapis planu. Test jest tani, a broni przed dokładnie
    tą klasą regresji, którą WB2 usuwa.
    """
    import inspect
    src = inspect.getsource(P._retime_one_bag_plan)
    blok = src.split("if new_stops is None:")[1].split("return False")[0]
    # Same KOD, bez komentarzy — inaczej ratchet łapie własne uzasadnienie.
    kod = "\n".join(l.split("#")[0] for l in blok.splitlines())
    assert "save_plan" not in kod and "_save(" not in kod


def test_G4_ratchet_f6_stale_nie_moze_wrocic_na_sciezce_guardow():
    """`_f6_stale` zostaje wyłącznie jako gałąź legacy (guardy OFF)."""
    import inspect
    src = inspect.getsource(P._gen_one_bag_plan)
    assert "RETIME_FAIL_KEEP_PRE_REORDER" in src, \
        "zniknął powrót do kolejności sprzed reorderu"
    assert "_f6_stale = True" in src.split("_g4_on")[1].split("legacy fallback")[0] \
        or "legacy fallback" in src


# ───────────────────────── 10. Ledger v2 — pola guardów ───────────────────────


def test_werdykty_guardow_traja_do_ledgera_v2(monkeypatch, fx):
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", True)
    fx2 = _strict_improvement(fx)
    ctx = LWL.writer_context("test", "tick").for_courier("492", 4)
    _mock_osrm(monkeypatch, fx2)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", True)
    P._lex_committed_window_reorder(
        [dict(s) for s in fx2["stops"]], fx2["orders_state"],
        tuple(fx2["start_pos"]), datetime.fromisoformat(fx2["now"]),
        ledger_ctx=ctx)

    rows = [r for r in LWL.read_records(LWL.CANONICAL_PATH)
            if r.get("record_kind") == "decision"]
    assert rows, "brak rekordu decyzji w kanonie"
    rec = rows[-1]
    assert set(rec["guards"]) == {"G1", "G2", "G3", "G4", "G5"}, \
        "kształt sekcji guards ZAMROŻONY schematem v2"
    assert rec["guards"]["G1"]["verdict"] == G.EXEMPT
    assert rec["guards"]["G3"]["verdict"] == G.EXEMPT
    assert rec["guards"]["G3"]["threshold_effective"] == 1.0
    assert rec["guards"]["G5"]["threshold_effective"] == 5.0
    assert rec["guards"]["G5"]["reason"] == "strict_no_snapshot"
    assert rec["loadgov"]["source"] == "absent"
    assert rec["thresholds"]["window_tol_min"] == 5.0


def test_guardy_OFF_zostawiaja_pola_guards_puste(monkeypatch, fx):
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", True)
    ctx = LWL.writer_context("test", "tick").for_courier("492", 4)
    _mock_osrm(monkeypatch, fx)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", False)
    P._lex_committed_window_reorder(
        [dict(s) for s in fx["stops"]], fx["orders_state"],
        tuple(fx["start_pos"]), datetime.fromisoformat(fx["now"]),
        ledger_ctx=ctx)
    rec = [r for r in LWL.read_records(LWL.CANONICAL_PATH)
           if r.get("record_kind") == "decision"][-1]
    assert set(rec["guards"]) == {"G1", "G2", "G3", "G4", "G5"}
    assert all(v is None for v in rec["guards"].values()), \
        "OFF nie może udawać, że guardy coś orzekły"


def test_liczniki_odrzucen_guardow_w_ledgerze(monkeypatch, fx):
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", True)
    ctx = LWL.writer_context("test", "tick").for_courier("492", 4)
    _mock_osrm(monkeypatch, fx)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", True)
    P._lex_committed_window_reorder(
        [dict(s) for s in fx["stops"]], fx["orders_state"],
        tuple(fx["start_pos"]), datetime.fromisoformat(fx["now"]),
        ledger_ctx=ctx)
    rec = [r for r in LWL.read_records(LWL.CANONICAL_PATH)
           if r.get("record_kind") == "decision"][-1]
    rej = rec["candidates"]["rejected"]
    assert sum(rej.get(k, 0) for k in
               ("guard_g1_delay", "guard_g2_delta", "guard_g2_cap",
                "guard_g3_gain", "guard_window", "guard_unevaluable")) > 0, \
        "guardy odrzuciły kandydatów, ale ledger tego nie pokazuje"


def test_ratchet_zywy_flags_json_nie_steruje_wersja_ledgera(monkeypatch):
    """Ratchet hermetyczności — bliźniak testu z bramki WB1, na fixture WB2.

    Oba pliki mają WŁASNY autouse `_isolate`, więc niezależność musi być
    dowiedziona osobno dla każdego z nich; jeden ratchet nie broni drugiego.
    """
    zywa = C.load_flags().get("ENABLE_LEX_WINDOW_LEDGER_V2")

    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False, raising=False)
    assert LWL.ledger_v2_enabled() is False, (
        f"żywy flags.json ({zywa!r}) przeciekł do testu — pin nie trzyma OFF")

    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", True, raising=False)
    assert LWL.ledger_v2_enabled() is True, (
        f"żywy flags.json ({zywa!r}) przeciekł do testu — pin nie trzyma ON")

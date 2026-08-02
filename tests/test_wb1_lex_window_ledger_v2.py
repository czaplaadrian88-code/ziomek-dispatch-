"""WB1 faza 1 (CZASY 492): bramka kanonicznego ledgera okna odbioru, schemat v2.

Broniony defekt (repro na żywym pliku 2026-07-27): v1 pisał `applied` = wartość FLAGI
`ENABLE_LEX_COMMITTED_WINDOW`, a nie fakt zapisu planu. Trzy timery obserwatorów
(carried-first-guard, b-route-shadow, bundle-calib-shadow) wołają tę samą warstwę
kanonu co silnik i dawały `applied: true` w 612/612 wierszy na dobę, z czego 384
(62,7 %) to powtórki. Na takim korpusie nie da się kalibrować progów WB2.

Bramka (spec `docs/WB1_LEDGER_V2_SCHEMA.md`):
  1. negatywny oracle — obserwator NIGDY nie tworzy wpisu kanonicznego,
  2. semantyka decided / written / served rozłączna (koniec `applied`),
  3. błąd appendu NIE wywraca decyzji silnika,
  4. kompatybilność: kill-switch OFF ⇒ v1 bajt-w-bajt, zero plików v2,
  5. mutation — usunięcie rozróżnienia ról zaczerwienia oracle,
  6. ratchet — `applied` nie może wrócić do rekordu v2.
"""
import json
import math
from datetime import datetime, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import osrm_client
from dispatch_v2 import plan_recheck as P
from dispatch_v2.core import lex_window_ledger as LWL
from dispatch_v2.core import lex_window_guards as LWG


# ── scenariusz: dokładnie ten sam bag co bramka P-1 (rozjazd gwarantowany) ──

def _hav_m(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _fake_table(pts_a, pts_b):
    return [[{"duration_s": _hav_m(a, b) / 8.333} for b in pts_b] for a in pts_a]


NOW = datetime(2026, 6, 24, 13, 0, 0, tzinfo=timezone.utc)
START = (53.130, 23.150)

ORDERS = {
    "A": {"status": "picked_up", "picked_up_at": "2026-06-24 14:55:00",
          "czas_kuriera_warsaw": None, "effective_pickup_source": "panel_ts",
          "pickup_coords": [53.130, 23.150], "delivery_coords": [53.100, 23.100]},
    "B": {"status": "assigned",
          "czas_kuriera_warsaw": "2026-06-24T15:02:00+02:00",
          "pickup_coords": [53.1305, 23.1505], "delivery_coords": [53.131, 23.149]},
}


def _stops():
    return [
        {"order_id": "A", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "B", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "B", "type": "dropoff", "dwell_min": 3.5},
    ]


def _ids(seq):
    return [(s["order_id"], s["type"]) for s in seq]


def _kind(path, kind):
    """Rekordy danego rodzaju (strumień zawiera też `heartbeat` raz na przebieg)."""
    return [r for r in LWL.read_records(path) if r.get("record_kind") == kind]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Pełna izolacja: OSRM zamockowany, WSZYSTKIE trzy pliki ledgera w tmp.

    Kill-switch ledgera przypięty do STAŁEJ MODUŁU — bo `ENABLE_LEX_WINDOW_LEDGER_V2`
    świadomie NIE należy do `ETAP4_DECISION_FLAGS` (nie zmienia treści decyzji,
    spec WB1 §6.1), a `_isolate_flags_json` w `tests/conftest.py` wycina z tmp-kopii
    tylko `ETAP4_DECISION_FLAGS`, `FLAGS_JSON_NUMERIC_OVERRIDES` i
    `TEST_ISOLATED_INFRA_FLAGS`. Wartość z ŻYWEGO `flags.json` przechodziła więc
    przez sito i wygrywała w `decision_flag()` nad stałą, którą ustawia test —
    po flipie ownera na `True` bramka kompatybilności („OFF ⇒ v1 bajt-w-bajt")
    testowała stan, którego nie da się w tym procesie osiągnąć.

    Pin idzie na `ledger_v2_enabled()`, bo to JEDYNY czytnik tej flagi w module
    (`record_decision`, `record_write_receipt`, `record_served_receipt` wołają
    wyłącznie ją). Dzięki temu idiom „patch stałej modułu steruje zachowaniem"
    działa znów w całym pliku, a wynik NIE zależy od tego, co owner ma dziś
    w `flags.json` — testy są zielone przy żywej fladze `True` i `False`.
    """
    monkeypatch.setattr(osrm_client, "table", _fake_table)
    monkeypatch.setattr(LWL, "CANONICAL_PATH", str(tmp_path / "canon.jsonl"))
    monkeypatch.setattr(LWL, "OBSERVATION_PATH", str(tmp_path / "obs.jsonl"))
    monkeypatch.setattr(LWL, "LEGACY_V1_PATH", str(tmp_path / "v1.jsonl"))
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", True)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", False)
    monkeypatch.setattr(P, "LEX_WINDOW_TOL_MIN", 5.0)
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False, raising=False)
    monkeypatch.setattr(
        LWL, "ledger_v2_enabled",
        lambda: bool(getattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False)))
    LWL.reset_state()
    yield
    LWL.reset_state()


@pytest.fixture
def v2_on(monkeypatch):
    """Kill-switch ON — przez stałą modułu, którą `_isolate` uczynił kanonem."""
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", True, raising=False)
    assert LWL.ledger_v2_enabled(), "kill-switch musi być ON w tym teście"


def _reorder(ctx):
    return P._lex_committed_window_reorder(_stops(), ORDERS, START, NOW, ledger_ctx=ctx)


# ── 1. NEGATYWNY ORACLE: obserwator nigdy nie zanieczyszcza kanonu ────────────

@pytest.mark.parametrize("ctx_factory", [
    pytest.param(lambda: None, id="brak_kontekstu"),
    pytest.param(lambda: LWL.observer_context("tools.b_route_shadow"), id="obserwator_jawny"),
])
def test_obserwator_nigdy_nie_tworzy_wpisu_kanonicznego(v2_on, ctx_factory):
    """ORACLE defektu: 3 timery obserwatorów pisały do kanonu jako `applied: true`."""
    out = _reorder(ctx_factory())

    canon = LWL.read_records(LWL.CANONICAL_PATH)
    obs = _kind(LWL.OBSERVATION_PATH, "decision")

    assert canon == [], "obserwator NIE MOŻE dopisać ani jednego wiersza do kanonu"
    assert len(obs) == 1, "wpis obserwatora ma przetrwać w pliku obserwacyjnym"
    assert obs[0]["caller"]["role"] == "observer"
    assert obs[0]["caller"]["can_persist_plan"] is False
    # obserwator nie decyduje planu — nawet gdy flaga APPLY jest ON
    assert obs[0]["decision"]["decided"] is False
    # ...i nie zmienia decyzji zwróconej wywołującemu (read-only pozostaje read-only)
    assert _ids(out)[0] == ("B", "pickup")


def test_writer_pisze_do_kanonu_i_tylko_tam(v2_on):
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492", 7)
    _reorder(ctx)

    canon = _kind(LWL.CANONICAL_PATH, "decision")
    assert len(canon) == 1
    assert LWL.read_records(LWL.OBSERVATION_PATH) == [], "writer nie dubluje do obserwacji"

    r = canon[0]
    assert r["schema"] == "lex_window_ledger.v2" and r["schema_version"] == 2
    assert r["caller"]["role"] == "writer" and r["caller"]["can_persist_plan"] is True
    assert r["courier_id"] == "492"
    assert r["route"]["generation"] == 7
    assert r["decision"]["decided"] is True and r["decision"]["identity"] is False


def test_kanon_zawiera_pola_wymagane_przez_kalibracje_wb2(v2_on):
    """13.2 p.1 — bez tych pól progi WB2 nie mają z czego powstać."""
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492", 3)
    _reorder(ctx)
    r = _kind(LWL.CANONICAL_PATH, "decision")[0]

    for key in ("decision_id", "attempt_id", "run_id", "bag", "route", "candidates",
                "baseline", "chosen", "items", "raw", "guards", "loadgov", "write",
                "served", "validator", "flags", "thresholds", "coverage"):
        assert key in r, f"brak sekcji {key}"

    assert r["bag"]["active_order_ids"] == ["A", "B"]
    assert r["bag"]["active_order_signature"]
    assert r["candidates"]["pool_size"] >= r["candidates"]["feasible"] >= 1
    assert set(r["candidates"]["rejected"]) == {
        "precedence", "no_return", "metrics", "carry_cap", "breaches",
        "delay_tol", "r6_per_order"}
    # progi EFEKTYWNE, nie defaulty z dokumentacji
    assert r["thresholds"]["window_tol_min"] == 5.0
    # pola guardów G1-G5 istnieją i czekają na WB2
    assert set(r["guards"]) == {"G1", "G2", "G3", "G4", "G5"}
    assert all(v is None for v in r["guards"].values())
    # per sztuka: arrival ≠ handoff (OD-01)
    by_oid = {(i["order_id"], i["kind"]): i for i in r["items"]}
    a_drop = by_oid[("A", "dropoff")]
    assert a_drop["handoff_min"] == pytest.approx(a_drop["arrival_min"] + a_drop["dwell_min"])
    assert a_drop["possession_source"] == "panel_ts"
    assert a_drop["raw_carry_min"] is not None
    assert by_oid[("B", "pickup")]["raw_W_min"] is not None
    assert r["flags"]["fingerprint_sha"] and r["flags"]["code_fingerprint"]


def test_kanon_guard_on_zawiera_jawne_liczniki_wb2(v2_on, monkeypatch):
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", True)
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492", 4)
    _reorder(ctx)
    r = _kind(LWL.CANONICAL_PATH, "decision")[0]

    legacy = {
        "precedence", "no_return", "metrics", "carry_cap", "breaches",
        "delay_tol", "r6_per_order",
    }
    guard_keys = set(LWG.empty_rejection_counters())
    expected = legacy | guard_keys
    assert set(r["candidates"]["rejected"]) == expected
    assert all(r["candidates"]["rejected"][key] == 0 for key in guard_keys)
    assert set(r["guards"]) == {"G1", "G2", "G3", "G4", "G5"}
    assert r["guards"]["G4"] is None


# ── 2. SEMANTYKA decided / written / served — trzy ROZŁĄCZNE fakty ────────────

def test_decided_written_served_sa_rozlaczne(v2_on):
    """`decided` NIE implikuje `written`, `written` NIE implikuje `served`."""
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492", 1)
    _reorder(ctx)

    dec = _kind(LWL.CANONICAL_PATH, "decision")[0]
    assert dec["record_kind"] == "decision"
    assert dec["decision"]["decided"] is True
    # sam fakt decyzji NIE twierdzi nic o zapisie ani o podaniu
    assert dec["write"]["outcome"] == "not_attempted"
    assert dec["served"]["outcome"] is None

    # zapis odrzucony przez CAS → osobny rekord, `decided` pozostaje prawdą
    P._lex_write_receipt(ctx, "492", "regen", "skipped_cas", expected=1, current=9)
    wrs = _kind(LWL.CANONICAL_PATH, "write_receipt")
    assert len(wrs) == 1
    wr = wrs[0]
    assert wr["attempt_id"] == dec["attempt_id"], "receipt wiąże się z TĄ próbą"
    assert wr["write"]["outcome"] == "skipped_cas"
    assert (wr["write"]["cas_expected"], wr["write"]["cas_current"]) == (1, 9)

    # podanie = trzeci, niezależny fakt
    LWL.record_served_receipt(ctx, attempt_id=dec["attempt_id"], courier_id="492",
                             surface="api", outcome="served")
    sv = _kind(LWL.CANONICAL_PATH, "served_receipt")[0]
    assert sv["served"]["surface"] == "api" and sv["served"]["outcome"] == "served"


def test_receipt_zapisu_konsumuje_sie_raz(v2_on):
    """Uchwyt próby jest jednorazowy — brak fantomowych `written` bez decyzji."""
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492", 1)
    _reorder(ctx)
    P._lex_write_receipt(ctx, "492", "regen", "written")
    P._lex_write_receipt(ctx, "492", "regen", "written")   # drugi raz = no-op
    kinds = [r["record_kind"] for r in LWL.read_records(LWL.CANONICAL_PATH)]
    assert kinds == ["heartbeat", "decision", "write_receipt"]


def test_identity_nie_jest_decyzja_ale_liczy_sie_do_pokrycia(v2_on):
    """Bag bez rozjazdu daje MIANOWNIK kalibracji, z decided=False."""
    orders = {k: dict(v) for k, v in ORDERS.items()}
    orders["B"]["czas_kuriera_warsaw"] = "2026-06-24T18:00:00+02:00"  # okno odległe
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492", 1)
    out = P._lex_committed_window_reorder(_stops(), orders, START, NOW, ledger_ctx=ctx)

    assert _ids(out) == _ids(_stops()), "identity nie rusza kolejności"
    r = _kind(LWL.CANONICAL_PATH, "decision")[0]
    assert r["decision"]["identity"] is True and r["decision"]["decided"] is False


# ── 3. BŁĄD ZAPISU nie może wywrócić decyzji ─────────────────────────────────

def test_blad_appendu_nie_wywraca_decyzji(v2_on, monkeypatch):
    def _boom(*a, **k):
        raise OSError("dysk pełny")

    monkeypatch.setattr(LWL, "append_jsonl", _boom)
    out = _reorder(LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492"))
    # decyzja silnika przeżywa: odbiór B nadal przed daleką dostawą niesionego A
    o = _ids(out)
    assert o.index(("B", "pickup")) < o.index(("A", "dropoff"))


def test_blad_appendu_jest_policzony_jako_degradacja(v2_on, monkeypatch):
    def _boom(*a, **k):
        raise OSError("dysk pełny")

    monkeypatch.setattr(LWL, "append_jsonl", _boom)
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    _reorder(ctx)
    # heartbeat + decyzja — KAŻDA nieudana emisja ma podnieść licznik, żeby cisza
    # w pliku nigdy nie była nieodróżnialna od braku rozjazdów.
    assert LWL.stats(ctx.run_id)["errors"] >= 1, "utrata wiersza musi być WIDOCZNA"
    assert LWL.read_records(LWL.CANONICAL_PATH) == []


def test_backpressure_odrzuca_po_przekroczeniu_budzetu(v2_on, monkeypatch):
    monkeypatch.setattr(LWL, "MAX_RECORDS_PER_RUN", 2)
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    for _ in range(5):
        _reorder(ctx)
    assert len(_kind(LWL.CANONICAL_PATH, "decision")) == 2
    st = LWL.stats(ctx.run_id)
    assert st["dropped"] == 3 and st["seq"] == 2


def test_rotacja_nie_gubi_danych(v2_on, monkeypatch, tmp_path):
    monkeypatch.setattr(LWL, "MAX_BYTES", 1)         # wymuś obrót przy drugim wpisie
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    _reorder(ctx)
    _reorder(ctx)
    rotated = list(tmp_path.glob("canon.jsonl.*"))
    rotated = [p for p in rotated if not p.name.endswith(".append.lock")]
    assert rotated, "plik po przekroczeniu limitu musi zostać PRZENIESIONY, nie skasowany"
    def _count(path):
        return len([r for r in LWL.read_records(path)
                    if r.get("record_kind") == "decision"])
    total = _count(LWL.CANONICAL_PATH) + sum(_count(str(p)) for p in rotated)
    assert total == 2, "rotacja nie może zgubić żadnego wiersza"


# ── 4. KOMPATYBILNOŚĆ: kill-switch OFF = v1 bajt-w-bajt ──────────────────────

def test_killswitch_off_pisze_v1_bajt_w_bajt_i_zero_v2(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False, raising=False)
    assert not LWL.ledger_v2_enabled()

    _reorder(LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492"))

    assert LWL.read_records(LWL.CANONICAL_PATH) == [], "OFF nie tworzy kanonu v2"
    assert LWL.read_records(LWL.OBSERVATION_PATH) == [], "OFF nie tworzy obserwacji v2"

    v1 = LWL.read_records(LWL.LEGACY_V1_PATH)
    assert len(v1) == 1
    # kontrakt v1 dokładnie jak przed WB1 — komplet kluczy w tej samej kolejności
    assert list(v1[0]) == ["ts", "carried", "base_window_viol", "lex_window_viol",
                           "d_drive_min", "base_max_carry", "lex_max_carry",
                           "applied", "base_seq", "lex_seq"]
    assert v1[0]["applied"] is True          # OFF = stara (kłamliwa) semantyka NIETKNIĘTA
    assert v1[0]["carried"] == ["A"]


def test_killswitch_off_nie_loguje_identity(monkeypatch):
    """Parytet z zachowaniem sprzed WB1: v1 powstawał TYLKO przy rozjeździe."""
    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False, raising=False)
    orders = {k: dict(v) for k, v in ORDERS.items()}
    orders["B"]["czas_kuriera_warsaw"] = "2026-06-24T18:00:00+02:00"
    P._lex_committed_window_reorder(_stops(), orders, START, NOW, ledger_ctx=None)
    assert LWL.read_records(LWL.LEGACY_V1_PATH) == []


def test_killswitch_on_zamraza_v1(v2_on):
    _reorder(LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492"))
    assert LWL.read_records(LWL.LEGACY_V1_PATH) == [], \
        "po flipie v1 jest ZAMROŻONY — żadnych dwóch writerów tej samej prawdy"


# ── 5. MUTATION: usunięcie rozróżnienia ról musi zaczerwienić oracle ─────────

def test_mutation_usuniecie_rozroznienia_rol_czerwieni(v2_on, monkeypatch):
    """Gdyby `_target_path` przestał patrzeć na rolę (regres do v1), oracle #1 pada."""
    monkeypatch.setattr(LWL, "_target_path", lambda ctx: LWL.CANONICAL_PATH)

    _reorder(LWL.observer_context("tools.b_route_shadow"))

    canon = LWL.read_records(LWL.CANONICAL_PATH)
    assert canon, "mutacja z definicji przywraca zanieczyszczenie kanonu"
    assert canon[0]["caller"]["role"] == "observer", (
        "DOWÓD mutacji: bez rozróżnienia ról wiersz obserwatora ląduje w kanonie — "
        "dokładnie defekt v1, który ten test ma blokować")


def test_mutation_writer_context_zdegradowany_do_obserwatora_czerwieni(v2_on, monkeypatch):
    """Druga mutacja: fabryka writera przestaje nadawać rolę WRITER."""
    monkeypatch.setattr(LWL, "writer_context",
                        lambda source, trigger: LWL.observer_context(source, trigger))
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    _reorder(ctx)
    assert _kind(LWL.CANONICAL_PATH, "decision") == [], (
        "DOWÓD mutacji: bez roli WRITER kanon zostaje PUSTY — brak danych do kalibracji WB2")


# ── 7. HEARTBEAT: dowód pokrycia + rozwinięcie skrótu odcisku flag ───────────

def test_heartbeat_raz_na_przebieg_z_pelnym_odciskiem(v2_on):
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    _reorder(ctx)
    _reorder(ctx)

    hb = _kind(LWL.CANONICAL_PATH, "heartbeat")
    assert len(hb) == 1, "heartbeat dokładnie RAZ na przebieg, nie co wiersz"
    assert hb[0]["flags"]["fingerprint"], "pełny odcisk flag musi być odtwarzalny"

    dec = _kind(LWL.CANONICAL_PATH, "decision")
    assert len(dec) == 2
    assert all(d["flags"]["fingerprint_sha"] == hb[0]["flags"]["fingerprint_sha"]
               for d in dec), "skrót w decyzjach musi joinować się z heartbeatem"


def test_rekord_nie_puchnie_od_odcisku_flag(v2_on):
    """Ratchet kosztu: pełny odcisk (~6 kB) nie może wrócić do rekordu decyzji."""
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    _reorder(ctx)
    rec = _kind(LWL.CANONICAL_PATH, "decision")[0]
    assert "fingerprint" not in rec["flags"], "w decyzji tylko skrót"
    assert len(json.dumps(rec)) < 4096, (
        f"rekord decyzji urósł do {len(json.dumps(rec))} B — rotacja i koszt I/O "
        "były liczone dla rekordu ~2-3 kB")


# ── 6. RATCHET: `applied` nie może wrócić do v2 ──────────────────────────────

def test_ratchet_applied_nie_moze_wrocic_do_v2(v2_on):
    ctx = LWL.writer_context("plan_recheck.run_recheck", "tick").for_courier("492")
    _reorder(ctx)
    P._lex_write_receipt(ctx, "492", "regen", "written")
    for r in LWL.read_records(LWL.CANONICAL_PATH):
        assert "applied" not in json.dumps(r), (
            "`applied` był wartością FLAGI, nie faktem — w v2 zastępują go "
            "decided/written/served i nie wolno go przywrócić")


def test_ratchet_jeden_punkt_odwzorowania_rola_plik():
    """Routing rola→plik istnieje DOKŁADNIE raz (inaczej wraca dryf writerów)."""
    import inspect
    src = inspect.getsource(LWL)
    assert src.count("def _target_path(") == 1
    # nikt poza `_target_path` nie może wybierać pliku kanonicznego
    body = inspect.getsource(LWL._target_path)
    assert "CANONICAL_PATH" in body
    assert src.count("CANONICAL_PATH") <= 3, (
        "CANONICAL_PATH poza definicją i `_target_path` = drugi punkt decyzji o pliku")


def test_plan_recheck_nie_ma_juz_wlasnej_sciezki_ledgera():
    """Ratchet na wygaszony duplikat nazwy (dwie nazwy jednej prawdy)."""
    assert not hasattr(P, "LEX_WINDOW_SHADOW_PATH"), (
        "ścieżka ledgera ma JEDNEGO właściciela: core.lex_window_ledger")


def test_ratchet_zywy_flags_json_nie_steruje_wersja_ledgera(monkeypatch):
    """Ratchet hermetyczności: o wersji ledgera decyduje TEST, nie owner.

    `ENABLE_LEX_WINDOW_LEDGER_V2` świadomie nie jest flagą decyzyjną (spec WB1
    §6.1), więc `_isolate_flags_json` NIE wycina jej z tmp-kopii `flags.json`
    i żywa wartość wygrywała w `decision_flag()` nad stałą ustawianą przez test.
    Skutkiem był fałszywie czerwony test kompatybilności po flipie ownera na
    `True` — bramka „OFF ⇒ v1 bajt-w-bajt" sprawdzała stan nieosiągalny.

    Ten test dowodzi ROZŁĄCZNOŚCI obu źródeł w jednym procesie: cokolwiek stoi
    w żywym pliku, oba kierunki pinu muszą się trzymać.
    """
    zywa = C.load_flags().get("ENABLE_LEX_WINDOW_LEDGER_V2")

    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", False, raising=False)
    assert LWL.ledger_v2_enabled() is False, (
        f"żywy flags.json ({zywa!r}) przeciekł do testu — pin nie trzyma OFF")

    monkeypatch.setattr(C, "ENABLE_LEX_WINDOW_LEDGER_V2", True, raising=False)
    assert LWL.ledger_v2_enabled() is True, (
        f"żywy flags.json ({zywa!r}) przeciekł do testu — pin nie trzyma ON")

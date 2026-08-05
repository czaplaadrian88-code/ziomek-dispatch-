"""Bramka testowa czytelnika SUWAKA AUTONOMII (tools/suwak_autonomii_review.py).

Suwak = DWIE liczby, na które owner czeka od 19.07, liczone odtąd CODZIENNIE pod
`shadow-review.timer`. Testy pilnują dokładnie tego, co czyni te liczby uczciwymi:

  * poprawność obu liczb na korpusie policzonym RĘCZNIE (nie na "co wyszło");
  * segmentacja puli (pool<=2 vs pool>=3) — bez niej Liczba 2 nie znaczy nic;
  * fail-soft: wyjątek/brak/martwy korpus NIE wywala dobowego przeglądu cienia,
    ale też NIE produkuje liczby z martwego strumienia (null + powód);
  * append-only szeregu i zero zapisu poza własnym wyjściem — to jest cały kontrakt
    read-only tego czytelnika wobec żywego stanu.

Hermetyczne: wyłącznie tmp_path, zero systemd, zero żywych ścieżek, zero sieci.
"""
import gzip
import importlib.util
import json
import os
import time

import pytest

TOOLS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
TOOL = os.path.join(TOOLS, "suwak_autonomii_review.py")
HARNESS = os.path.join(TOOLS, "shadow_review_daily.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load(TOOL, "suwak_autonomii_review")
H = _load(HARNESS, "shadow_review_daily_for_suwak")


# ───────────────────────── korpusy syntetyczne ─────────────────────────

def _dec(ts, eid, oid, **kw):
    r = {"ts": ts, "event_id": eid, "order_id": oid}
    r.update(kw)
    return r


# 7 wierszy + 2 zepsute linie. Ręczny rachunek w docstringu testu niżej.
DECISIONS_ROWS = [
    _dec("2026-08-01T10:00:00Z", "e1", "100", would_auto_assign=False, would_auto_assign_d=True,
         would_auto_assign_dprime=True, auto_route="AUTO", pool_feasible_count=5),
    _dec("2026-08-01T11:00:00Z", "e2", "101", would_auto_assign=False, would_auto_assign_d=False,
         would_auto_assign_dprime=False, auto_route="ACK", pool_feasible_count=2,
         auto_block_reasons_d=["scarcity_pool:pool=2", "pos_not_informed:1"]),
    _dec("2026-08-02T10:00:00Z", "e3", "102", would_auto_assign=True, would_auto_assign_d=True,
         would_auto_assign_dprime=False, auto_route="ALERT", pool_feasible_count=3),
    # duplikat e2 (rotacja) — musi wypaść
    _dec("2026-08-01T11:00:00Z", "e2", "101", would_auto_assign=False, would_auto_assign_d=False,
         would_auto_assign_dprime=False, auto_route="ACK", pool_feasible_count=2),
    # lifecycle — nie jest decyzją dyspozytorską
    _dec("2026-08-02T11:00:00Z", "e5", "104", record_type="CZASOWKA_RECLAIM_EVALUATION",
         would_auto_assign_d=True, auto_route="AUTO", pool_feasible_count=9),
    # bez pól bramki auto — poza mianownikiem
    _dec("2026-08-02T12:00:00Z", "e6", "103"),
    # DRUGA decyzja tego samego zamówienia (przekierowanie) — do joinu bierzemy pierwszą
    _dec("2026-08-02T13:00:00Z", "e7", "100", would_auto_assign=False, would_auto_assign_d=False,
         would_auto_assign_dprime=False, auto_route="ACK", pool_feasible_count=4),
]

OUTCOMES_ROWS = [
    {"oid": "100", "day": "2026-08-01", "agree": True, "pool_feasible": 5},
    {"oid": "200", "day": "2026-08-01", "agree": True, "pool_feasible": 4},
    {"oid": "201", "day": "2026-08-02", "agree": False, "pool_feasible": 3},
    {"oid": "202", "day": "2026-08-01", "agree": False, "pool_feasible": 1},
    {"oid": "203", "day": "2026-08-01", "agree": False, "pool_feasible": 1},
    {"oid": "101", "day": "2026-08-02", "agree": False, "pool_feasible": 2},
    {"oid": "204", "day": "2026-08-02", "agree": True, "pool_feasible": 2},
    {"oid": "205", "day": "2026-08-02", "agree": True, "pool_feasible": None},
    {"oid": "206", "day": "2026-08-02", "agree": False},
    {"oid": "207", "day": "2026-08-02", "agree": None, "pool_feasible": 7},
    # poza oknem decyzji (do testu okna wspólnego)
    {"oid": "208", "day": "2026-07-01", "agree": True, "pool_feasible": 6},
]


def _write_jsonl(path, rows, extra_lines=()):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        for line in extra_lines:
            fh.write(line + "\n")
    return str(path)


@pytest.fixture()
def decisions(tmp_path):
    return _write_jsonl(tmp_path / "shadow_decisions.jsonl", DECISIONS_ROWS,
                        extra_lines=("{to nie json", "[1, 2, 3]"))


@pytest.fixture()
def outcomes(tmp_path):
    return _write_jsonl(tmp_path / "outcomes_clean_shadow.jsonl", OUTCOMES_ROWS)


# ───────────────────────── LICZBA 1 ─────────────────────────

def test_liczba1_policzona_recznie(decisions):
    """Rachunek ręczny mianownika i czterech miar.

    9 linii → 2 nieparsowalne → 7 rekordów → -1 duplikat `e2` → -1 lifecycle
    (CZASOWKA_RECLAIM_EVALUATION) → -1 bez pól bramki → mianownik = 4 (e1, e2, e3, e7).
    bazowy true = {e3} = 1/4 = 25 % · D true = {e1, e3} = 2/4 = 50 %
    D' true = {e1} = 1/4 = 25 % · auto_route=AUTO = {e1} = 1/4 = 25 %
    """
    l1, manifest, idx = M.compute_liczba1(decisions, 48.0)
    assert l1["available"] is True
    it = l1["intake"]
    assert (it["records_read"], it["duplicates_dropped"], it["excluded_lifecycle"],
            it["excluded_missing_gate_fields"], it["decisions"], it["bad_lines"]) == (7, 1, 1, 1, 4, 2)

    g = l1["global"]
    assert (g["would_auto_assign"]["true"], g["would_auto_assign"]["n"]) == (1, 4)
    assert g["would_auto_assign"]["pct"] == pytest.approx(25.0)
    assert (g["would_auto_assign_d"]["true"], g["would_auto_assign_d"]["n"]) == (2, 4)
    assert g["would_auto_assign_d"]["pct"] == pytest.approx(50.0)
    assert g["would_auto_assign_dprime"]["pct"] == pytest.approx(25.0)
    assert (g["auto_route_AUTO"]["true"], g["auto_route_AUTO"]["n"]) == (1, 4)
    assert g["auto_route_dist"] == {"ACK": 2, "AUTO": 1, "ALERT": 1}
    assert l1["window"] == {"day_first": "2026-08-01", "day_last": "2026-08-02", "days_distinct": 2}
    assert manifest[0]["records"] == 7 and manifest[0]["sha256"]


def test_liczba1_segmentacja_puli(decisions):
    """pool>=3 = {e1(5), e3(3), e7(4)}; pool<=2 = {e2(2)}. Granica 3 jest częścią definicji."""
    l1, _, _ = M.compute_liczba1(decisions, 48.0)
    assert l1["pool_ge3"]["n"] == 3 and l1["pool_le2"]["n"] == 1 and l1["pool_unknown"]["n"] == 0
    assert l1["pool_ge3"]["would_auto_assign_d"]["pct"] == pytest.approx(200.0 / 3)
    assert l1["pool_le2"]["would_auto_assign_d"]["pct"] == pytest.approx(0.0)


def test_pool_bucket_definicja():
    assert M.pool_bucket(3) == ">=3" and M.pool_bucket(2) == "<=2"
    assert M.pool_bucket(None) == "unknown" and M.pool_bucket("3") == "unknown"
    # bool jest podtypem int — pool_feasible=True NIE jest liczbą kurierów
    assert M.pool_bucket(True) == "unknown"


def test_liczba1_rodziny_blokerow(decisions):
    l1, _, _ = M.compute_liczba1(decisions, 48.0)
    bl = l1["blockers_d"]
    assert bl["n_blocked"] == 2 and bl["n_with_reasons"] == 1
    assert bl["families_any"] == {"scarcity_pool": 1, "pos_not_informed": 1}
    assert bl["families_first"] == {"scarcity_pool": 1}


def test_liczba1_indeks_bierze_pierwsza_decyzje(decisions):
    """Zamówienie 100 ma dwie decyzje (e1 auto-gotowa, e7 nie) — kanonem jest PIERWSZA."""
    l1, _, idx = M.compute_liczba1(decisions, 48.0)
    assert idx["100"]["would_auto_assign_d"] is True
    assert l1["orders"] == {"distinct": 3, "with_multiple_decisions": 1, "max_decisions_per_order": 2}


# ───────────────────────── rotacje ─────────────────────────

# Nazwy, które REALNIE potrafią leżeć obok korpusu: rotowany lock pisarza
# (`core/jsonl_rotation.py` tworzy `<baza>.append.lock`), kopie operatorskie, pliki tymczasowe.
# Żaden z nich nie jest korpusem decyzji, także wtedy gdy kończy się kropką i liczbą.
ROTATION_JUNK_SUFFIXES = (
    ".append.lock", ".append.lock.1", ".bak", ".bak.1", ".old.7", ".save.12.gz",
    ".tmp", ".tmp.3", ".1.zst", ".1.bak", "-20260804", ".gz",
)


def test_rotacje_wykrywane_globem_bez_smieci(tmp_path):
    """RATCHET WHITELIST: korpusem jest wyłącznie `<baza>.<N>` i `<baza>.<N>.gz`.

    Filtr blacklistowy (`\\.(\\d+)(\\.gz)?$` przez `.search()` na całej ścieżce) wpuszczał
    całą klasę `<baza>.<cokolwiek>.<N>` — jedna kopia operatorska albo jeden rotowany lock
    cicho przesuwał OBIE liczby ownera, bo plik wpisywał się w manifest jak pełna rotacja.
    """
    base = tmp_path / "shadow_decisions.jsonl"
    base.write_text("{}\n")
    for suffix in (".1", ".2.gz", ".10"):
        (tmp_path / ("shadow_decisions.jsonl" + suffix)).write_text("x")
    for suffix in ROTATION_JUNK_SUFFIXES:
        (tmp_path / ("shadow_decisions.jsonl" + suffix)).write_text("x")
    got = M.discover_decision_files(str(base))
    assert [os.path.basename(p) for p in got] == [
        "shadow_decisions.jsonl", "shadow_decisions.jsonl.1",
        "shadow_decisions.jsonl.2.gz", "shadow_decisions.jsonl.10"]
    for suffix in ROTATION_JUNK_SUFFIXES:
        assert M.rotation_index(str(base), str(base) + suffix) is None, suffix
    assert M.rotation_index(str(base), str(base) + ".7") == 7
    assert M.rotation_index(str(base), str(base) + ".7.gz") == 7


def test_smieciowy_plik_konczacy_sie_liczba_nie_wchodzi_do_mianownika(tmp_path):
    """NEGATYWNY ORACLE (parytet z composerem): śmieć NIE MOŻE ruszyć żadnej z dwóch liczb."""
    czysty = _write_jsonl(tmp_path / "shadow_decisions.jsonl", DECISIONS_ROWS)
    wzorzec, _, wzorzec_idx = M.compute_liczba1(czysty, 48.0)
    for suffix, oid in ((".append.lock.1", "oLOCK"), (".bak.1", "oBAK"), (".old.7", "oOLD")):
        _write_jsonl(tmp_path / ("shadow_decisions.jsonl" + suffix), [
            _dec("2026-08-02T23:59:00Z", "smiec" + oid, oid, would_auto_assign=True,
                 would_auto_assign_d=True, would_auto_assign_dprime=True,
                 auto_route="AUTO", pool_feasible_count=8)])
    l1, manifest, idx = M.compute_liczba1(czysty, 48.0)
    assert l1["intake"] == wzorzec["intake"] and l1["global"] == wzorzec["global"]
    assert idx == wzorzec_idx and "oLOCK" not in idx and "oBAK" not in idx
    assert [os.path.basename(m["path"]) for m in manifest] == ["shadow_decisions.jsonl"]


def test_rotacja_gz_wchodzi_do_mianownika(tmp_path):
    base = _write_jsonl(tmp_path / "shadow_decisions.jsonl", DECISIONS_ROWS[:1])
    gz = str(tmp_path / "shadow_decisions.jsonl.1.gz")
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(DECISIONS_ROWS[1], ensure_ascii=False) + "\n")
    l1, _, _ = M.compute_liczba1(base, 48.0)
    assert l1["intake"]["decisions"] == 2
    assert l1["global"]["would_auto_assign_d"]["pct"] == pytest.approx(50.0)


def test_brak_zywej_bazy_przy_swiezej_rotacji_nie_daje_liczby(tmp_path):
    """NEGATYWNY ORACLE fail-closed: świeża rotacja BEZ pliku bazowego = strumień stanął.

    Plik żywy rozpoznajemy po ŚCIEŻCE BAZOWEJ, nie po pozycji na liście: bez tego najnowsza
    rotacja awansuje na "bazę", przechodzi kontrolę świeżości i produkuje liczbę dnia
    z korpusu, do którego nikt już nie pisze (dokładnie to, czemu ma zapobiegać próg 48 h).
    """
    base = str(tmp_path / "shadow_decisions.jsonl")
    _write_jsonl(tmp_path / "shadow_decisions.jsonl.1", DECISIONS_ROWS)  # mtime = teraz
    assert [os.path.basename(p) for p in M.discover_decision_files(base)] == [
        "shadow_decisions.jsonl.1"]

    l1, manifest, idx = M.compute_liczba1(base, 48.0)
    assert l1["available"] is False
    assert "korpusu bazowego" in l1["reason"] and idx == {}

    # KONTROLA POZYTYWNA: ta sama rotacja + OBECNA świeża baza → liczba powstaje.
    _write_jsonl(tmp_path / "shadow_decisions.jsonl", DECISIONS_ROWS)
    l1b, _, idxb = M.compute_liczba1(base, 48.0)
    assert l1b["available"] is True and l1b["intake"]["decisions"] == 4 and idxb


def test_martwa_baza_nie_ozywa_przez_swieza_rotacje(tmp_path):
    """Baza sprzed 40 dni + świeża rotacja: liczba NADAL nie powstaje (powód: MARTWY)."""
    base = _write_jsonl(tmp_path / "shadow_decisions.jsonl", DECISIONS_ROWS)
    _write_jsonl(tmp_path / "shadow_decisions.jsonl.1", DECISIONS_ROWS[:1])
    old = time.time() - 40 * 86400
    os.utime(base, (old, old))
    l1, _, idx = M.compute_liczba1(base, 48.0)
    assert l1["available"] is False and "MARTWY" in l1["reason"] and idx == {}


def test_indeks_bierze_najwczesniejsza_decyzje_takze_miedzy_rotacjami(tmp_path):
    """ŚWIADOMA różnica metodologii vs composer 04.08 (decyzja CTO 05.08): PIERWSZA = w CZASIE.

    Pliki wczytywane są od najnowszego, więc "pierwszy napotkany rekord" dawał dla zamówienia
    rozpiętego na granicy rotacji decyzję PÓŹNIEJSZĄ (przekierowanie) i przesuwał macierz
    joinu "auto-gotowe × zgodność" — przy zdaniu w raporcie, że brana jest pierwsza.
    """
    base = _write_jsonl(tmp_path / "shadow_decisions.jsonl", [
        _dec("2026-08-05T10:00:00Z", "nowa", "o1", would_auto_assign_d=True,
             auto_route="AUTO", pool_feasible_count=9),
        # rekord BEZ `ts` nie może wygrać z rekordem o znanym czasie
        {"event_id": "bez_ts", "order_id": "o2", "would_auto_assign_d": True,
         "auto_route": "AUTO", "pool_feasible_count": 9},
        _dec("2026-08-05T09:00:00Z", "o2_pierwsza", "o2", would_auto_assign_d=False,
             auto_route="MANUAL", pool_feasible_count=2),
    ])
    _write_jsonl(tmp_path / "shadow_decisions.jsonl.1", [
        _dec("2026-08-04T09:00:00Z", "stara", "o1", would_auto_assign_d=False,
             auto_route="MANUAL", pool_feasible_count=1)])

    l1, _, idx = M.compute_liczba1(base, 48.0)
    assert idx["o1"] == {"would_auto_assign_d": False, "auto_route": "MANUAL",
                         "pool_feasible_count": 1}
    assert idx["o2"] == {"would_auto_assign_d": False, "auto_route": "MANUAL",
                         "pool_feasible_count": 2}
    assert l1["orders"] == {"distinct": 2, "with_multiple_decisions": 2,
                            "max_decisions_per_order": 2}


def test_pokrycie_puli_nie_liczy_boola_jako_liczby_kurierow(tmp_path):
    """Jedna definicja pokrycia puli = ta sama, której używają kubełki (`pool_bucket`)."""
    base = _write_jsonl(tmp_path / "shadow_decisions.jsonl", DECISIONS_ROWS + [
        _dec("2026-08-02T14:00:00Z", "e8", "105", would_auto_assign_d=True,
             auto_route="AUTO", pool_feasible_count=True)])
    l1, _, _ = M.compute_liczba1(base, 48.0)
    assert l1["intake"]["decisions"] == 5 and l1["pool_unknown"]["n"] == 1
    assert l1["coverage"]["pool_feasible_count"] == 4
    assert l1["coverage"]["pool_feasible_count"] == l1["pool_ge3"]["n"] + l1["pool_le2"]["n"]


# ───────────────────────── LICZBA 2 ─────────────────────────

def test_liczba2_policzona_recznie(outcomes):
    """Ręczny rachunek zgodności i segmentów.

    agree znane = 10 z 11 (rekord `207` ma agree=None). Poza oknem decyzji zostaje `208`,
    ale PEŁNE okno liczy wszystko: agree = {100, 200, 204, 205, 208} = 5/10 = 50 %.
    pool>=3 = {100(5,T), 200(4,T), 201(3,F), 208(6,T)} → 3/4 = 75 %
    pool<=2 = {202(1,F), 203(1,F), 101(2,F), 204(2,T)} → 1/4 = 25 %, niezgoda 75 %
    pula nieznana = {205(None,T), 206(brak,F)} → 1/2 = 50 %
    """
    l2, meta, rows = M.compute_liczba2(outcomes, 48.0)
    assert l2["available"] is True and meta["records"] == 11 and len(rows) == 11
    full = l2["full"]
    assert full["global"]["n_agree_known"] == 10
    assert full["global"]["agree_pct"] == pytest.approx(50.0)
    assert (full["pool_ge3"]["n_agree_known"], full["pool_ge3"]["agree"]) == (4, 3)
    assert full["pool_ge3"]["agree_pct"] == pytest.approx(75.0)
    assert (full["pool_le2"]["n_agree_known"], full["pool_le2"]["agree"]) == (4, 1)
    assert full["pool_le2"]["agree_pct"] == pytest.approx(25.0)
    assert full["pool_unknown"]["n_agree_known"] == 2
    # pokrycie liczone na REKORDACH (nie na agree-znanych): pulę zna 9 z 11 wierszy —
    # rekord 207 ma pool_feasible=7 przy agree=None, więc wchodzi do pokrycia, a nie do zgodności.
    assert full["pool_coverage_pct"] == pytest.approx(100.0 * 9 / 11)


def test_liczba2_dekompozycja_nadwyzki_z_niedoboru(outcomes):
    """Nadwyżka = niezgody pool<=2 ponad poziom bazowy (stopa niezgody przy pool>=3).

    baseline = 25 % (1 z 4 niezgód przy pool>=3) · obserwowane niezgody pool<=2 = 3
    oczekiwane przy baseline = 4 × 0,25 = 1,0 → nadwyżka = 2,0 = 66,67 % niezgód segmentu
    udział w całym zmierzonym korpusie = 2,0 / (3 + 1) = 50 %
    """
    l2, _, _ = M.compute_liczba2(outcomes, 48.0)
    sc = l2["full"]["scarcity_decomposition"]
    assert sc["baseline_disagree_rate_pct"] == pytest.approx(25.0)
    assert sc["le2_disagree_observed"] == 3
    assert sc["le2_disagree_expected_at_baseline"] == pytest.approx(1.0)
    assert sc["le2_disagree_excess_scarcity"] == pytest.approx(2.0)
    assert sc["excess_share_of_le2_disagreements_pct"] == pytest.approx(200.0 / 3)
    assert sc["excess_share_of_measured_disagreements_pct"] == pytest.approx(50.0)
    assert sc["measured_disagreements"] == 4


def test_liczba2_okno_wspolne_przycina_korpus(outcomes):
    """Okno wspólne z Liczbą 1 MUSI odciąć rekord 208 z 01.07 — inaczej cytuje się dwa okna."""
    l2, _, _ = M.compute_liczba2(outcomes, 48.0, window=("2026-08-01", "2026-08-02"))
    assert l2["overlap"]["global"]["n_agree_known"] == 9
    assert l2["overlap"]["pool_ge3"]["n_agree_known"] == 3
    assert l2["full"]["global"]["n_agree_known"] == 10


def test_liczba2_okno_znanej_puli_raportowane_osobno(outcomes):
    l2, _, _ = M.compute_liczba2(outcomes, 48.0)
    pk = l2["pool_known_window"]
    assert pk["records"] == 9
    assert pk["share_of_corpus_pct"] == pytest.approx(100.0 * 9 / 11)
    assert pk["day_first"] == "2026-07-01"


# ───────────────────────── JOIN ─────────────────────────

def test_join_laczy_po_order_id(decisions, outcomes):
    """W oknie 01-02.08 wspólne są 100 (auto-gotowe D, agree=T) i 101 (nie-auto, agree=F)."""
    l1, _, idx = M.compute_liczba1(decisions, 48.0)
    _, _, rows = M.compute_liczba2(outcomes, 48.0)
    join = M.compute_join(rows, idx, ("2026-08-01", "2026-08-02"))
    assert join["n_joined"] == 2
    assert join["matrix"] == {"auto=T,agree=T": 1, "auto=T,agree=F": 0,
                              "auto=F,agree=T": 0, "auto=F,agree=F": 1}
    assert join["agree_given_auto_ready_pct"] == pytest.approx(100.0)
    assert join["agree_given_not_auto_ready_pct"] == pytest.approx(0.0)


# ───────────────── fail-soft / fail-closed per liczba ─────────────────

def test_martwy_korpus_decyzji_nie_daje_liczby_tylko_powod(tmp_path, decisions):
    """NEGATYWNY ORACLE: strumień sprzed 40 dni NIE MOŻE wyprodukować Liczby 1."""
    old = time.time() - 40 * 86400
    os.utime(decisions, (old, old))
    l1, manifest, idx = M.compute_liczba1(decisions, 48.0)
    assert l1["available"] is False and "MARTWY" in l1["reason"]
    assert idx == {} and manifest[0]["skipped"]


def test_pusty_i_brakujacy_korpus_outcomes_daje_null(tmp_path):
    l2, _, rows = M.compute_liczba2(str(tmp_path / "nie-ma.jsonl"), 48.0)
    assert l2["available"] is False and "nie istnieje" in l2["reason"] and rows == []
    pusty = tmp_path / "pusty.jsonl"
    pusty.write_text("")
    l2b, _, _ = M.compute_liczba2(str(pusty), 48.0)
    assert l2b["available"] is False and "PUSTY" in l2b["reason"]


def test_brak_obu_korpusow_to_rekord_z_null_a_nie_crash(tmp_path):
    out_dir = tmp_path / "raport" / "2026-08-05"
    args = M.build_parser().parse_args([
        "--no-telegram", "--out-dir", str(out_dir),
        "--decisions", str(tmp_path / "brak.jsonl"), "--outcomes", str(tmp_path / "brak2.jsonl")])
    rec = M.run(args)
    assert rec["status"] == "DEGRADED"
    assert rec["liczba1"]["pct"] is None and rec["liczba1"]["reason"]
    assert rec["liczba2"]["global"] is None and rec["liczba2"]["reason"]
    seria = json.loads((tmp_path / "raport" / M.SERIES_FILENAME).read_text().strip())
    assert seria["day"] == rec["day"] and seria["status"] == "DEGRADED"
    assert (out_dir / M.SNAPSHOT_MD).exists() and (out_dir / M.SNAPSHOT_JSON).exists()


def test_wyjatek_w_liczbie_nie_wywala_biegu(tmp_path, monkeypatch, decisions, outcomes):
    """Fail-soft: awaria składnika degraduje rekord, ale `main` kończy się exit 0.

    To jest kontrakt wobec `shadow-review`: jeden czytelnik nie może wywalić dobowego biegu.
    """
    def boom(*a, **kw):
        raise RuntimeError("celowa awaria")

    monkeypatch.setattr(M, "compute_liczba1", boom)
    out_dir = tmp_path / "raport" / "2026-08-05"
    code = M.main(["--no-telegram", "--out-dir", str(out_dir),
                   "--decisions", decisions, "--outcomes", outcomes])
    assert code == 0
    seria = json.loads((tmp_path / "raport" / M.SERIES_FILENAME).read_text().strip())
    assert seria["status"] == "DEGRADED"
    assert "celowa awaria" in seria["liczba1"]["reason"]
    # druga liczba nadal policzona — degradacja jest CZĘŚCIOWA, nie zerojedynkowa
    assert seria["liczba2"]["global"]["agree_pct"] == pytest.approx(50.0)


def test_awaria_snapshotu_nie_wywala_biegu_a_szereg_mowi_prawde(tmp_path, monkeypatch,
                                                                decisions, outcomes):
    """Szereg jest APPEND-ONLY, więc linia MUSI powstać PO wszystkich zapisach snapshotu.

    Odwrotna kolejność utrwalała rekord `status=OK` ze wskaźnikiem na snapshot, który nie
    powstał — a linii szeregu z założenia nie da się już poprawić ani skasować. Sprawdzamy
    TREŚĆ linii, nie tylko exit i istnienie pliku (na tym potknęła się poprzednia bramka).
    """
    out_dir = tmp_path / "raport" / "2026-08-05"
    monkeypatch.setattr(M, "write_atomic", lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only fs")))
    code = M.main(["--no-telegram", "--out-dir", str(out_dir),
                   "--decisions", decisions, "--outcomes", outcomes])
    assert code == 0
    seria_path = tmp_path / "raport" / M.SERIES_FILENAME
    assert seria_path.exists()
    seria = json.loads(seria_path.read_text().strip())
    assert seria["status"] == "DEGRADED"
    assert any("snapshot" in d for d in seria["degraded"])
    # nie wolno wskazywać pliku, który nie powstał
    assert seria["snapshot"] is None
    assert not (out_dir / M.SNAPSHOT_JSON).exists()


def test_awaria_samego_raportu_md_zostawia_wskaznik_na_istniejacy_snapshot(
        tmp_path, monkeypatch, decisions, outcomes):
    """Gdy JSON snapshotu POWSTAŁ, a padł tylko raport MD — wskaźnik jest prawdziwy, status DEGRADED."""
    def boom(*a, **kw):
        raise RuntimeError("render padł")

    monkeypatch.setattr(M, "render_md", boom)
    out_dir = tmp_path / "raport" / "2026-08-05"
    assert M.main(["--no-telegram", "--out-dir", str(out_dir),
                   "--decisions", decisions, "--outcomes", outcomes]) == 0
    seria = json.loads((tmp_path / "raport" / M.SERIES_FILENAME).read_text().strip())
    assert seria["status"] == "DEGRADED"
    assert seria["snapshot"] == str(out_dir / M.SNAPSHOT_JSON)
    assert os.path.exists(seria["snapshot"])
    assert not (out_dir / M.SNAPSHOT_MD).exists()


# ───────────────────────── szereg czasowy / read-only ─────────────────────────

def test_szereg_jest_append_only(tmp_path, decisions, outcomes):
    """Dwa biegi = dwie linie; pierwsza linia nietknięta co do bajtu."""
    out_dir = tmp_path / "raport" / "2026-08-05"
    argv = ["--no-telegram", "--out-dir", str(out_dir),
            "--decisions", decisions, "--outcomes", outcomes]
    M.main(argv)
    seria = tmp_path / "raport" / M.SERIES_FILENAME
    pierwsza = seria.read_bytes()
    M.main(argv)
    teraz = seria.read_bytes()
    assert teraz.startswith(pierwsza)
    assert len(teraz.decode().strip().splitlines()) == 2


def test_bieg_nie_dotyka_zrodel_ani_nie_pisze_poza_wyjsciem(tmp_path, decisions, outcomes):
    """Źródła bit-w-bit nietknięte, a JEDYNE nowe pliki to trzy artefakty czytelnika."""
    def _pliki():
        return {os.path.relpath(os.path.join(d, f), str(tmp_path))
                for d, _, fs in os.walk(str(tmp_path)) for f in fs}

    zrodla = {p: (os.stat(p).st_mtime_ns, open(p, "rb").read()) for p in (decisions, outcomes)}
    przed = _pliki()
    M.main(["--no-telegram", "--out-dir", str(tmp_path / "raport" / "2026-08-05"),
            "--decisions", decisions, "--outcomes", outcomes])
    for p, (mtime, blob) in zrodla.items():
        assert os.stat(p).st_mtime_ns == mtime, f"czytelnik dotknął źródła {p}"
        assert open(p, "rb").read() == blob
    assert _pliki() - przed == {
        os.path.join("raport", M.SERIES_FILENAME),
        os.path.join("raport", "2026-08-05", M.SNAPSHOT_JSON),
        os.path.join("raport", "2026-08-05", M.SNAPSHOT_MD),
    }


def test_no_series_nie_dopisuje_do_szeregu(tmp_path, decisions, outcomes):
    out_dir = tmp_path / "raport" / "2026-08-05"
    M.main(["--no-telegram", "--no-series", "--out-dir", str(out_dir),
            "--decisions", decisions, "--outcomes", outcomes])
    assert not (tmp_path / "raport" / M.SERIES_FILENAME).exists()
    assert (out_dir / M.SNAPSHOT_JSON).exists()


def test_rekord_szeregu_ma_liczby_okna_i_tozsamosc_wejsc(tmp_path, decisions, outcomes):
    out_dir = tmp_path / "raport" / "2026-08-05"
    rec = M.run(M.build_parser().parse_args(
        ["--no-telegram", "--out-dir", str(out_dir), "--decisions", decisions, "--outcomes", outcomes]))
    assert rec["schema"] == M.SERIES_SCHEMA and rec["status"] == "OK" and rec["mode"] == "READ_ONLY"
    assert rec["liczba1"]["pct"] == pytest.approx(50.0) and rec["liczba1"]["n"] == 4
    assert rec["liczba1"]["window"]["days_distinct"] == 2
    assert rec["liczba2"]["pool_le2"]["agree_pct"] == pytest.approx(25.0)
    assert rec["liczba2"]["pool_ge3"]["agree_pct"] == pytest.approx(75.0)
    assert rec["liczba2"]["overlap"]["n"] == 9
    assert rec["join"]["n_joined"] == 2
    sciezki = {i["path"] for i in rec["inputs"]}
    assert sciezki == {decisions, outcomes}
    assert all(i["sha256"] for i in rec["inputs"])


def test_ratchet_zrodlo_nie_ma_operacji_kasujacych_i_nadpisujacych_szereg():
    """Ratchet append-only: szereg wolno otwierać wyłącznie w trybie "a"."""
    src = open(TOOL, encoding="utf-8").read()
    for zakazane in ("os.remove", "os.unlink", "shutil.rmtree", "truncate("):
        assert zakazane not in src, f"czytelnik ma operację kasującą: {zakazane}"
    assert 'open(series_path, "w")' not in src and "open(series_path, 'w')" not in src
    assert 'open(series_path, "a", encoding="utf-8")' in src


# ───────────── rejestracja w dobowym przeglądzie cienia ─────────────

def test_czytelnik_jest_zarejestrowany_w_shadow_review_daily():
    """Bez wpisu w READERS suwak nie policzy się sam — to jest cała dostawa sesji 297."""
    suwak = [r for r in H.READERS if r.name == "suwak_autonomii"]
    assert suwak, "suwak_autonomii nie jest zarejestrowany w shadow_review_daily.READERS"
    r = suwak[0]
    assert "dispatch_v2.tools.suwak_autonomii_review" in r.argv
    assert "--no-telegram" in r.argv
    # korpus per-czytelnik = None ŚWIADOMIE: suwak ma dwa źródła i sam raportuje ich
    # świeżość jako null+powód, zamiast dać się wyciąć z szeregu przez bramkę harnessu.
    assert r.corpus is None and r.corpus_from_unit is None
    assert r.out_dir_flag == "--out-dir"


def test_harness_przekazuje_out_dir_czytelnikowi(tmp_path):
    """`out_dir_flag` musi dołożyć katalog dnia do argv — inaczej snapshot idzie w default."""
    marker = tmp_path / "argv.txt"
    r = H.Reader(name="x", argv=["-c", f"import sys;open({str(marker)!r},'w').write(' '.join(sys.argv))"],
                 corpus=None, out_dir_flag="--out-dir")
    res = H.run_reader(r, str(tmp_path), 48.0, dry=False)
    assert res["status"] == "OK"
    assert marker.read_text().endswith(f"--out-dir {tmp_path}")
    assert res["argv"][-2:] == ["--out-dir", str(tmp_path)]


def test_harness_bez_flagi_nie_dokłada_out_dir(tmp_path):
    r = H.Reader(name="x", argv=["-c", "pass"], corpus=None)
    res = H.run_reader(r, str(tmp_path), 48.0, dry=False)
    assert res["argv"][-1] == "pass"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

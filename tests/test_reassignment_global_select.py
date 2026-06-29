"""Testy reassignment_global_select — globalne rozbijanie pile-on przerzutów.

Orchestracja `select()` testowana z ZAMOCKOWANYM `global_allocate` (jego de-pile-on
sekwencyjny jest już pokryty 8/8 w pending_global_resweep + 5 dni shadow) — tu
weryfikujemy GLUE: zdjęcie z holderów, mapowanie allocation→decision(show/hide),
drop'y (stays_with_holder / no_feasible / quality_failed → action=hide), metryki
pile-on before↔after, passthrough <2, flaga ON≠OFF, inwarianty (survivors ⊆ candidates,
maxpile_after ≤ before, każdy hide+show ma swój oid w kandydatach).
"""
import json
import pytest

from dispatch_v2.tools import reassignment_global_select as M


# ---- lekkie atrapy ----

class FakeCS:
    def __init__(self, cid, bag=None, pos="gps"):
        self.courier_id = cid
        self.bag = bag or []
        self.pos_source = pos


class FakeCand:
    def __init__(self, cid, name=None):
        self.courier_id = cid
        self.name = name or f"K{cid}"


class FakeRes:
    def __init__(self, best_cid, holder_cid):
        self.best = FakeCand(best_cid)
        self.candidates = [FakeCand(best_cid), FakeCand(holder_cid)]


def _bag(*oids):
    return [{"order_id": o} for o in oids]


def _rec(rest="R"):
    return {"restaurant": rest, "pickup_coords": [1, 1], "delivery_coords": [2, 2],
            "status": "assigned"}


def _shows(decisions):
    return {o for o, d in decisions.items() if d.get("action") == "show"}


def _hides(decisions):
    return {o for o, d in decisions.items() if d.get("action") == "hide"}


def _patch_alloc(monkeypatch, alloc_map):
    """global_allocate → zwraca alloc_map (oid->{cid,no_courier}) + wypełnia _results_out."""
    def fake(items, fleet, now, _results_out=None):
        holders = {o: h for (o, h) in [(i[0], None) for i in items]}
        if _results_out is not None:
            for oid, _ in items:
                a = alloc_map.get(oid) or {}
                g = a.get("cid")
                if g is not None and not a.get("no_courier"):
                    _results_out[oid] = FakeRes(str(g), "H")
        return alloc_map
    monkeypatch.setattr(M, "global_allocate", fake)


def _patch_quality(monkeypatch, verdicts):
    def fake(a_cand, best, oid, a_pos, b_pos, holder_cid, b_cid, b_bag=None, a_in_fleet=False):
        v = dict(verdicts.get(oid, {"quality_reassign": True}))
        v.setdefault("quality_reason", "test")
        v.setdefault("save_min", 5.0)
        v.setdefault("a_late", False)
        return v
    monkeypatch.setattr(M, "_quality_gate", fake)


# ---- testy select() ----

def test_depile_splits_pileon_across_couriers(monkeypatch):
    """2 zlecenia oba chciane na Jakuba (J) → global_allocate rozbija na J i P →
    2 show na 2 kurierów; maxpile 2→1; spread."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec())]
    fleet = {"H1": FakeCS("H1", _bag("X")), "H2": FakeCS("H2", _bag("Y")),
             "J": FakeCS("J", _bag("Z")), "P": FakeCS("P")}
    cand_best = {"O1": "J", "O2": "J"}            # generator: oba na Jakuba = pile-on 2
    _patch_alloc(monkeypatch, {"O1": {"cid": "J"}, "O2": {"cid": "P"}})
    _patch_quality(monkeypatch, {"O1": {"quality_reassign": True},
                                 "O2": {"quality_reassign": True}})
    dec, metrics = M.select(cands, fleet, M._now_utc(), {}, cand_best)
    assert _shows(dec) == {"O1", "O2"}
    assert dec["O1"]["best_cid"] == "J"
    assert dec["O2"]["best_cid"] == "P"
    assert metrics["maxpile_before"] == 2
    assert metrics["maxpile_after"] == 1
    assert metrics["survivors_out"] == 2
    # inwarianty
    assert set(dec) <= {o for o, _, _ in cands}
    assert metrics["maxpile_after"] <= metrics["maxpile_before"]


def test_stays_with_holder_hidden(monkeypatch):
    """global_allocate zostawia O2 u holdera (H2) → nie ma przerzutu → hide."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec())]
    fleet = {"H1": FakeCS("H1"), "H2": FakeCS("H2"), "J": FakeCS("J")}
    _patch_alloc(monkeypatch, {"O1": {"cid": "J"}, "O2": {"cid": "H2"}})
    _patch_quality(monkeypatch, {})
    dec, metrics = M.select(cands, fleet, M._now_utc(), {}, {"O1": "J", "O2": "J"})
    assert _shows(dec) == {"O1"}
    assert "O2" in _hides(dec) and dec["O2"]["why"] == "stays_with_holder"


def test_quality_fail_hidden(monkeypatch):
    """Globalny kurier ≠ holder ale _quality_gate odrzuca → hide."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec())]
    fleet = {"H1": FakeCS("H1"), "H2": FakeCS("H2"), "J": FakeCS("J"), "P": FakeCS("P")}
    _patch_alloc(monkeypatch, {"O1": {"cid": "J"}, "O2": {"cid": "P"}})
    _patch_quality(monkeypatch, {"O1": {"quality_reassign": True},
                                 "O2": {"quality_reassign": False}})
    dec, _ = M.select(cands, fleet, M._now_utc(), {}, {"O1": "J", "O2": "J"})
    assert _shows(dec) == {"O1"}
    assert dec["O2"]["why"] == "quality_failed_vs_global"


def test_no_feasible_hidden(monkeypatch):
    """no_courier (KOORD) → hide, nie show."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec())]
    fleet = {"H1": FakeCS("H1"), "H2": FakeCS("H2"), "J": FakeCS("J")}
    _patch_alloc(monkeypatch, {"O1": {"cid": "J"}, "O2": {"cid": None, "no_courier": True}})
    _patch_quality(monkeypatch, {})
    dec, _ = M.select(cands, fleet, M._now_utc(), {}, {"O1": "J", "O2": "J"})
    assert _shows(dec) == {"O1"}
    assert dec["O2"]["why"] == "no_feasible_courier_KOORD"


def test_arm_ratunek_vs_oszczednosc(monkeypatch):
    """arm = 'ratunek' gdy a_late else 'oszczędność' (spójne z feed._load_reassign_proposals)."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec())]
    fleet = {"H1": FakeCS("H1"), "H2": FakeCS("H2"), "J": FakeCS("J"), "P": FakeCS("P")}
    _patch_alloc(monkeypatch, {"O1": {"cid": "J"}, "O2": {"cid": "P"}})
    _patch_quality(monkeypatch, {"O1": {"quality_reassign": True, "a_late": True},
                                 "O2": {"quality_reassign": True, "a_late": False}})
    dec, _ = M.select(cands, fleet, M._now_utc(), {}, {"O1": "J", "O2": "J"})
    assert dec["O1"]["arm"] == "ratunek"
    assert dec["O2"]["arm"] == "oszczędność"


def test_singletons_passthrough_no_depile(monkeypatch):
    """2 propozycje na RÓŻNE cele (brak kolizji celu) → obie SHOW passthrough; global_allocate
    NIE wołany (regresja buga 484222/484195 z 29.06: usuwanie wszystkich z holderów fałszowało
    flotę → genuine przerzut błędnie 'stays_with_holder'). De-pile TYLKO realne kolizje."""
    cands = [("O1", "H", _rec()), ("O2", "H", _rec())]   # ten sam holder, RÓŻNE cele
    fleet = {"H": FakeCS("H"), "J": FakeCS("J"), "P": FakeCS("P")}
    called = {"n": 0}
    def boom(items, fleet, now, _results_out=None):
        called["n"] += 1
        return {}
    monkeypatch.setattr(M, "global_allocate", boom)
    dec, metrics = M.select(cands, fleet, M._now_utc(), {}, {"O1": "J", "O2": "P"})
    assert _shows(dec) == {"O1", "O2"}                   # obie pokazane (generator stoi)
    assert dec["O1"].get("passthrough") and dec["O2"].get("passthrough")
    assert dec["O1"].get("best_cid") is None             # passthrough = overlay zostawia feed
    assert called["n"] == 0                              # brak kolizji → global_allocate pominięty
    assert metrics["depiled_groups"] == 0


def test_mixed_singleton_and_pileon(monkeypatch):
    """3 propozycje: O1,O2 na J (kolizja → de-pile), O3 na P (singleton → passthrough)."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec()), ("O3", "H3", _rec())]
    fleet = {"H1": FakeCS("H1"), "H2": FakeCS("H2"), "H3": FakeCS("H3"),
             "J": FakeCS("J"), "P": FakeCS("P"), "Q": FakeCS("Q")}
    # tylko kolidujące (O1,O2) idą do global_allocate; O3 (singleton P) NIE
    def fake_alloc(items, fleet, now, _results_out=None):
        ids = {o for o, _ in items}
        assert ids == {"O1", "O2"}                      # singleton O3 poza de-pile
        amap = {"O1": {"cid": "J"}, "O2": {"cid": "Q"}}
        if _results_out is not None:
            for oid, _ in items:
                _results_out[oid] = FakeRes(amap[oid]["cid"], "H")
        return amap
    monkeypatch.setattr(M, "global_allocate", fake_alloc)
    _patch_quality(monkeypatch, {"O1": {"quality_reassign": True}, "O2": {"quality_reassign": True}})
    dec, metrics = M.select(cands, fleet, M._now_utc(), {}, {"O1": "J", "O2": "J", "O3": "P"})
    assert dec["O3"]["passthrough"] is True             # singleton → pokazany jak jest
    assert dec["O1"]["best_cid"] == "J" and dec["O2"]["best_cid"] == "Q"   # de-piled
    assert metrics["depiled_groups"] == 1
    assert metrics["maxpile_before"] == 2               # J miało 2


# ---- testy run_once() (flaga + passthrough) ----

def _patch_run_once_io(monkeypatch, cands, flag_on, tmp_path):
    monkeypatch.setattr(M.C, "flag", lambda name, default=False: flag_on if name == M.FLAG else default)
    monkeypatch.setattr(M.C, "load_flags", lambda: {})
    osf = tmp_path / "orders.json"
    osf.write_text(json.dumps({"orders": {}}), encoding="utf-8")
    monkeypatch.setattr(M, "ORDERS_STATE", str(osf))
    monkeypatch.setattr(M, "_fresh_candidates", lambda orders, now, ttl: cands)
    monkeypatch.setattr(M, "_alias_map", lambda: {})
    captured = {}
    def cap(decisions, now):
        captured["decisions"] = decisions
        return len(decisions)
    monkeypatch.setattr(M, "_atomic_write_channel", cap)
    monkeypatch.setattr(M, "_append_verdict", lambda row: None)
    return captured


def test_flag_off_is_noop(monkeypatch, tmp_path):
    captured = _patch_run_once_io(monkeypatch, [], flag_on=False, tmp_path=tmp_path)
    out = M.run_once()
    assert out == {"skipped": "flag_off"}
    assert "decisions" not in captured          # nic nie zapisane przy OFF


def test_passthrough_single_candidate(monkeypatch, tmp_path):
    """<2 kandydatów = brak pile-on → passthrough (action=show, BEZ override) żeby overlay
    NIE ukrył jedynej legalnej propozycji."""
    cands = [("O1", "H1", _rec("Resto"))]
    captured = _patch_run_once_io(monkeypatch, cands, flag_on=True, tmp_path=tmp_path)
    out = M.run_once()
    assert out["passthrough"] is True
    assert out["survivors_out"] == 1
    d = captured["decisions"]["O1"]
    assert d["action"] == "show" and d["passthrough"] is True and d["best_cid"] is None


def test_run_once_depile_writes_decisions(monkeypatch, tmp_path):
    """run_once z ≥2 kandydatami pile-on → 1 show (rozbity) + 1 show na innego kuriera."""
    cands = [("O1", "H1", _rec()), ("O2", "H2", _rec())]
    captured = _patch_run_once_io(monkeypatch, cands, flag_on=True, tmp_path=tmp_path)
    monkeypatch.setattr(M.CR, "dispatchable_fleet",
                        lambda: [FakeCS("H1"), FakeCS("H2"), FakeCS("J"), FakeCS("P")])
    monkeypatch.setattr(M, "_candidate_best_cids", lambda c: {"O1": "J", "O2": "J"})
    _patch_alloc(monkeypatch, {"O1": {"cid": "J"}, "O2": {"cid": "P"}})
    _patch_quality(monkeypatch, {"O1": {"quality_reassign": True},
                                 "O2": {"quality_reassign": True}})
    out = M.run_once()
    assert out["maxpile_before"] == 2 and out["maxpile_after"] == 1
    assert out["spread_improved"] is True
    assert _shows(captured["decisions"]) == {"O1", "O2"}


def test_run_once_depile_hides_losers(monkeypatch, tmp_path):
    """5 zleceń wszystkie na Jakuba (J), global_allocate daje tylko 1 na J resztę zostawia
    u holderów → 1 show + 4 hide (Adrian: '10 na Jakuba → 1-2', reszta ukryta)."""
    cands = [(f"O{i}", f"H{i}", _rec()) for i in range(5)]
    captured = _patch_run_once_io(monkeypatch, cands, flag_on=True, tmp_path=tmp_path)
    monkeypatch.setattr(M.CR, "dispatchable_fleet",
                        lambda: [FakeCS(f"H{i}") for i in range(5)] + [FakeCS("J")])
    monkeypatch.setattr(M, "_candidate_best_cids", lambda c: {f"O{i}": "J" for i in range(5)})
    # global_allocate: O0→J (survivor), reszta zostaje u holderów (de-piled)
    _patch_alloc(monkeypatch, {"O0": {"cid": "J"},
                               **{f"O{i}": {"cid": f"H{i}"} for i in range(1, 5)}})
    _patch_quality(monkeypatch, {})
    out = M.run_once()
    assert out["maxpile_before"] == 5      # generator: 5 na Jakuba
    assert out["maxpile_after"] == 1       # po rozbiciu: 1 na Jakuba
    assert out["survivors_out"] == 1 and out["hidden_out"] == 4
    assert _shows(captured["decisions"]) == {"O0"}
    assert _hides(captured["decisions"]) == {"O1", "O2", "O3", "O4"}

"""K5 (2026-07-05) — ścieżka LIVE resweepa: podmiana propozycji dla KONSOLI.

Decyzja Adriana (Wariant A): live = pending_proposals + 1-klik konsoli, NIE
Telegram. Testy na fixtures z `test_pending_global_resweep` (jedno źródło
fake'ów) + izolowany pending store (locked_mutate na tmp — kanon L7.5).

Osie: ON≠OFF · bramka geometrii nie do ominięcia · podmiana decision_record
z provenance (stary/nowy kurier, margines) i zachowaniem message_id ·
TOCTOU-guardy wewnątrz locka (gone/changed) · cap per tick · fail-soft IO.
"""
import json
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import pending_proposals_store as PPS  # noqa: E402
from dispatch_v2.tools import pending_global_resweep as PGR  # noqa: E402
from dispatch_v2.tests.test_pending_global_resweep import (  # noqa: E402
    _N, _cs, _setup,
)


def _arm_live(tmp_path, monkeypatch, proposed_best, gate_open=True):
    """_setup + flagi LIVE + izolacja pending store na tym samym pliku co PGR."""
    out = _setup(tmp_path, monkeypatch, proposed_best)
    pending_path = str(tmp_path / "pending.json")
    monkeypatch.setattr(PPS, "PENDING_PATH", pending_path)
    # ANTY-PROD (klasa: testy piszą do PROD — Załącznik C protokołu #0)
    assert "/dispatch_state/" not in PPS.PENDING_PATH
    assert str(tmp_path) in PPS.PENDING_PATH
    monkeypatch.setattr(
        C, "flag",
        lambda n, d=False: True if n in (PGR.FLAG, PGR.FLAG_LIVE) else d)
    monkeypatch.setattr(PGR, "live_gate_open", lambda: gate_open)
    return out, pending_path


def _patch_serializer(monkeypatch):
    from dispatch_v2 import shadow_dispatcher as SD
    monkeypatch.setattr(
        SD, "_serialize_result",
        lambda res, evt, lat: {"order_id": evt.split("-")[-1],
                               "verdict": "PROPOSE",
                               "best": {"courier_id": str(res.best.courier_id),
                                        "score": res.best.score,
                                        "plan": {"sequence": []}},
                               "auto_route": "ACK"})


def _pending(path):
    return json.loads(open(path).read())


def test_live_off_no_mutation_byte_parity(tmp_path, monkeypatch):
    """FLAG_LIVE=OFF: pending nietknięty, brak live_action w wierszach,
    live_acted=0 (zachowanie shadow bajt-parytetne)."""
    out = _setup(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    before = _pending(str(tmp_path / "pending.json"))
    s = PGR.run_once(now=_N)
    assert s.get("live_acted", 0) == 0
    assert _pending(str(tmp_path / "pending.json")) == before
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert all("live_action" not in r for r in rows)


def test_live_on_gate_closed_zero_actions(tmp_path, monkeypatch):
    """Bramka L6.C zamknięta → ZERO akcji mimo FLAG_LIVE=ON (nie do ominięcia).
    Serializer ZAPATCHOWANY jak w happy-path — jedyną zaporą jest BRAMKA
    (bez tego probe gate-bypass przeżywał na fail-soft serializacji — C14b)."""
    _patch_serializer(monkeypatch)
    _, pp = _arm_live(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"},
                      gate_open=False)
    before = _pending(pp)
    s = PGR.run_once(now=_N)
    assert s.get("live_acted", 0) == 0
    assert _pending(pp) == before


def test_live_on_swaps_decision_record_with_provenance(tmp_path, monkeypatch):
    """Pile-on A,A,A → o2/o3 przerzucone: pending[oid].decision_record wskazuje
    NOWEGO kuriera, provenance resweep_live kompletna, message_id zachowany."""
    _patch_serializer(monkeypatch)
    out, pp = _arm_live(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    s = PGR.run_once(now=_N)
    assert s["would_repropose"] == 2 and s["live_acted"] == 2, s
    pend = _pending(pp)
    swapped = {oid: e for oid, e in pend.items() if "resweep_live" in e}
    assert set(swapped) == {"o2", "o3"}, sorted(pend)
    for oid, e in swapped.items():
        best = e["decision_record"]["best"]
        assert best["courier_id"] in ("B", "C") and best["courier_id"] != "A"
        prov = e["resweep_live"]
        assert prov["old_cid"] == "A" and prov["new_cid"] == best["courier_id"]
        assert prov["reason"] == "rozjazd_kierunkow" and prov["ts"] == _N.isoformat()
        assert e["message_id"] == 1          # metadane wysyłki NIETKNIĘTE
    # o1 bez zmian (best zgodny z alokacją globalną)
    assert "resweep_live" not in pend["o1"]
    rows = {r["order_id"]: r for r in
            (json.loads(l) for l in out.read_text().splitlines())}
    assert rows["o2"]["live_action"] == "acted" and rows["o3"]["live_action"] == "acted"


def test_toctou_gone_entry_skipped_inside_lock(tmp_path, monkeypatch):
    """Wpis znika między compute a lockiem (przypisanie) → skip_gone, zero mutacji."""
    _patch_serializer(monkeypatch)
    out, pp = _arm_live(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    real_mutate = PPS.locked_mutate

    def _racing_mutate(fn, path=PPS.PENDING_PATH):
        def _wrapped(pending):
            pending.pop("o2", None)   # symulacja: panel_watcher pop po ASSIGN
            fn(pending)
        return real_mutate(_wrapped, path)

    monkeypatch.setattr(PPS, "locked_mutate", _racing_mutate)
    s = PGR.run_once(now=_N)
    rows = {r["order_id"]: r for r in
            (json.loads(l) for l in out.read_text().splitlines())}
    assert rows["o2"]["live_action"] == "skip_gone", rows["o2"]
    assert s["live_acted"] == 1          # o3 nadal przeszło


def test_toctou_changed_proposal_skipped(tmp_path, monkeypatch):
    """Inny pisarz podmienił propozycję (best≠proposed_cid z compute) → skip_changed."""
    _patch_serializer(monkeypatch)
    out, pp = _arm_live(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    real_mutate = PPS.locked_mutate

    def _racing_mutate(fn, path=PPS.PENDING_PATH):
        def _wrapped(pending):
            if "o3" in pending:
                pending["o3"]["decision_record"]["best"]["courier_id"] = "ZZZ"
            fn(pending)
        return real_mutate(_wrapped, path)

    monkeypatch.setattr(PPS, "locked_mutate", _racing_mutate)
    PGR.run_once(now=_N)
    rows = {r["order_id"]: r for r in
            (json.loads(l) for l in out.read_text().splitlines())}
    assert rows["o3"]["live_action"] == "skip_changed", rows["o3"]


def test_tick_cap_limits_actions(tmp_path, monkeypatch):
    """Cap per tick: przy 2 kandydatach i capie=1 → 1 acted + 1 skip_tick_cap."""
    _patch_serializer(monkeypatch)
    out, _ = _arm_live(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})
    monkeypatch.setattr(PGR, "LIVE_MAX_ACTIONS_PER_TICK", 1)
    s = PGR.run_once(now=_N)
    assert s["live_acted"] == 1
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    kinds = sorted(r.get("live_action") for r in rows if r.get("live_action"))
    assert kinds == ["acted", "skip_tick_cap"], kinds


def test_io_failure_is_failsoft_tick_survives(tmp_path, monkeypatch):
    """locked_mutate rzuca (IO) → skip_io_fail, tick i jsonl przeżywają."""
    _patch_serializer(monkeypatch)
    out, _ = _arm_live(tmp_path, monkeypatch, {"o1": "A", "o2": "A", "o3": "A"})

    def _boom(fn, path=None):
        raise OSError("disk on fire")

    monkeypatch.setattr(PPS, "locked_mutate", _boom)
    s = PGR.run_once(now=_N)
    assert s["live_acted"] == 0 and s["would_repropose"] == 2
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert sum(1 for r in rows if r.get("live_action") == "skip_io_fail") == 2

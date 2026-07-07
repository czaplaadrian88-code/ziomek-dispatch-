"""S28-C — bramka korpusowa: schema-aware bucket dla rekordów sprzed wr1.

Kontekst (A2_worldreplay_minus40): rekordy `schema=wr0` (sprzed deployu wr1
07.07) NIE mają `live_inputs` (loadgov EWMA / K07 prefetch), więc replay liczy
je od nowa w świeżym procesie → różnica jest LUKĄ NAGRYWANIA, nie bugiem
determinizmu (np. kara loadgov −40 nienałożona w replayu). Nocna bramka 02:00
wygenerowała 12 fałszywych „ROZNICA-KRYTYCZNA". Fix: rekordy schema<wr1 →
POMINIĘTE (skipped_pre_wr1), NIE liczone jako różnice; realne różnice na wr1
zachowane.

Strażnik behawioralny (C13): wr0 pominięte / wr1 realna różnica przeżywa /
mutation-probe (ten sam rekord przetagowany na wr1 → różnica WRACA = dowód, że
suppression pochodzi WYŁĄCZNIE z tagu schematu, nie z czegoś innego).
"""
import json
from pathlib import Path

from dispatch_v2.tools import world_replay_gate as G


def _write(dirpath: Path, recs):
    f = dirpath / "world_record-20260706.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return f


def _rec(oid, ts, schema):
    return {"order_id": oid, "ts": ts, "schema": schema,
            "now": ts, "verdict": "PROPOSE"}


def _extract(cid="484", score=-1.0):
    return {"verdict": "PROPOSE", "reason": "r", "best_cid": cid,
            "best_score": score, "pool_feasible": 5, "pool_total": 10}


def _shadow_idx(*pairs):
    # pairs: (oid, ts, best_cid) — zapis kanoniczny (żywa decyzja)
    idx = {}
    for oid, ts, cid in pairs:
        idx.setdefault(str(oid), []).append(
            {"order_id": str(oid), "ts": ts, "verdict": "PROPOSE", "reason": "r",
             "best": {"courier_id": cid, "score": -1.0},
             "pool_feasible_count": 5, "pool_total_count": 10})
    return idx


TS_WR0 = "2026-07-06T15:39:00+00:00"   # 485927 (case z A2 — schema=wr0)
TS_WR1 = "2026-07-06T18:00:00+00:00"   # 486006 (schema=wr1, realny rozjazd)


def test_wr0_skipped_wr1_diff_preserved(tmp_path, monkeypatch):
    _write(tmp_path, [
        _rec("485927", TS_WR0, "wr0"),   # LUKA nagrywania — musi być POMINIĘTY
        _rec("486006", TS_WR1, "wr1"),   # realny rozjazd — musi ZOSTAĆ
    ])
    # replay daje INNY best_cid dla OBU → oba BYŁYBY krytyczne, gdyby nie filtr
    monkeypatch.setattr(G.WR, "replay_one", lambda rec: (_extract(cid="999"), 0))
    idx = _shadow_idx(("485927", TS_WR0, "484"), ("486006", TS_WR1, "484"))

    rep = G.run_gate(None, None, record_dir=str(tmp_path), shadow_index=idx)

    # wr0 pominięty (nie replayowany, nie liczony jako różnica)
    assert rep["skipped_pre_wr1"] == 1
    assert rep["n"] == 1                                   # tylko wr1 certyfikowany
    assert all(r["order_id"] != "485927" for r in rep["roznice"]), \
        "wr0 485927 nie może pojawić się jako różnica (to luka nagrywania)"
    # wr1 realna różnica ZACHOWANA
    assert rep["roznice_krytyczne_n"] == 1
    assert any(r["order_id"] == "486006" and r["krytyczna"] for r in rep["roznice"])
    assert rep["verdict"] == "DIFFS"


def test_mutation_probe_wr0_as_wr1_surfaces(tmp_path, monkeypatch):
    """Dowód kauzalny: TEN SAM rekord 485927 przetagowany na wr1 → różnica WRACA.
    Gdyby suppression brała się skądkolwiek indziej niż z tagu schematu, ten
    test by nie wykrył różnicy → strażnik ma zęby."""
    _write(tmp_path, [_rec("485927", TS_WR0, "wr1")])       # <-- wr1 (mutacja)
    monkeypatch.setattr(G.WR, "replay_one", lambda rec: (_extract(cid="999"), 0))
    idx = _shadow_idx(("485927", TS_WR0, "484"))

    rep = G.run_gate(None, None, record_dir=str(tmp_path), shadow_index=idx)
    assert rep["skipped_pre_wr1"] == 0
    assert rep["roznice_krytyczne_n"] == 1
    assert any(r["order_id"] == "485927" for r in rep["roznice"])


def test_verdict_txt_reports_skipped_pre_wr1(tmp_path, monkeypatch):
    _write(tmp_path, [_rec("485927", TS_WR0, "wr0"), _rec("486006", TS_WR1, "wr1")])
    monkeypatch.setattr(G.WR, "replay_one", lambda rec: (_extract(), 0))
    idx = _shadow_idx(("486006", TS_WR1, "484"))
    rep = G.run_gate(None, None, record_dir=str(tmp_path), shadow_index=idx)
    txt = G.render_verdict_txt(rep)
    assert "pominiete schema<wr1: 1" in txt
    # wr1 zgodny → PARITY (wr0 nie psuje werdyktu)
    assert rep["verdict"] == "PARITY" and rep["zgodne"] == 1

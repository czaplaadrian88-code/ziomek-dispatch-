"""ETAP 4 (2026-06-10, audyt Z-04) — unifikacja flag decyzyjnych cross-proces.

Mechanizm: common.decision_flag(name) = flags.json (hot-reload, wspólny dla
wszystkich procesów) → stała modułu (env-default z czasu importu) → False.

Testy:
  1. flags.json WYGRYWA ze stałą modułu (to jest unifikacja: czasówka bez env
     dostaje wartości shadow).
  2. Brak klucza w flags.json → fallback do stałej modułu (idiom testów
     patchujących common.ENABLE_X działa jak przed ETAP 4; conftest wycina
     klucze ETAP4 z tmp-kopii).
  3. Fingerprint identyczny niezależnie od env procesu, gdy flags.json ma klucze
     (symulacja: proces "czasowka" = stałe default vs "shadow" = stałe z
     override.conf → ten sam fingerprint).
  4. INTEGRACJA (walidacja ETAP 4): ten sam order_event + ta sama zamockowana
     flota przez assess_order przy stałych "czasowka" i "shadow" → IDENTYCZNY
     ranking (bo flags.json jest kanonem dla obu).
"""
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import dispatch_v2.common as C
from dispatch_v2.dispatch_pipeline import assess_order

# Wartości shadow z override.conf na 2026-06-10 (flag_inventory_etap4.md).
SHADOW_VALUES = {
    "ENABLE_BUNDLE_DELIV_SPREAD_CAP": True,
    "ENABLE_R1_PROGRESSIVE_CLIP": True,
    "ENABLE_V319H_CONTINUATION_GUARD": True,
    "ENABLE_A2_RELIABILITY_SOFT_SCORE": True,
    "ENABLE_FAIL12_SCHEDULE_FAILOPEN": True,
    "ENABLE_F4_COURIER_POS_PICKUP_PROXY": True,
    "ENABLE_F4_COURIER_POS_INTERP": True,
    "ENABLE_C2_NEG_GAP_DECAY": True,
    "ENABLE_PRE_SHIFT_DEPARTURE_CLAMP": True,
    "ENABLE_OBJ_SPAN_COST": True,
    "ENABLE_OBJ_R6_SOFT_DEADLINE": True,
    "ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD": True,
    "ENABLE_OBJ_PICKUP_FRESHNESS": False,
    "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE": False,
    "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT": False,
}


def _write_flags(extra: dict):
    """Dopisz klucze do (spatchowanego przez conftest) tmp flags.json + reset cache."""
    p = C.FLAGS_PATH
    d = json.loads(open(p, encoding="utf-8").read())
    d.update(extra)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    C._flags_cache = None
    C._flags_mtime = 0


def _strip_flags(keys):
    p = C.FLAGS_PATH
    d = json.loads(open(p, encoding="utf-8").read())
    for k in keys:
        d.pop(k, None)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    C._flags_cache = None
    C._flags_mtime = 0


def test_decision_flag_flagsjson_wins(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_R1_PROGRESSIVE_CLIP", False, raising=False)
    _write_flags({"ENABLE_R1_PROGRESSIVE_CLIP": True})
    assert C.decision_flag("ENABLE_R1_PROGRESSIVE_CLIP") is True
    _write_flags({"ENABLE_R1_PROGRESSIVE_CLIP": False})
    monkeypatch.setattr(C, "ENABLE_R1_PROGRESSIVE_CLIP", True, raising=False)
    assert C.decision_flag("ENABLE_R1_PROGRESSIVE_CLIP") is False


def test_decision_flag_fallback_to_module_const(monkeypatch):
    # conftest wyciął klucze ETAP4 z tmp-kopii — fallback do stałej modułu.
    _strip_flags(["ENABLE_R1_PROGRESSIVE_CLIP"])
    monkeypatch.setattr(C, "ENABLE_R1_PROGRESSIVE_CLIP", True, raising=False)
    assert C.decision_flag("ENABLE_R1_PROGRESSIVE_CLIP") is True
    monkeypatch.setattr(C, "ENABLE_R1_PROGRESSIVE_CLIP", False, raising=False)
    assert C.decision_flag("ENABLE_R1_PROGRESSIVE_CLIP") is False


def test_decision_flag_unknown_name_safe():
    assert C.decision_flag("ENABLE_ETAP4_NONEXISTENT_FLAG") is False


def test_all_etap4_flags_have_module_const():
    """Każda flaga z listy ma stałą-fallback w common (literówka w liście = głośno)."""
    for name in C.ETAP4_DECISION_FLAGS:
        assert hasattr(C, name), f"brak stałej-fallback common.{name}"


def _set_consts(monkeypatch, values):
    for name, val in values.items():
        monkeypatch.setattr(C, name, val, raising=False)


def test_fingerprint_identical_across_process_envs(monkeypatch):
    """flags.json ma komplet kluczy → fingerprint nie zależy od env procesu."""
    _write_flags(SHADOW_VALUES)
    # proces "czasowka": stałe = env-defaulty (OFF + commit_div=True)
    _set_consts(monkeypatch, {k: (k == "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE")
                              for k in SHADOW_VALUES})
    fp_czasowka = C.flag_fingerprint()
    # proces "shadow": stałe = wartości z override.conf
    _set_consts(monkeypatch, SHADOW_VALUES)
    fp_shadow = C.flag_fingerprint()
    assert fp_czasowka == fp_shadow
    assert "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=0" in fp_shadow
    assert "ENABLE_R1_PROGRESSIVE_CLIP=1" in fp_shadow


# ─── INTEGRACJA: identyczny ranking assess_order cross-proces ────────────────

BIALYSTOK_CENTER = (53.1325, 23.1688)


def _build_fleet():
    shift_end = datetime(2026, 4, 20, 20, 0, tzinfo=timezone.utc)
    fleet = {}
    for cid, ps, pos in [
        ("etap4_c1", "gps", (53.130, 23.165)),
        ("etap4_c2", "gps", (53.150, 23.190)),
        ("etap4_c3", "no_gps", BIALYSTOK_CENTER),
    ]:
        fleet[cid] = SimpleNamespace(
            courier_id=cid,
            name=f"Etap4_{cid}",
            pos=pos,
            pos_source=ps,
            pos_age_min=2.0 if ps == "gps" else None,
            shift_end=shift_end,
            shift_start_min=0,
            bag=[],
        )
    return fleet


def _order_event():
    return {
        "order_id": "ETAP4_INTEGRATION_TEST",
        "restaurant": "Test Restaurant Etap4",
        "delivery_address": "Test delivery Etap4",
        "pickup_coords": [53.133, 23.169],
        "delivery_coords": [53.145, 23.185],
        "pickup_at_warsaw": "2026-04-20T17:25:00+02:00",
        "pickup_time_minutes": None,
    }


def _ranking(res):
    rows = []
    for c in (res.candidates or []):
        score = getattr(c, "score", None)
        if score is None:
            score = (getattr(c, "metrics", {}) or {}).get("score")
        rows.append((c.courier_id,
                     round(float(score), 4) if score is not None else None,
                     getattr(c, "verdict", None)))
    best = getattr(res, "best", None)
    best_id = getattr(best, "courier_id", None) if best is not None else None
    return {"rows": sorted(rows), "best": best_id,
            "decision": getattr(res, "decision", None)}


def test_assess_order_ranking_identical_czasowka_vs_shadow(monkeypatch):
    """Walidacja ETAP 4: ten sam order + flota → identyczny ranking niezależnie
    od env-defaultów procesu, bo flags.json jest kanonem dla decision_flag."""
    _write_flags(SHADOW_VALUES)
    now = datetime(2026, 4, 20, 15, 0, 0, tzinfo=timezone.utc)

    # przebieg "czasowka" (env-defaulty)
    _set_consts(monkeypatch, {k: (k == "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE")
                              for k in SHADOW_VALUES})
    res_cz = assess_order(_order_event(), _build_fleet(), restaurant_meta=None, now=now)

    # przebieg "shadow" (env z override.conf)
    _set_consts(monkeypatch, SHADOW_VALUES)
    res_sh = assess_order(_order_event(), _build_fleet(), restaurant_meta=None, now=now)

    assert _ranking(res_cz) == _ranking(res_sh), (
        f"ROZJAZD RANKINGU cross-proces:\nczasowka={_ranking(res_cz)}\n"
        f"shadow={_ranking(res_sh)}")

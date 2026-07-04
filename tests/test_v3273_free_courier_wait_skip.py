"""tech-debt #38 re-scope (2026-05-18) — v3273 wait hard-reject NIE dla wolnego kuriera.

Decyzja Adrian 2026-05-18: "jeżeli kurier jest wolny i nie ma lepszych opcji — niech
bierze; jeżeli ma 0 w bagu, lepiej żeby czekał 20 min niż stał godzinę bezczynnie".

Fix `dispatch_pipeline.py` (~2570): hard-reject `v3273_wait_courier` (verdict→NO) tylko
gdy bag kuriera ma order `assigned` (pending pickup, picked_up_at is None). Bag pusty /
wszystkie picked_up → skip reject, verdict zostaje MAYBE, penalty soft.

Integration test na archetypie 472791 (Piotr 470 picked_up-almost-done bag=1, Tomek 514
pre_shift bag=2) — real fixture tools/fixtures/472791_archetype.json (Lekcja #28: nie mock).
Diagnoza: eod_drafts/2026-05-18/replay_validation_38_findings_2026-05-18.md.
"""
import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

FIXTURE = str(Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "472791_archetype.json")


def _run_replay():
    """Reload common (flag czytany przy module-load) + uruchom instrumentowany replay."""
    from dispatch_v2 import common
    importlib.reload(common)
    from dispatch_v2.tools import replay_feasibility
    return replay_feasibility.replay(FIXTURE)


def test_free_courier_picked_up_bag_wait_not_hard_rejected():
    """Piotr 470 (bag=1 picked_up — brak pending pickupu) z wait ~22min pod nowym
    pickupem: hard-reject SKIPPED → verdict MAYBE → czysta feasible PROPOSE best=470."""
    os.environ["ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP"] = "1"
    out = _run_replay()
    f470 = out["final_by_cid"].get("470")
    assert f470 is not None, "Piotr 470 brak w result.candidates"
    assert f470["feasibility_verdict"] == "MAYBE", f"oczekiwano MAYBE, jest {f470}"
    assert out["result"]["best_cid"] == "470", out["result"]
    assert out["result"]["pool_feasible_count"] >= 1, out["result"]


def test_kill_switch_restores_hard_reject():
    """ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP=0 → stary hard-reject niezależny
    od bagu: Piotr 470 wypada z puli feasible (verdict NO).

    Hermetyzacja 2026-07-04: `result.candidates` na ścieżce PROPOSE = feasible-only,
    a feasibility DRUGIEGO kuriera (514, r6_max ~34-36 na granicy HARD 35) zależy od
    ŻYWEGO zegara — osrm_client.route()/table() biorą get_traffic_multiplier(now())
    (by design dla live; replay majowego fixture'a dziedziczy dzisiejszy kubełek).
    Weekday → 514 infeasible → best-effort pakował OBU do candidates (470 z NO);
    sobota-lunch → 514 feasible → 470 (NO) poza candidates → stara asercja padała.
    Nowa asercja = intencja kill-switcha niezależnie od losu 514: 470 NIGDY nie jest
    feasible/best (a jeśli w ogóle widoczny w candidates, to z NO + hard_reject reason).
    Kontrast ON≠OFF: test 1 (skip=1) wymaga 470 MAYBE i best=470."""
    os.environ["ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP"] = "0"
    try:
        out = _run_replay()
        assert out["result"]["best_cid"] != "470", (
            "kill-switch OFF nie przywrócił hard-rejectu — 470 dalej wygrywa: "
            f"{out['result']}")
        f470 = out["final_by_cid"].get("470")
        if f470 is not None:
            # ścieżka best-effort (nikt feasible) — 470 widoczny, ale z NO
            assert f470["feasibility_verdict"] == "NO", f"oczekiwano NO, jest {f470}"
            assert "v3273_wait_courier_hard_reject" in (f470["feasibility_reason"] or ""), f470
        else:
            # ścieżka PROPOSE (ktoś inny feasible) — 470 wycięty z feasible-only candidates
            assert out["result"]["pool_feasible_count"] < out["result"]["pool_total_count"], (
                "470 zniknął z candidates, ale pool_feasible==pool_total — "
                f"to nie hard-reject: {out['result']}")
    finally:
        os.environ["ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP"] = "1"


if __name__ == "__main__":
    test_free_courier_picked_up_bag_wait_not_hard_rejected()
    print("test 1 PASS — wolny kurier (picked_up bag) NIE hard-rejected")
    test_kill_switch_restores_hard_reject()
    print("test 2 PASS — kill-switch przywraca hard-reject")
    print("2/2 PASS")

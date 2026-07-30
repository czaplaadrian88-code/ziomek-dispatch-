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
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dispatch_v2 import common as C

FIXTURE = str(Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "472791_archetype.json")


def _run_replay():
    """Uruchom instrumentowany replay bez przeładowania globalnego stanu flag.

    Hermetyzacja 2026-07-05 (2. odsłona tej samej klasy — Lekcja: żywy zegar):
    osrm_client/chain_eta biorą get_traffic_multiplier(now()) → replay majowego
    fixture'a dziedziczył BIEŻĄCY kubełek ruchu (sobota-lunch ≠ weekday ≠
    niedziela-wieczór; 04.07 padła asercja przy 514-feasible, 05.07 przy
    0-feasible→best_effort best=470). Zamrażamy mnożnik na 1.0 NA CZAS replayu
    (osrm cache bezpieczny: re-multiply z osrm_raw_duration_s), przywracamy
    w finally — testy przestają zależeć od godziny uruchomienia."""
    from dispatch_v2 import osrm_client
    _frozen = lambda dt_utc, *a, **k: 1.0  # noqa: E731
    _orig = {
        "common_v1": C.get_traffic_multiplier,
        "common_v2": C.get_traffic_multiplier_v2,
        "osrm_v1": osrm_client.get_traffic_multiplier,
        "osrm_v2": osrm_client.get_traffic_multiplier_v2,
    }
    C.get_traffic_multiplier = _frozen
    C.get_traffic_multiplier_v2 = _frozen
    osrm_client.get_traffic_multiplier = _frozen
    osrm_client.get_traffic_multiplier_v2 = _frozen
    try:
        from dispatch_v2.tools import replay_feasibility
        return replay_feasibility.replay(FIXTURE)
    finally:
        C.get_traffic_multiplier = _orig["common_v1"]
        C.get_traffic_multiplier_v2 = _orig["common_v2"]
        osrm_client.get_traffic_multiplier = _orig["osrm_v1"]
        osrm_client.get_traffic_multiplier_v2 = _orig["osrm_v2"]


def test_free_courier_picked_up_bag_wait_not_hard_rejected(monkeypatch):
    """Piotr 470 (bag=1 picked_up — brak pending pickupu) z wait ~22min pod nowym
    pickupem: hard-reject SKIPPED → verdict MAYBE → czysta feasible PROPOSE best=470."""
    monkeypatch.setattr(C, "ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP", True)
    out = _run_replay()
    f470 = out["final_by_cid"].get("470")
    assert f470 is not None, "Piotr 470 brak w result.candidates"
    assert f470["feasibility_verdict"] == "MAYBE", f"oczekiwano MAYBE, jest {f470}"
    assert out["result"]["best_cid"] == "470", out["result"]
    assert out["result"]["pool_feasible_count"] >= 1, out["result"]


def test_kill_switch_restores_hard_reject(monkeypatch):
    """ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP=0 → stary hard-reject niezależny
    od bagu: Piotr 470 wypada z puli feasible (verdict NO).

    Hermetyzacja 2026-07-05 (3. odsłona; poprzednie 04.07 i pierwotna — obie
    uzależniały asercję od losu DRUGIEGO kuriera 514, którego R6 balansuje na
    granicy HARD 35 [±1 min od żywego OSRM/kubełka ruchu]):
    - INTENCJA kill-switcha = los 470: OFF → 470 hard-rejected z puli FEASIBLE
      (verdict NO + reason v3273_wait_courier_hard_reject). To jedyna twarda
      asercja tego testu; kontrast ON≠OFF z testem 1 (ON: 470 MAYBE + best).
    - `best_cid != 470` było NADASERCJĄ sprzeczną z doktryną always-propose:
      przy 0 feasible ścieżka best-effort LEGALNIE wybiera 470 (może łamać
      HARD, otagowana ALERT/best_effort — „sentinel best-effort = poprawne").
    - Gałąź feasible (gdy 514 przejdzie): best ≠ 470 i 470 poza feasible.
    - Gałąź best-effort (gdy 514 padnie): best może być 470, ale werdykt MUSI
      być uczciwie otagowany (ALERT / best_effort), a 470 pozostaje NO."""
    monkeypatch.setattr(C, "ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP", False)
    out = _run_replay()
    f470 = out["final_by_cid"].get("470")

    assert f470 is not None, f"470 brak w candidates: {out['result']}"
    assert f470["feasibility_verdict"] == "NO", f"oczekiwano NO, jest {f470}"
    assert "v3273_wait_courier_hard_reject" in (
        f470["feasibility_reason"] or ""), f470


def test_mutation_removing_free_courier_skip_is_detected(monkeypatch):
    """Wymuszenie starego hard-rejectu czerwieni kontrakt wariantu ON."""

    monkeypatch.setattr(C, "ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP", True)
    baseline = _run_replay()["final_by_cid"]["470"]
    assert baseline["feasibility_verdict"] == "MAYBE"

    monkeypatch.setattr(C, "ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP", False)
    mutant = _run_replay()["final_by_cid"]["470"]
    with pytest.raises(AssertionError):
        assert mutant["feasibility_verdict"] == "MAYBE"


if __name__ == "__main__":
    test_free_courier_picked_up_bag_wait_not_hard_rejected()
    print("test 1 PASS — wolny kurier (picked_up bag) NIE hard-rejected")
    test_kill_switch_restores_hard_reject()
    print("test 2 PASS — kill-switch przywraca hard-reject")
    print("2/2 PASS")

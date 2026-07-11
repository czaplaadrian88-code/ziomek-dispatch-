"""Tech debt #14 — score-based resolve_cid testy (2026-05-07).

Coverage:
  GRUPA 1: exact match (case-sensitive + case-insensitive)
  GRUPA 2: score-based fallback (BUG FIX path — Adrian Citko regression)
  GRUPA 3: ambiguity / tie / unmatched edge cases
  GRUPA 4: side-effect logging (RESOLVE_CID_AMBIGUOUS_RESOLVED)

Pattern: custom-runner (NIE pytest fixtures); use isolated_shift_state
context manager dla testow ktore weryfikuja learning_log.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.shift_notifications import worker
from dispatch_v2.shift_notifications import state as shift_state
from dispatch_v2.tests._shift_test_helpers import isolated_shift_state

# `resolve_cid()` emituje debug także w grupach testujących sam scoring, poza
# blokami `isolated_shift_state()`. Skieruj ten efekt uboczny do jednego
# syntetycznego katalogu na CAŁY proces script-runnera. Bez tego wyjątek guarda
# był łapany fail-soft w `_append_jsonl_to()` i test pozostawał pozornie zielony,
# mimo próby zapisu do produkcyjnego courier_match_debug.jsonl.
_PROCESS_TMPDIR = tempfile.TemporaryDirectory(prefix="resolve_cid_score_")
shift_state.MATCH_DEBUG_LOG = Path(_PROCESS_TMPDIR.name) / "courier_match_debug.jsonl"
assert "/root/.openclaw/workspace/dispatch_state/" not in str(shift_state.MATCH_DEBUG_LOG)

passed = 0
failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label} {detail}")


# ============================================================
print("=== #14 GRUPA 1: exact match path ===")
# ============================================================

# Test 1 — exact case-sensitive
kids = {"Bartek O": "123"}
check("1. exact_match_returns_cid",
      worker.resolve_cid("Bartek O", kids) == "123")

# Test 2 — exact full name as alias
kids = {"Adrian Citko": "457"}
check("2. exact_match_full_name",
      worker.resolve_cid("Adrian Citko", kids) == "457")

# ============================================================
print("\n=== #14 GRUPA 2: case-insensitive exact ===")
# ============================================================

# Test 3 — lowercase schedule, mixed-case alias
kids = {"Adrian Citko": "457"}
check("3. case_insensitive_exact_match",
      worker.resolve_cid("adrian citko", kids) == "457")

# Test 4 — uppercase schedule, mixed-case alias
kids = {"Bartek O": "123"}
check("4. mixed_case_returns_cid",
      worker.resolve_cid("BARTEK O", kids) == "123")

# ============================================================
print("\n=== #14 GRUPA 3: score-based fallback (BUG FIX) ===")
# ============================================================

# Test 5 — BUG FIX: Adrian Citko picks Adrian Cit (score=30) > Adrian (1) > Adrian R (0)
kids = {"Adrian": "21", "Adrian R": "400", "Adrian Cit": "457"}
check("5. score_picks_longest_prefix_alias",
      worker.resolve_cid("Adrian Citko", kids) == "457")

# Test 6 — Adrian Czapla: only "Adrian" alias has positive score (1); Adrian Cit "cit" not prefix of "czapla" → 0
kids = {"Adrian": "21", "Adrian Cit": "457"}
check("6. score_picks_short_alias_when_only_match",
      worker.resolve_cid("Adrian Czapla", kids) == "21")

# Test 7 — Adrian R wins for surname starting with R
kids = {"Adrian": "21", "Adrian R": "400", "Adrian Cit": "457"}
check("7. score_picks_R_alias_for_surname_R",
      worker.resolve_cid("Adrian Rogowski", kids) == "400")

# Test 8 — bare first-name fallback (only Krystian alias)
kids = {"Krystian": "61", "Bartek O": "123"}
check("8. score_picks_first_name_when_no_surname_match",
      worker.resolve_cid("Krystian Kowalski", kids) == "61")

# Test 9 — Mateusz Bro (score=30) > Mateusz O (score=10) > Mateusz L (score=10)
kids = {"Mateusz L": "284", "Mateusz Bro": "409", "Mateusz O": "413"}
check("9. score_picks_correct_long_alias",
      worker.resolve_cid("Mateusz Brodowski", kids) == "409")

# ============================================================
print("\n=== #14 GRUPA 4: ambiguity / tie / unmatched ===")
# ============================================================

# Test 10 — tie: schedule "Adrian K" (single-letter surname), 2 aliases obie z surname startujacym K
# "Adrian Ka" a_last="ka", s_last="k", a_last.startswith(s_last) → score=5
# "Adrian Ko" a_last="ko", s_last="k", a_last.startswith(s_last) → score=5
# TIE @ score=5 → return None
kids = {"Adrian Ka": "111", "Adrian Ko": "222"}
check("10. ambiguous_tie_returns_none",
      worker.resolve_cid("Adrian K", kids) is None)

# Test 11 — all-zero (no first-name-only alias, no prefix match)
kids = {"Adrian Cit": "457", "Adrian R": "400"}
check("11. all_zero_score_returns_none",
      worker.resolve_cid("Adrian Czapla", kids) is None)

# Test 12 — nonexistent first name
kids = {"Adrian": "21"}
check("12. nonexistent_first_name_returns_none",
      worker.resolve_cid("Krzysztof Nowak", kids) is None)

# Test 13 — empty kids dict
kids = {}
check("13. empty_kids_returns_none",
      worker.resolve_cid("Adrian Citko", kids) is None)

# Test 14a — empty string
kids = {"Adrian": "21"}
check("14a. empty_full_name_returns_none",
      worker.resolve_cid("", kids) is None)

# Test 14b — None input
check("14b. none_full_name_returns_none",
      worker.resolve_cid(None, kids) is None)

# ============================================================
print("\n=== #14 GRUPA 5: side-effect logging ===")
# ============================================================

# Test 15 — RESOLVE_CID_AMBIGUOUS_RESOLVED logged when score-based fallback picks winner
# ETAP 3 krok 2 (2026-06-10): RESOLVE_CID_* idą do courier_match_debug.jsonl
# (odszumienie learning_log) — testy czytają match_debug_log.
with isolated_shift_state() as paths:
    kids = {"Adrian": "21", "Adrian Cit": "457"}
    cid = worker.resolve_cid("Adrian Citko", kids)
    if not paths.match_debug_log.exists():
        check("15. ambiguous_resolved_logs_event", False,
              detail="match_debug_log file not created")
    else:
        with open(paths.match_debug_log) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        events = [json.loads(ln) for ln in lines]
        resolved = [e for e in events if e.get("event") == "RESOLVE_CID_AMBIGUOUS_RESOLVED"]
        check("15. ambiguous_resolved_logs_event",
              cid == "457"
              and len(resolved) == 1
              and resolved[0].get("winner_cid") == "457"
              and resolved[0].get("full_name") == "Adrian Citko",
              detail=f"cid={cid}, resolved_events={resolved}")

# Test 16 — RESOLVE_CID_AMBIGUOUS_TIE logged on tie (Adrian K → Ka i Ko obie score=5)
with isolated_shift_state() as paths:
    kids = {"Adrian Ka": "111", "Adrian Ko": "222"}
    cid = worker.resolve_cid("Adrian K", kids)
    if not paths.match_debug_log.exists():
        check("16. ambiguous_tie_logs_event", False,
              detail="match_debug_log file not created")
    else:
        with open(paths.match_debug_log) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        events = [json.loads(ln) for ln in lines]
        ties = [e for e in events if e.get("event") == "RESOLVE_CID_AMBIGUOUS_TIE"]
        check("16. ambiguous_tie_logs_event",
              cid is None and len(ties) == 1 and ties[0].get("tied_score") == 5,
              detail=f"cid={cid}, ties={ties}")

# Test 17 — NO logging when single unambiguous winner (only 1 alias with first-name)
with isolated_shift_state() as paths:
    kids = {"Bartek O": "123", "Krystian": "61"}
    cid = worker.resolve_cid("Bartek Ołdziej", kids)
    if paths.match_debug_log.exists():
        with open(paths.match_debug_log) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        events = [json.loads(ln) for ln in lines]
        ambiguous = [e for e in events
                     if e.get("event") in ("RESOLVE_CID_AMBIGUOUS_RESOLVED", "RESOLVE_CID_AMBIGUOUS_TIE")]
        check("17. unambiguous_winner_no_log",
              cid == "123" and len(ambiguous) == 0,
              detail=f"cid={cid}, ambiguous_events={ambiguous}")
    else:
        check("17. unambiguous_winner_no_log", cid == "123", detail=f"cid={cid}")

# ============================================================
print("\n=== #14 GRUPA 6: hermetic kurier_ids regression fixture ===")
# ============================================================

# Test 18 — ten sam znany przypadek, ale bez zależności od produkcyjnego pliku.
# Osobny live-smoke tożsamości zapewnia `python -m dispatch_v2.identity.report --parity`.
fixture_kids = {
    "Adrian": "21",
    "Adrian R": "400",
    "Adrian Cit": "457",
    "Mateusz O": "413",
    "Michał K": "393",
    "Bartek O": "123",
}
expected = {
    "Adrian Citko": "457",      # was 21 (BUG pre-#14), should be 457
    "Adrian Czapla": "21",      # bare "Adrian" alone, score=1
    "Adrian Rogowski": "400",   # "Adrian R" score=10
    "Mateusz Olchowik": "413",  # "Mateusz O" score=10
    "Michał Karpiuk": "393",    # "Michał K" score=10
    "Bartek Ołdziej": "123",    # "Bartek O" score=10
}
all_ok = True
fails = []
for name, exp in expected.items():
    got = worker.resolve_cid(name, fixture_kids)
    if got != exp:
        all_ok = False
        fails.append(f"{name}: got={got} expected={exp}")
check("18. synthetic_kurier_ids_regression",
      all_ok, detail="; ".join(fails) if fails else "")

print("\n" + "=" * 60)
print(f"#14 RESOLVE_CID SCORE-BASED: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"#14 RESOLVE_CID SCORE-BASED: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)

#!/usr/bin/env python3
"""C-FLAG-EFFECT (2026-06-25): coverage TESTÓW EFEKTU flag decyzyjnych ETAP4.

Trzeci checker higieny flag, KOMPLEMENT do:
  - C-ORPHAN (flag_hygiene_check): klucz flags.json, którego NIKT nie czyta w kodzie;
  - C-FLAG-DRIFT (flag_doc_coverage_check): flaga czytana, ale nieudokumentowana w ref.
TU: flaga z ETAP4_DECISION_FLAGS, której ŻADEN test nie dotyka (nie toggluje / nie
asertuje) → ryzyko „flaga zadeklarowana i wpięta, ale jej EFEKT nigdy nie sprawdzony".
Dokładnie ta klasa przepuściła ENABLE_BEST_EFFORT_OBJM_R6_KEY (wpięta, 0 testów efektu).

Bar realistyczny → RATCHET (jak C-FLAG-DRIFT): baseline zamraża dziś-nieprzetestowane,
checker pada TYLKO gdy NOWA flaga decyzyjna pojawi się bez testu poza baseline. Nowa
flaga ⇒ dodaj test, który toggluje ją ON↔OFF i asertuje zmianę decyzji (verdict/best/
reason), ALBO świadomie dopisz do baseline (kurczyć listę = cel długofalowy).

Proxy „ma test efektu" = nazwa flagi występuje w tekście tests/*.py (test ją toggluje
lub asertuje). Słabsze niż „udowodnij flip", ale spójne z barem C-FLAG-DRIFT (`k in ref`)
i łapie realny przypadek „0 testów dotyka flagi". Zakres = ETAP4_DECISION_FLAGS (kanoniczny
rejestr flag decyzyjnych cross-proces). Flagi decyzyjne POZA ETAP4 = osobny dług rejestru.

Użycie: python3 tools/flag_effect_coverage_check.py   (exit 0 = brak nowej luki, 1 = NOWA)
"""
import json
import os
import sys

# C12(e) self-lokalizacja (2026-07-08, A2): domyślnie KANON (CI/kanoniczny bieg
# bez zmian), ale nadpisywalne ZIOMEK_SCRIPTS_ROOT dla walidacji kodu z WORKTREE
# przed merge — inaczej checker skanuje KANON `tests/` (bez nowego testu efektu z
# worktree) i fałszywie zgłasza nową flagę jako „bez testu". Spójne z conftest.
SCRIPTS = os.environ.get("ZIOMEK_SCRIPTS_ROOT", "/root/.openclaw/workspace/scripts")
TESTS = os.path.join(SCRIPTS, "dispatch_v2", "tests")
BASELINE = os.path.join(SCRIPTS, "dispatch_v2", "tools", "flag_effect_baseline.json")


def _etap4_flags():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    from dispatch_v2 import common as C
    return list(C.ETAP4_DECISION_FLAGS)


def _tests_text():
    buf = []
    for root, _dirs, files in os.walk(TESTS):
        for fn in files:
            if fn.endswith(".py"):
                try:
                    buf.append(open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read())
                except Exception:
                    pass
    return "\n".join(buf)


def compute():
    """Zwraca dict: tested/untested/new_gap/stale_baseline/coverage_pct."""
    flags = _etap4_flags()
    txt = _tests_text()
    base = set(json.load(open(BASELINE)).get("baseline", []))

    tested = [k for k in flags if k in txt]
    untested = {k for k in flags if k not in txt}

    new_gap = sorted(untested - base)                 # NOWA flaga bez testu (FAIL)
    stale_baseline = sorted(b for b in base            # baseline-entry już z testem / poza ETAP4
                            if b not in flags or b in txt)
    pct = round(100 * len(tested) / max(1, len(flags)), 1)
    return {
        "total_decision_flags": len(flags),
        "tested": sorted(tested),
        "untested": sorted(untested),
        "new_gap": new_gap,
        "stale_baseline": stale_baseline,
        "coverage_pct": pct,
    }


def main():
    r = compute()
    print(f"flagi decyzyjne (ETAP4):  {r['total_decision_flags']}")
    print(f"z testem efektu:          {len(r['tested'])} ({r['coverage_pct']}%)")
    print(f"bez testu (total):        {len(r['untested'])}")
    print(f"baseline (świadomy dług): {r['total_decision_flags'] - len(r['tested']) - len(r['new_gap'])}")
    if r["stale_baseline"]:
        print(f"\n⚠ baseline do sprzątnięcia ({len(r['stale_baseline'])}) — "
              f"zniknęły z ETAP4 lub już mają test (usuń z baseline):")
        for k in r["stale_baseline"]:
            print(f"   {k}")
    if r["new_gap"]:
        print(f"\n❌ NOWA LUKA — {len(r['new_gap'])} flaga(i) decyzyjna bez testu efektu "
              f"i poza baseline:")
        for k in r["new_gap"]:
            print(f"   {k}")
        print("\n→ dodaj test togglujący flagę ON↔OFF z asercją zmiany decyzji ALBO "
              "dopisz świadomie do tools/flag_effect_baseline.json.")
        return 1
    print("\n✅ brak nowej luki testów efektu flag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

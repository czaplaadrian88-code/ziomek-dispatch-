#!/usr/bin/env python3
"""C-FLAG-DRIFT (audyt 2026-06-24 §6.C): coverage dokumentacji flag decyzyjnych.
KOMPLEMENT do flag_hygiene_check (C-ORPHAN: klucz, którego NIKT nie czyta). TU:
flaga decyzyjna ENABLE_/USE_, którą kod CZYTA, ale której NIE ma w
ZIOMEK_LOGIC_REFERENCE.md → DRYFT dokumentacji (audyt: ref↔żywe się rozjeżdżały).

Bar realistyczny: ref to doc LOGIKI, nie rejestr flag → 21% kluczy w nim (shadow/
guard/alert/scalar tam nie należą). Dlatego RATCHET, nie „100% coverage": baseline
zamraża dziś-niedokumentowane (70), a checker pada TYLKO gdy pojawi się NOWA
niedokumentowana flaga poza baseline. Nowa flaga ⇒ udokumentuj w ref ALBO świadomie
dopisz do baseline (kurczyć listę = cel długofalowy).

Użycie: python3 tools/flag_doc_coverage_check.py   (exit 0 = brak driftu, 1 = NOWY drift)
"""
import json
import os
import sys

SCRIPTS = os.environ.get(
    "ZIOMEK_SCRIPTS_ROOT", "/root/.openclaw/workspace/scripts")
FLAGS = os.path.join(SCRIPTS, "flags.json")
REF = os.path.join(SCRIPTS, "dispatch_v2", "ZIOMEK_LOGIC_REFERENCE.md")
BASELINE = os.path.join(SCRIPTS, "dispatch_v2", "tools", "flag_doc_baseline.json")


def _decision_keys(flags):
    return [k for k in flags
            if (k.startswith("ENABLE_") or k.startswith("USE_"))]


def compute():
    """Zwraca dict: documented/undocumented/new_drift/stale_baseline/coverage_pct."""
    flags = json.load(open(FLAGS))
    ref = open(REF, encoding="utf-8").read()
    base = set(json.load(open(BASELINE)).get("baseline", []))

    keys = _decision_keys(flags)
    undocumented = {k for k in keys if k not in ref}
    documented = [k for k in keys if k in ref]

    new_drift = sorted(undocumented - base)         # nowa niedok. flaga (FAIL)
    # baseline-entry która zniknęła z flags.json LUB jest już w ref → do sprzątnięcia
    stale_baseline = sorted(b for b in base
                            if b not in flags or b in ref)
    pct = round(100 * len(documented) / max(1, len(keys)), 1)
    return {
        "total_decision_keys": len(keys),
        "documented": sorted(documented),
        "undocumented": sorted(undocumented),
        "new_drift": new_drift,
        "stale_baseline": stale_baseline,
        "coverage_pct": pct,
    }


def main():
    r = compute()
    print(f"flagi decyzyjne ENABLE_/USE_: {r['total_decision_keys']}")
    print(f"udokumentowane w ref:         {len(r['documented'])} ({r['coverage_pct']}%)")
    print(f"niedokumentowane (total):     {len(r['undocumented'])}")
    print(f"baseline (świadomy dług):     {r['total_decision_keys'] - len(r['documented']) - len(r['new_drift'])}")
    if r["stale_baseline"]:
        print(f"\n⚠ baseline do sprzątnięcia ({len(r['stale_baseline'])}) — "
              f"zniknęły z flags.json lub już udokumentowane (usuń z baseline):")
        for k in r["stale_baseline"]:
            print(f"   {k}")
    if r["new_drift"]:
        print(f"\n❌ NOWY DRYFT — {len(r['new_drift'])} flaga(i) decyzyjna niedokumentowana "
              f"i poza baseline:")
        for k in r["new_drift"]:
            print(f"   {k}")
        print("\n→ udokumentuj w ZIOMEK_LOGIC_REFERENCE.md ALBO dopisz do "
              "tools/flag_doc_baseline.json (świadomy dług).")
        return 1
    print("\n✅ brak nowego driftu dokumentacji flag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

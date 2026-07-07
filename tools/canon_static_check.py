#!/usr/bin/env python3
"""canon_static_check — A1 STRAŻNIK (VETO): statyczny check stałych KANONU.

Pinuje w CI wartości, których NIKT nie zmienia bez jawnego ACK Adriana
(ZIOMEK_REGULY_KANON): R6=35 (40 wyłącznie eskalacja ALARM), R27 ±5,
capy worka (tier + sanity), dial O2 — oraz RATCHETY pojedynczego źródła:
 - `BAG_TIME_HARD_MAX_MIN =` wolno definiować TYLKO w common.py (forward-guard
   mode-consistency: przyszły mode-layer W1 relaksuje przez usankcjonowany
   dial, nie przez DRUGĄ stałą — wzorzec #2 „fix w 1 z N bliźniaczych");
 - `_apply_canon_order_invariants` definiowane TYLKO w plan_recheck.py
   (ratchet 4 kopii kanonu trasy: kopie konsola/apka są w INNYCH repo,
   w tym repo nie wolno dorobić piątej).

Check jest STATYCZNY (AST + regex na źródłach; zero importu silnika, zero
side-effectów) → działa na systemowym python3 i w każdym worktree.

Użycie:
  python3 tools/canon_static_check.py            # raport ≤10 linii, exit 0/1
  python3 tools/canon_static_check.py --selftest # mutation-probes (C13/C14):
        każda wstrzyknięta mutacja kanonu MUSI być wykryta (in-memory,
        pliki NIETKNIĘTE); przeżywająca sonda = VOID strażnika, exit 1.
  python3 tools/canon_static_check.py --json     # wynik maszynowy

Wpięcie CI: tests/test_canon_static_check_a1.py (pełna regresja pytest).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
ROOT = _THIS.parents[1]  # …/dispatch_v2 (kanon LUB worktree — samo-lokalizacja C12e)

# Katalogi/pliki poza „silnikiem" dla ratchetów (testy/narzędzia/archiwa).
# `.claude` = worktree'y sąsiednich sesji (ADR-007, `.claude/worktrees/agent-*`):
# ich kopie common.py/plan_recheck.py to NIE druga definicja kanonu w silniku —
# bez tego skanu checker liczyłby je jako nielegalny bliźniak (fałszywy VETO).
_EXCLUDE_DIRS = {"tests", "tools", "eod_drafts", "docs", "__pycache__",
                 "dispatch_state", "venv", ".git", ".claude"}


# ── kanon: oczekiwane wartości ────────────────────────────────────────
# (name, plik, rodzaj, oczekiwana wartość, opis-kanonu)
#  rodzaj: "literal"  → NAME = <stała>
#          "env"      → NAME = cast(os.environ.get("NAME", "<default>"))
#          "dict"     → NAME = {литерały}
CANON_DIALS = [
    ("BAG_TIME_HARD_MAX_MIN", "common.py", "literal", 35,
     "R6 termiczna = PŁASKIE 35 min (doktryna 2026-05-10)"),
    ("V3274_FROZEN_PICKUP_WINDOW_MIN", "common.py", "literal", 5.0,
     "R27: okno committed pickup ±5 min"),
    ("OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN", "common.py", "literal", 5.0,
     "R27: tolerancja strict ±5 min (obiektyw)"),
    ("BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", "common.py", "env", "40",
     "40 = WYŁĄCZNIE cap eskalacji ratunkowej (ALARM), nie cel świeżości"),
    ("O2_OVERAGE_CAP_MIN", "common.py", "env", "35",
     "dial O2 = 35 (parytet instrument↔silnik, test_overage_cap_equals_engine_dial)"),
    ("MAX_BAG_SANITY_CAP", "common.py", "env", "8",
     "sanity cap worka = 8"),
    ("HARD_TIER_BAG_CAP", "common.py", "dict",
     {"gold": 6, "std+": 6, "std": 5, "slow": 4, "new": 4},
     "capy worka per KLASA kuriera (tier=klasa, nie eskalacja)"),
]

# (regex-def, dozwolony plik, opis)
SINGLE_SOURCE_RATCHETS = [
    (re.compile(r"^\s*BAG_TIME_HARD_MAX_MIN\s*=", re.M), {"common.py"},
     "R6=35 ma JEDNO źródło (common.py); druga definicja = mode-consistency VETO"),
    (re.compile(r"^\s*def\s+_apply_canon_order_invariants\b", re.M), {"plan_recheck.py"},
     "kanon kolejności trasy: jedna definicja w silniku (plan_recheck)"),
]


def load_sources(root: Path | None = None) -> dict[str, str]:
    """{ścieżka-względna: źródło} dla plików silnika (top-level + core/)."""
    root = root or ROOT
    out = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        if ".bak" in p.name or p.name.startswith("."):
            continue
        try:
            out[str(rel)] = p.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


# ── ekstrakcja wartości z AST ─────────────────────────────────────────

def _extract_assign(tree: ast.Module, name: str):
    """Ostatnie top-level przypisanie `name = …` → węzeł wartości."""
    node = None
    for st in tree.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    node = st.value
    return node


def _literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _env_default(node):
    """`cast(os.environ.get("NAME", "<def>"))` → "<def>" (string literal)."""
    # zdejmij cast float()/int()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("float", "int", "str") and node.args:
        node = node.args[0]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "get" and len(node.args) >= 2:
        return _literal(node.args[1])
    return None


def check_dials(sources: dict[str, str]) -> list[str]:
    viol = []
    trees = {}
    for name, fname, kind, expected, desc in CANON_DIALS:
        src = sources.get(fname)
        if src is None:
            viol.append(f"{fname}: BRAK PLIKU (dial {name})")
            continue
        if fname not in trees:
            try:
                trees[fname] = ast.parse(src)
            except SyntaxError as e:
                viol.append(f"{fname}: SyntaxError ({e}) — check niemożliwy")
                trees[fname] = None
        tree = trees[fname]
        if tree is None:
            continue
        node = _extract_assign(tree, name)
        if node is None:
            viol.append(f"{name}: brak definicji w {fname} — {desc}")
            continue
        got = _env_default(node) if kind == "env" else _literal(node)
        if got != expected:
            viol.append(f"{name}={got!r} ≠ kanon {expected!r} [{fname}] — {desc}")
    return viol


def check_ratchets(sources: dict[str, str]) -> list[str]:
    viol = []
    for rx, allowed, desc in SINGLE_SOURCE_RATCHETS:
        hits = [rel for rel, src in sources.items() if rx.search(src)]
        extra = [h for h in hits if h not in allowed]
        missing = [a for a in allowed if a not in hits]
        if extra:
            viol.append(f"RATCHET: {desc} — nielegalne definicje w: {', '.join(extra)}")
        if missing:
            viol.append(f"RATCHET: {desc} — brak definicji w: {', '.join(missing)}")
    return viol


def run_checks(sources: dict[str, str] | None = None) -> list[str]:
    sources = sources if sources is not None else load_sources()
    return check_dials(sources) + check_ratchets(sources)


# ── mutation-probes (C13/C14: strażnik bez zabitej sondy = VOID) ─────

def _mutations(sources: dict[str, str]) -> list[tuple[str, dict[str, str]]]:
    """Lista (nazwa-sondy, zmutowane źródła). Mutacje IN-MEMORY — dysk nietknięty.
    Każda reprezentuje realną klasę naruszenia kanonu."""
    muts = []

    def sub(fname, old, new, label):
        src = sources.get(fname, "")
        if old not in src:
            raise AssertionError(f"sonda {label}: wzorzec nie występuje w {fname} "
                                 f"(kanon się przesunął — zaktualizuj sondę)")
        m = dict(sources)
        m[fname] = src.replace(old, new, 1)
        muts.append((label, m))

    sub("common.py", "BAG_TIME_HARD_MAX_MIN = 35", "BAG_TIME_HARD_MAX_MIN = 40",
        "r6_35_to_40")
    sub("common.py", "V3274_FROZEN_PICKUP_WINDOW_MIN = 5.0",
        "V3274_FROZEN_PICKUP_WINDOW_MIN = 7.0", "r27_5_to_7")
    sub("common.py", "OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN = 5.0",
        "OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN = 10.0", "r27_strict_5_to_10")
    sub("common.py", '"BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", "40"',
        '"BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", "45"', "escalation_40_to_45")
    sub("common.py", '"O2_OVERAGE_CAP_MIN", "35"',
        '"O2_OVERAGE_CAP_MIN", "40"', "o2_dial_35_to_40")
    sub("common.py", '"MAX_BAG_SANITY_CAP", "8"',
        '"MAX_BAG_SANITY_CAP", "12"', "sanity_cap_8_to_12")
    sub("common.py", '"gold": 6', '"gold": 9', "tier_cap_gold_6_to_9")
    # ratchet: druga definicja stałej R6 w innym pliku silnika
    m = dict(sources)
    victim = next((f for f in ("dispatch_pipeline.py", "feasibility_v2.py")
                   if f in m), None)
    if victim:
        m[victim] = m[victim] + "\nBAG_TIME_HARD_MAX_MIN = 40\n"
        muts.append(("second_r6_source", m))
    # ratchet: piąta kopia kanonu trasy
    m2 = dict(sources)
    if victim:
        m2[victim] = m2[victim] + "\ndef _apply_canon_order_invariants(x):\n    return x\n"
        muts.append(("fifth_route_canon_copy", m2))
    # kasacja definicji dialu (usunięcie linii = też naruszenie)
    m3 = dict(sources)
    m3["common.py"] = m3["common.py"].replace(
        "BAG_TIME_HARD_MAX_MIN = 35", "_A1_PROBE_REMOVED = 0", 1)
    muts.append(("r6_definition_removed", m3))
    return muts


def selftest() -> dict:
    """0 false-positive na czystym repo + 100% wykrycia wstrzykniętych naruszeń."""
    sources = load_sources()
    clean = run_checks(sources)
    results = {"clean_violations": clean, "probes": {}}
    for label, mutated in _mutations(sources):
        v = run_checks(mutated)
        results["probes"][label] = {"killed": bool(v), "violations": v[:2]}
    results["all_killed"] = all(p["killed"] for p in results["probes"].values())
    results["ok"] = results["all_killed"] and not clean
    return results


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        r = selftest()
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print(f"czyste repo: {'0 naruszeń ✓' if not r['clean_violations'] else r['clean_violations']}")
            for lbl, p in r["probes"].items():
                print(f"  sonda {lbl:28} {'KILLED ✓' if p['killed'] else 'SURVIVED ✗ (VOID!)'}")
            print(f"WERDYKT: {'OK — strażnik uzbrojony' if r['ok'] else 'VOID — napraw strażnika'}")
        return 0 if r["ok"] else 1

    viol = run_checks()
    if a.json:
        print(json.dumps({"violations": viol}, ensure_ascii=False, indent=1))
    elif viol:
        print(f"⛔ A1 VETO — naruszenia kanonu ({len(viol)}):")
        for v in viol[:9]:
            print(f"  • {v}")
    else:
        print("✓ kanon nienaruszony (R6=35, R27=±5, eskalacja=40, capy, ratchety)")
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())

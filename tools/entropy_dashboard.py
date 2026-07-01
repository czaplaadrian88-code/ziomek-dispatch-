#!/usr/bin/env python3
"""entropy_dashboard — STOJĄCY miernik zdrowia Ziomka (8 metryk kanonicznych z FAZA1_04).

DRAFT 2026-07-01. Read-only. Odpalaj po KAŻDEJ naprawie fundamentu — liczby MAJĄ MALEĆ do 0/1.
Miejsce docelowe po ACK: dispatch_v2/tools/entropy_dashboard.py.

UCZCIWOŚĆ (reguła C9/C11 — miernik też nie może kłamać). Tag jakości per metryka:
  [AUTO]          policzone tu (AST / istniejący checker) — ground-truth-ish.
  [AUTO-oracle]   policzone tu + zweryfikowane ręcznym oracle (sentinele 30.06).
  [AUDIT-BASELINE] liczba z audytu Fazy 1 (wymaga metody audytu: oracle-lane / graf-konfliktów /
                  ledger bliźniaków) — NIE auto-liczalne prostym AST; re-measure = re-run odpowiedniego
                  narzędzia z FAZA1 (patrz 'jak'). Wpisane jako baseline, żeby trend był widoczny.
"""
from __future__ import annotations
import os, re, ast, subprocess, sys

ROOT = "/root/.openclaw/workspace/scripts/dispatch_v2"

# ---------- helpers ----------
def live_engine_py():
    out = []
    for dp, _, fns in os.walk(ROOT):
        if any(x in dp for x in ("/tests", "/.git", "/eod_drafts")):
            continue
        for fn in fns:
            if fn.endswith(".py") and not fn.startswith("test_") and ".bak" not in fn:
                out.append(os.path.join(dp, fn))
    return out

def parse(f):
    try:
        t = ast.parse(open(f, encoding="utf-8", errors="ignore").read())
        for n in ast.walk(t):
            for c in ast.iter_child_nodes(n):
                c.parent = n
        return t
    except Exception:
        return None

DEFENSE_FN = re.compile(r"guard|saniti|sanit|rescue|clean|poison|fallback|_assert|validate|reconcile|last_known|blocked|coord_poison|bbox", re.I)
POS_RE = re.compile(r"pos|coord|pickup|deliv|\blat\b|\blng\b|\bloc\b|_c$|_ll$|geo|naive_pos|start_pos", re.I)

def is_zero_pair(n):
    if isinstance(n, (ast.Tuple, ast.List)) and len(n.elts) == 2:
        z = lambda e: (isinstance(e, ast.Constant) and isinstance(e.value, (int, float))
                       and not isinstance(e.value, bool) and e.value == 0)
        return z(n.elts[0]) and z(n.elts[1])
    return False

def is_sent(n):
    return (isinstance(n, ast.Name) and n.id == "BIALYSTOK_CENTER") or is_zero_pair(n)

def binding_name(anc):
    for p in anc:
        if isinstance(p, ast.Assign):
            names = []
            for t in p.targets:
                if isinstance(t, ast.Name): names.append(t.id)
                elif isinstance(t, ast.Attribute): names.append(t.attr)
                elif isinstance(t, ast.Tuple):
                    for e in t.elts: names.append(getattr(e, "id", getattr(e, "attr", "")))
            return " ".join(n for n in names if n)
        if isinstance(p, ast.keyword) and p.arg: return p.arg
        if isinstance(p, ast.AnnAssign) and isinstance(p.target, ast.Name): return p.target.id
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)): return None
    return None

def sentinel_poison(files):
    poison, instrument = [], []
    for f in files:
        t = parse(f)
        if not t: continue
        is_tool = os.sep + "tools" + os.sep in f
        for n in ast.walk(t):
            if not is_sent(n): continue
            anc = []; cur = getattr(n, "parent", None)
            while cur is not None: anc.append(cur); cur = getattr(cur, "parent", None)
            if isinstance(n, ast.Name) and n.id == "BIALYSTOK_CENTER" and anc and isinstance(anc[0], ast.Assign) \
               and any(isinstance(tt, ast.Name) and tt.id == "BIALYSTOK_CENTER" for tt in anc[0].targets):
                continue  # stała
            if any(isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)) and DEFENSE_FN.search(p.name) for p in anc) \
               or any(isinstance(p, ast.Compare) for p in anc[:3]):
                continue  # obrona
            if isinstance(n, ast.Name):
                pass
            else:
                bn = binding_name(anc)
                if not (bn and POS_RE.search(bn)): continue  # fałszywka
            (instrument if is_tool else poison).append((os.path.relpath(f, ROOT), n.lineno))
    return poison, instrument

def flag_divergences():
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "flag_registry.py")],
                           capture_output=True, text=True, cwd=os.path.dirname(ROOT), timeout=120)
        txt = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"ROZJAZDY\s*\((\d+)\)", txt)
        return int(m.group(1)) if m else None
    except Exception:
        return None

# ---------- 8 metryk ----------
def main():
    files = live_engine_py()
    poison, instrument = sentinel_poison(files)
    div = flag_divergences()

    rows = [
        # (nr, nazwa, DZIŚ, cel, tag, jak-re-measure)
        ("1", "copy-count (reguł >1 źródło)", "17 (≈90 inst.)", "0",
         "AUDIT-BASELINE", "kuratorowany rejestr bliźniaków (ARCHITECTURE §4); auto-licznik NIE działa (kopie semantyczne)"),
        ("2", "twin-divergence (bliźniaki DIVERGED)", "~13 (route 44-75/d)", "0",
         "AUDIT-BASELINE", "monitor ziomek_time_route_monitor.jsonl (⚠ wygasa 07-10) → golden-test"),
        ("3", "void-instrument (przyrząd kłamie)", "19 VOID + 6 UNTESTED = 25/49", "0",
         "AUDIT-BASELINE", "lane runtime-oracle (FAZA1_03) — odpal przyrząd vs 2. metoda"),
        ("4", "dead-flag / rozjazdy flag", f"{div if div is not None else '5 (+112 poza rej.)'}", "0",
         "AUTO" if div is not None else "AUDIT-BASELINE", "tools/flag_registry.py (rozjazdy env↔drop-in↔flags.json)"),
        ("5", "layer-violation (HARD w złej warstwie)", "7", "0",
         "AUDIT-BASELINE", "FAZA1_01/B05 — geometria SOFT-only, FEAS_CARRY bypass"),
        ("6", "unresolved-conflict (precedencja)", "13 klastrów (64 par)", "0",
         "AUDIT-BASELINE", "graf konfliktów FAZA1_02"),
        ("7", "sentinel-as-data (trucizna pozycji)", f"{len(poison)} żywy silnik (+{len(instrument)} instr.)", "0 (1 walidator/ingest)",
         "AUTO-oracle", "AST tu (bool wykluczony + kontekst pozycji); 6/12=courier_resolver=most K5"),
        ("8", "threshold-sprawl (próg w N miejscach)", "10 rodzin (≈40 sites)", "0",
         "AUDIT-BASELINE", "R6=35/40 ×6, czasówka=60 ×6 → nazwana stała"),
    ]

    print("=" * 96)
    print("ZIOMEK ENTROPY DASHBOARD (8 metryk kanonicznych — FAZA1_04) | DRAFT 2026-07-01 | read-only")
    print(f"pliki żywego silnika: {len(files)}")
    print("=" * 96)
    print(f"{'#':<2} {'METRYKA':<40} {'DZIŚ':<26} {'CEL':<20} TAG")
    print("-" * 96)
    for nr, name, today, cel, tag, how in rows:
        print(f"{nr:<2} {name:<40} {today:<26} {cel:<20} [{tag}]")
    print("-" * 96)
    print("\nSZCZEGÓŁ AUTO:")
    print(f"  #4 flag-rozjazdy [AUTO]: {div if div is not None else 'N/D (odpal flag_registry.py ręcznie)'}")
    print(f"  #7 sentinel-trucizna żywy silnik [AUTO-oracle]: {len(poison)}")
    for f, ln in sorted(poison):
        print(f"       · {f}:{ln}")
    print(f"  #7 instrument/harness (osobno): {len(instrument)}")
    print("\nRE-MEASURE metryk [AUDIT-BASELINE] = odpal narzędzie z kolumny 'jak' (patrz kod). "
          "\nLiczby [AUDIT-BASELINE] pochodzą z Fazy 1 (2026-06-30) — aktualizuj po każdej fali fundamentu.")
    print("\nZASADA: wszystkie 8 mają MALEĆ do 0/1. Metryka bez ruchu w dobrą stronę po fali = brak progresu.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""flag_registry — inwentarz flag z prowieniencją (F3, audyt 03.06 „effective_flags").

Flagi żyją w 3 miejscach i łatwo o rozjazd (incydent ETAP4: czasówka liczyła
innym silnikiem niż shadow przez env-only flagi):
  1. common.py — definicja + default (env-overridable przy imporcie),
  2. env unitów systemd (dispatch-*.service + drop-iny *.conf) — wartość
     ZAMROŻONA per proces przy starcie,
  3. flags.json — kanon hot-reload dla flag decyzyjnych (ETAP4_DECISION_FLAGS
     + FLAGS_JSON_NUMERIC_OVERRIDES czytane przez decision_flag/load_flags).

Tool READ-ONLY: skanuje wszystkie trzy źródła i wypisuje per flaga efektywną
wartość per proces + prowieniencję + WYKRYTE ROZJAZDY:
  - env-frozen flaga ustawiona w CZĘŚCI unitów silnika (cross-proces divergence),
  - klucz flags.json przykrywający env (env martwy dla flag decyzyjnych),
  - klucz flags.json bez definicji w common.py (literówka / sierota).

Użycie:
  python3 -m dispatch_v2.tools.flag_registry            # tabela rozjazdów + statystyki
  python3 -m dispatch_v2.tools.flag_registry --all      # pełny inwentarz
  python3 -m dispatch_v2.tools.flag_registry --md PLIK  # raport markdown
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH_V2 = os.path.dirname(_HERE)
COMMON_PY = os.path.join(DISPATCH_V2, "common.py")
FLAGS_JSON = "/root/.openclaw/workspace/scripts/flags.json"
SYSTEMD_DIR = "/etc/systemd/system"

# Unity liczące silnikiem dispatch_v2 (cross-proces spójność = wymóg ETAP4).
ENGINE_UNITS = (
    "dispatch-shadow.service",
    "dispatch-panel-watcher.service",
    "dispatch-czasowka.service",
    "dispatch-plan-recheck.service",
    "dispatch-telegram.service",
)

_DEF_RE = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?:float\(|int\()?_os\.environ\.get\(\s*'
    r'"(?P<env>[A-Z0-9_]+)"\s*,\s*"(?P<default>[^"]*)"\s*\)\s*\)?'
    r'(?P<cmp>\s*==\s*"1")?', re.M)


def scan_common(path: str = COMMON_PY) -> dict:
    """Definicje env-overridable z common.py: {nazwa: {default, bool}}."""
    out = {}
    src = open(path, encoding="utf-8").read()
    for m in _DEF_RE.finditer(src):
        name = m.group("name")
        raw = m.group("default")
        is_bool = bool(m.group("cmp"))
        out[name] = {
            "default": (raw == "1") if is_bool else raw,
            "bool": is_bool,
        }
    return out


def scan_decision_lists(path: str = COMMON_PY) -> tuple:
    """ETAP4_DECISION_FLAGS + FLAGS_JSON_NUMERIC_OVERRIDES bez importu modułu
    (tool ma działać też na drzewie bez venv silnika)."""
    src = open(path, encoding="utf-8").read()

    def _tuple_items(varname):
        m = re.search(varname + r"\s*=\s*\((.*?)\)", src, re.S)
        if not m:
            return ()
        return tuple(re.findall(r'"([A-Z0-9_]+)"', m.group(1)))

    return (_tuple_items("ETAP4_DECISION_FLAGS")
            + _tuple_items("_FINGERPRINT_EXTRA_FLAGS"),
            _tuple_items("FLAGS_JSON_NUMERIC_OVERRIDES"))


def scan_unit_env(unit: str) -> dict:
    """Environment= z main unitu + wszystkich drop-inów *.conf (jak systemd:
    ostatnia definicja wygrywa, drop-iny po main unicie)."""
    env = {}
    files = []
    main = os.path.join(SYSTEMD_DIR, unit)
    if os.path.exists(main):
        files.append(main)
    files += sorted(glob.glob(os.path.join(SYSTEMD_DIR, unit + ".d", "*.conf")))
    for f in files:
        try:
            for line in open(f, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line.startswith("Environment="):
                    continue
                body = line[len("Environment="):].strip().strip('"')
                if "=" in body:
                    k, v = body.split("=", 1)
                    env[k.strip()] = (v.strip(), os.path.basename(f))
        except OSError:
            continue
    return env


def load_flags_json(path: str = FLAGS_JSON) -> dict:
    try:
        return {k: v for k, v in json.load(open(path)).items()
                if not k.startswith("_comment")}
    except Exception:
        return {}


# Rodziny kluczy budowanych DYNAMICZNIE (f-string) — token-scan ich nie widzi.
# Przykład: evaluator.py f"CZASOWKA_T{trigger_min}_ENABLED".
DYNAMIC_KEY_FAMILIES = (re.compile(r"^CZASOWKA_T\d+_ENABLED$"),)

# Flagi celowo per-proces (kategorie (b) telemetria / (c) per-proces z ETAP4;
# pełna tabela + ACK: eod_drafts/2026-06-10/flag_inventory_etap4.md).
INTENTIONAL_PER_PROCESS = {
    "ENABLE_PANEL_BG_REFRESH",  # shadow=1 / watcher=0 ZAMIERZONE (własny cykl loginu)
    "ENABLE_LGBM_SHADOW", "ENABLE_LGBM_METRICS_READ", "ENABLE_PENDING_POOL",
    "ENABLE_OBJ_REPLAY_CAPTURE", "ENABLE_LOADAWARE_SELECTION_SHADOW",
    "PYTHONPATH",  # infra, nie flaga
}


def scan_code_tokens(roots=(DISPATCH_V2, os.path.dirname(DISPATCH_V2))) -> set:
    """Wszystkie tokeny-identyfikatory w *.py (dispatch_v2 + cały scripts/,
    poza eod_drafts/__pycache__/.bak) — do wykrywania SIEROT w flags.json.
    Konsumpcja przez C.flag('NAZWA')/load_flags()['NAZWA'] też się łapie,
    bo nazwa występuje w źródle jako literal."""
    tokens = set()
    seen_files = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
            real = os.path.realpath(path)
            if real in seen_files:
                continue
            seen_files.add(real)
            if "eod_drafts" in path or "__pycache__" in path or ".bak" in path:
                continue
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            tokens.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", src))
    return tokens


def build_registry():
    defs = scan_common()
    decision, numeric = scan_decision_lists()
    fjson = load_flags_json()
    unit_env = {u: scan_unit_env(u) for u in ENGINE_UNITS}
    code_tokens = scan_code_tokens()

    names = sorted(set(defs) | set(fjson)
                   | {k for env in unit_env.values() for k in env})
    rows, issues = [], []
    for n in names:
        d = defs.get(n)
        in_fjson = n in fjson
        envs = {u: unit_env[u][n] for u in ENGINE_UNITS if n in unit_env[u]}
        is_decision = n in decision or n in numeric
        if is_decision and in_fjson:
            source, effective = "flags.json (kanon hot-reload)", fjson[n]
        elif is_decision:
            source = "common.py default (brak klucza flags.json)"
            effective = d["default"] if d else None
        elif envs:
            vals = {u: v for u, (v, _f) in envs.items()}
            source, effective = "env unitów (zamrożone przy starcie)", vals
        else:
            source = "common.py default"
            effective = d["default"] if d else None
        rows.append({"flag": n, "defined": bool(d),
                     "default": (d or {}).get("default"),
                     "decision": is_decision, "flags_json": fjson.get(n) if in_fjson else None,
                     "env": {u: f"{v} ({f})" for u, (v, f) in envs.items()},
                     "source": source, "effective": effective})

        # Rozjazdy
        intentional = n in INTENTIONAL_PER_PROCESS
        if envs and not is_decision and set(envs) != set(ENGINE_UNITS) and not intentional:
            only = ", ".join(sorted(envs))
            issues.append(f"⚠ {n}: env-frozen tylko w [{only}] — pozostałe unity "
                          f"silnika liczą defaultem ({(d or {}).get('default')!r}). "
                          f"Zweryfikuj zamiar: domenowe per-proces OK (inwentarz "
                          f"ETAP4), flaga SILNIKA = rozjazd klasy Z-04.")
        if envs and is_decision and in_fjson:
            issues.append(f"⚠ {n}: klucz flags.json PRZYKRYWA env w "
                          f"[{', '.join(sorted(envs))}] — env martwy, usunąć z unitu.")
        if (in_fjson and n not in code_tokens
                and not any(p.match(n) for p in DYNAMIC_KEY_FAMILIES)):
            issues.append(f"⚠ {n}: SIEROTA — klucz w flags.json bez ŻADNEGO "
                          f"wystąpienia w kodzie *.py (literówka albo martwy klucz; "
                          f"klucze dynamiczne f-stringiem sprawdź ręcznie).")
        if envs and not intentional:
            uniq = {v for v, _f in envs.values()}
            if len(uniq) > 1:
                issues.append(f"⛔ {n}: RÓŻNE wartości env między unitami: "
                              + ", ".join(f"{u}={v}" for u, (v, _f) in sorted(envs.items())))
    return rows, issues


def render(rows, issues, show_all=False):
    lines = []
    lines.append(f"FLAG REGISTRY — {len(rows)} flag "
                 f"(decyzyjne: {sum(1 for r in rows if r['decision'])}, "
                 f"w flags.json: {sum(1 for r in rows if r['flags_json'] is not None)}, "
                 f"env-frozen gdziekolwiek: {sum(1 for r in rows if r['env'])})")
    lines.append("")
    lines.append(f"ROZJAZDY ({len(issues)}):")
    lines.extend("  " + i for i in issues) if issues else lines.append("  (brak)")
    if show_all:
        lines.append("")
        for r in rows:
            lines.append(f"- {r['flag']}: efektywnie={r['effective']!r} "
                         f"[{r['source']}] default={r['default']!r} "
                         f"flags.json={r['flags_json']!r} env={r['env'] or '—'}")
    return "\n".join(lines)


def render_md(rows, issues):
    out = ["# Rejestr flag (F3) — wygenerowany tools/flag_registry.py", ""]
    out.append(f"Flag: **{len(rows)}** · rozjazdy: **{len(issues)}**")
    out.append("")
    out.append("## Rozjazdy")
    out.extend(f"- {i}" for i in issues) if issues else out.append("- brak")
    out.append("")
    out.append("## Pełny inwentarz")
    out.append("| flaga | efektywna | źródło | default | flags.json | env |")
    out.append("|---|---|---|---|---|---|")
    for r in rows:
        env = "<br>".join(f"{u}: {v}" for u, v in r["env"].items()) or "—"
        out.append(f"| `{r['flag']}` | `{r['effective']!r}` | {r['source']} "
                   f"| `{r['default']!r}` | `{r['flags_json']!r}` | {env} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="pełny inwentarz na stdout")
    ap.add_argument("--md", help="zapisz raport markdown do pliku")
    args = ap.parse_args()
    rows, issues = build_registry()
    print(render(rows, issues, show_all=args.all))
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(render_md(rows, issues) + "\n")
        print(f"\nMD: {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

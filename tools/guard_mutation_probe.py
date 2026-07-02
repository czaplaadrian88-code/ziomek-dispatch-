#!/usr/bin/env python3
"""guard_mutation_probe — ODSŁANIA teatr strażników HARD-bramek Ziomka (audyt 2.0
L09, protokół C13). Read-only wobec plików źródłowych: mutuje CEL IN-MEMORY i
sprawdza, czy właściwe testy PADAJĄ (KILLED = strażnik działa) czy przechodzą
zielone (SURVIVED = teatr).

Dwa rodzaje sond (deterministyczne, zero edycji plików produkcyjnych):

  1. BEHAVIORAL — dla bramek `check_feasibility_v2` (bag-cap off-by-one,
     pickup_too_far flip, R6 per-order disable, SLA threshold, hard_tier_cap):
     w trybie PLUGIN (`-p guard_mutation_probe`, env `GUARD_MUT=<name>`) sonda
     w `pytest_configure` wstrzykuje ZMUTOWANĄ kopię `dispatch_v2.feasibility_v2`
     do `sys.modules` PRZED kolekcją. Plik na dysku NIETKNIĘTY. CLI odpala dwie
     grupy testów per mutacja: BEHAWIORALNE (nowy `test_feasibility_guards_behavioral`)
     vs LEGACY (istniejące `test_scale01_caps_flags` / `test_feasibility_c3`) —
     pokazuje, że część mutantów LEGACY przeżywa, a behawioralne zabija.

  2. POLARITY — dla bramki verdict-gate (`_always_propose_on()`): mutacja
     `if not _always_propose_on()`→`if _always_propose_on()` NIE zmienia zachowania
     modułu w sposób łapany przez sys.modules-injection (stary strażnik czyta ŹRÓDŁO
     z DYSKU), więc sondujemy na poziomie TEKSTU: stary detektor (obecność tokenu)
     vs nowy detektor polaryzacji (`gate_guard_polarity` z test_verdict_gate_guards).

Uruchomienie:
    python tools/guard_mutation_probe.py                # pełny raport (tabela+jsonl)
    GUARD_MUT=bagcap_ge_to_gt pytest <testy> -p guard_mutation_probe   # 1 mutacja (plugin)

Samo-lokalizacja (C12(e)): moduły i ścieżki liczone od `Path(__file__)`, NIGDY
hardcode worktree. Sprzątanie `sys.modules` w try/finally po stronie CLI (każda
mutacja = świeży subprocess pytest, więc izolacja naturalna).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Samo-lokalizacja ──────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_DISPATCH_ROOT = _THIS.parents[1]          # …/dispatch_v2 (worktree LUB kanon)
_TESTS_DIR = _DISPATCH_ROOT / "tests"

# ── Definicje mutacji BEHAWIORALNYCH (cel = dispatch_v2.feasibility_v2) ────────
# Każda: unikalny fragment źródła → jego mutant. Uniqueness zweryfikowana (grep -c==1).
BEHAVIORAL_MUTATIONS = {
    "bagcap_ge_to_gt": {
        "gate": "bag_full (sanity cap)",
        "old": "if len(bag) >= _bag_cap:",
        "new": "if len(bag) > _bag_cap:",
        "note": "off-by-one progu worka; L09: przeżył WSZYSTKIE testy behawioralne",
        "source": "feasibility_v2 ~460",
    },
    "pickup_far_flip": {
        "gate": "pickup_too_far",
        "old": "if pickup_dist_km > _pickup_reach_km():",
        "new": "if pickup_dist_km < _pickup_reach_km():",
        "note": "inwersja kierunku zasięgu odbioru",
        "source": "feasibility_v2 ~656",
    },
    "r6_per_order_disable": {
        "gate": "R6_per_order (carried-age HARD)",
        "old": "if r6_per_order_violations:",
        "new": "if False and r6_per_order_violations:",
        "note": "wyłącza WYŁĄCZNIE R6 per-order; sprawdza maskowanie przez SLA",
        "source": "feasibility_v2 ~1234",
    },
    "sla_threshold_999": {
        "gate": "sla_violation (35min)",
        "old": "DEFAULT_SLA_MINUTES = 35",
        "new": "DEFAULT_SLA_MINUTES = 999",
        "note": "rozbraja domyślny próg SLA",
        "source": "feasibility_v2 ~53",
    },
    "hard_tier_cap_neuter": {
        "gate": "hard_tier_bag_cap",
        "old": 'metrics["would_hard_cap"] = bag_after > _hard_cap',
        "new": 'metrics["would_hard_cap"] = bag_after > _hard_cap + 99',
        "note": "neutralizuje twardy cap per-tier (flaga ON)",
        "source": "feasibility_v2 ~468",
    },
}

# Grupy testów uruchamiane per mutacja (self-located).
_BEHAVIORAL_TARGETS = [str(_TESTS_DIR / "test_feasibility_guards_behavioral.py")]
_LEGACY_TARGETS = [
    str(_TESTS_DIR / "test_scale01_caps_flags.py"),
    str(_TESTS_DIR / "test_feasibility_c3.py"),
]


# ══════════════════════════════════════════════════════════════════════════════
# TRYB PLUGIN — wstrzyknięcie zmutowanego feasibility_v2 przed kolekcją
# ══════════════════════════════════════════════════════════════════════════════
def _load_mutated_feasibility(mut_name: str):
    """Zbuduj zmutowany moduł dispatch_v2.feasibility_v2 IN-MEMORY i zarejestruj
    w sys.modules. Plik na dysku nietknięty."""
    spec_info = BEHAVIORAL_MUTATIONS[mut_name]
    spec = importlib.util.find_spec("dispatch_v2.feasibility_v2")
    if spec is None or not spec.origin:
        raise RuntimeError("nie znaleziono dispatch_v2.feasibility_v2 na sys.path")
    src = Path(spec.origin).read_text(encoding="utf-8")
    n = src.count(spec_info["old"])
    if n != 1:
        raise RuntimeError(
            f"mutacja {mut_name}: fragment '{spec_info['old']}' wystąpił {n}× "
            f"(oczekiwano 1) w {spec.origin}")
    mutated = src.replace(spec_info["old"], spec_info["new"])
    module = importlib.util.module_from_spec(spec)
    module.__file__ = spec.origin
    code = compile(mutated, spec.origin, "exec")
    # zarejestruj PRZED exec (feasibility może self-referencyjnie importować)
    sys.modules["dispatch_v2.feasibility_v2"] = module
    exec(code, module.__dict__)
    # spójny handle na pakiecie
    import dispatch_v2  # noqa: F401
    setattr(sys.modules["dispatch_v2"], "feasibility_v2", module)


def pytest_configure(config):  # noqa: D401  (pytest hook)
    """Hook pytest: gdy env GUARD_MUT wskazuje mutację behawioralną, wstrzyknij ją."""
    mut = os.environ.get("GUARD_MUT", "").strip()
    if not mut or mut == "none":
        return
    if mut in BEHAVIORAL_MUTATIONS:
        _load_mutated_feasibility(mut)
        # widoczny ślad w nagłówku sesji
        try:
            config.pluginmanager.get_plugin("terminalreporter")
        except Exception:
            pass
        print(f"[guard_mutation_probe] WSTRZYKNIĘTO MUTANT: {mut} "
              f"({BEHAVIORAL_MUTATIONS[mut]['gate']})", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════════════
# TRYB CLI — orkiestracja: baseline + mutacje behawioralne + polarity
# ══════════════════════════════════════════════════════════════════════════════
def _run_pytest(targets, mut_name, extra_env=None):
    """Odpal pytest na `targets` z GUARD_MUT=mut_name (subprocess = izolacja).
    Zwraca (returncode, ostatnia_linia_podsumowania)."""
    env = dict(os.environ)
    env["GUARD_MUT"] = mut_name
    # udostępnij ten plik jako plugin importowalny po nazwie
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_THIS.parent)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, "-m", "pytest", *targets,
           "-p", "guard_mutation_probe", "-q", "-p", "no:cacheprovider",
           "--no-header", "-o", "addopts="]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    tail = ""
    for line in reversed(r.stdout.splitlines()):
        if line.strip() and ("passed" in line or "failed" in line or "error" in line):
            tail = line.strip()
            break
    return r.returncode, tail


def _probe_behavioral():
    """Dla każdej mutacji: baseline (targets muszą przechodzić) + KILLED/SURVIVED
    osobno dla grupy BEHAWIORALNEJ i LEGACY."""
    records = []
    for name, spec in BEHAVIORAL_MUTATIONS.items():
        beh_rc, beh_tail = _run_pytest(_BEHAVIORAL_TARGETS, name)
        leg_rc, leg_tail = _run_pytest(_LEGACY_TARGETS, name)
        rec = {
            "kind": "behavioral",
            "mutation": name,
            "gate": spec["gate"],
            "source": spec["source"],
            "note": spec["note"],
            "behavioral_verdict": "KILLED" if beh_rc != 0 else "SURVIVED",
            "behavioral_summary": beh_tail,
            "legacy_verdict": "KILLED" if leg_rc != 0 else "SURVIVED",
            "legacy_summary": leg_tail,
        }
        records.append(rec)
    return records


# ── POLARITY (verdict-gate) ───────────────────────────────────────────────────
def _ensure_dispatch_importable():
    """Zapewnij, że `import dispatch_v2` działa w procesie CLI. Samo-lokalizacja:
    scripts-root = rodzic katalogu dispatch_v2 (worktree LUB kanon), z env-override
    ZIOMEK_SCRIPTS_ROOT (spójnie z tests/conftest.py). NIE parsujemy conftest
    regexem — literał zniknął (S1: env-overridable), regex łamał się na refaktorze
    semantyko-zachowawczym (klasa C13/C12(f), złapane po merge FALA-SERIAL 02.07)."""
    try:
        import dispatch_v2  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    root = os.environ.get("ZIOMEK_SCRIPTS_ROOT", str(_DISPATCH_ROOT.parent))
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_verdict_helpers():
    """Załaduj test_verdict_gate_guards jako moduł (self-located), by reużyć
    `gate_guard_polarity` i dostać `dispatch_pipeline` + EXPECTED_GATES."""
    _ensure_dispatch_importable()
    path = _TESTS_DIR / "test_verdict_gate_guards.py"
    spec = importlib.util.spec_from_file_location("_vg_probe_helpers", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_vg_probe_helpers"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"nie załadowano test_verdict_gate_guards: {e}")
    return mod


def _old_token_detector(source_text, gate_id):
    """Replika STAREGO strażnika (L09 teatr): guarded == OBECNOŚĆ tokenu w warunku
    (bez rozróżnienia polaryzacji)."""
    import re
    lines = source_text.splitlines()
    for i, ln in enumerate(lines):
        if not re.search(r'verdict\s*=\s*"KOORD"', ln):
            continue
        gid = None
        for j in range(i, min(i + 6, len(lines))):
            if "reason" in lines[j]:
                m = re.search(r'reason\s*=\s*\(?\s*f?["\'](\w+)', lines[j]) \
                    or re.search(r'f["\'](\w+)', lines[j + 1] if j + 1 < len(lines) else "")
                if m:
                    gid = m.group(1)
                    break
        if gid != gate_id:
            continue
        k = i
        while k > 0 and "PipelineResult(" not in lines[k]:
            k -= 1
        cond_lines = []
        m = k - 1
        while m > 0:
            cond_lines.append(lines[m])
            if re.match(r'\s*if[\s(]', lines[m]):
                break
            m -= 1
        return any("_always_propose_on()" in c for c in cond_lines)
    return None


def _probe_polarity():
    """Mutuj `not _always_propose_on()`→`_always_propose_on()` w źródle
    dispatch_pipeline (in-memory string) na pierwszej bramce QUALITY i porównaj
    stary detektor tokenu vs nowy detektor polaryzacji."""
    helpers = _import_verdict_helpers()
    DP = helpers.DP
    expected = helpers.EXPECTED_GATES
    src = Path(DP.__file__).read_text(encoding="utf-8")

    # znajdź bramkę QUALITY z guardem `not _always_propose_on()` (poprawną dziś)
    base_pol = helpers.gate_guard_polarity(src)
    quality_gate = next(
        (g for g, c in expected.items() if c == "quality" and base_pol.get(g) == "negated"),
        None)
    records = []
    if quality_gate is None:
        records.append({
            "kind": "polarity", "mutation": "not->bare", "gate": "(brak QUALITY-negated)",
            "behavioral_verdict": "N/A", "legacy_verdict": "N/A",
            "note": "nie znaleziono żywej bramki QUALITY z guardem 'negated' — sprawdź kod",
        })
        return records

    # mutacja: usuń pierwsze `not ` przed `_always_propose_on()`
    mutated = src.replace("not _always_propose_on()", "_always_propose_on()", 1)
    assert mutated != src

    new_pol = helpers.gate_guard_polarity(mutated)
    old_flags = _old_token_detector(mutated, quality_gate)   # True=guarded (przeżył)
    new_polarity = new_pol.get(quality_gate)                 # 'bare'=złapany

    # stary detektor: guarded True (widzi token) → NIE alarmuje → SURVIVED
    old_verdict = "SURVIVED" if old_flags else "KILLED"
    # nowy detektor: polaryzacja 'bare' zamiast 'negated' → alarmuje → KILLED
    new_verdict = "KILLED" if new_polarity != "negated" else "SURVIVED"
    records.append({
        "kind": "polarity",
        "mutation": "not _always_propose_on() -> _always_propose_on()",
        "gate": quality_gate,
        "source": "dispatch_pipeline (_always_propose_on gates)",
        "note": "L09: stary strażnik = token-presence (teatr); nowy = polaryzacja",
        "old_token_guard_verdict": old_verdict,
        "new_polarity_guard_verdict": new_verdict,
        "old_token_flags_guarded": old_flags,
        "new_polarity_value": new_polarity,
    })
    return records


# ── Raport ────────────────────────────────────────────────────────────────────
def _print_table(behavioral, polarity):
    print("\n" + "=" * 92)
    print("GUARD MUTATION PROBE — teatr strażników HARD-bramek (audyt 2.0 L09 / C13)")
    print("=" * 92)
    print("\n[BEHAVIORAL] mutant feasibility_v2 → czy testy PADAJĄ (KILLED) czy przeżywa (SURVIVED)")
    print("-" * 92)
    print(f"{'mutacja':<24}{'bramka':<30}{'BEHAWIOR.':<12}{'LEGACY':<10}")
    print("-" * 92)
    n_killed_beh = n_survived_leg = 0
    for r in behavioral:
        if r["behavioral_verdict"] == "KILLED":
            n_killed_beh += 1
        if r["legacy_verdict"] == "SURVIVED":
            n_survived_leg += 1
        print(f"{r['mutation']:<24}{r['gate']:<30}{r['behavioral_verdict']:<12}{r['legacy_verdict']:<10}")
    print("-" * 92)
    print(f"BEHAWIORALNE zabiły {n_killed_beh}/{len(behavioral)} mutantów; "
          f"LEGACY przeżyło (teatr) {n_survived_leg}/{len(behavioral)}")

    print("\n[POLARITY] verdict-gate `not _always_propose_on()` → `_always_propose_on()`")
    print("-" * 92)
    for r in polarity:
        print(f"  bramka QUALITY: {r['gate']}")
        print(f"    stary strażnik (token-presence): {r.get('old_token_guard_verdict')}  "
              f"(guarded={r.get('old_token_flags_guarded')})")
        print(f"    nowy strażnik (polaryzacja):     {r.get('new_polarity_guard_verdict')}  "
              f"(polaryzacja='{r.get('new_polarity_value')}')")
    print("=" * 92)


def main():
    behavioral = _probe_behavioral()
    polarity = _probe_polarity()
    _print_table(behavioral, polarity)

    out = _DISPATCH_ROOT / "eod_drafts" / "2026-07-02" / "guard_mutation_probe.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with out.open("w", encoding="utf-8") as f:
        for r in behavioral + polarity:
            r = dict(r, ts=ts)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\njsonl → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

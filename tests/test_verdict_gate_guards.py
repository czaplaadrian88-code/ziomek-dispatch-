"""B-VERDICT-GATE-GUARDS (audyt 2026-06-24, spec odporności §6.B): snapshot
mapujący KAŻDĄ bramkę verdict=KOORD w dispatch_pipeline → klasę {quality|operational}
i obecność guardu `_always_propose_on()`. Chroni przed:
  - dodaniem NOWEJ bramki KOORD bez klasyfikacji (set-equality fail),
  - utratą guardu z bramki QUALITY (zaczęłaby milczeć/KOORD-ować mimo ALWAYS-PROPOSE),
  - dodaniem guardu do bramki OPERATIONAL (ALWAYS-PROPOSE stłumiłby eskalację
    bezpieczeństwa: pustą pulę / stale-state / early_bird).

Kontrakt (z docstring `_always_propose_on`): ALWAYS-PROPOSE neutralizuje TYLKO
bramki jakości (best_effort r6_breach/low_score, all_candidates_low_score → guard).
`early_bird` i pusta pula (no_solo) + stale/geometry/commit-divergence ZOSTAJĄ KOORD
(operational, bez guardu).

Test = PURE source-inspection (zero edycji kodu produkcji). Identyfikator bramki =
wiodący token reason-stringa (semantyczny, stabilny; zmiana → świadomy review).
"""
import re
from pathlib import Path

import dispatch_v2.dispatch_pipeline as DP
import dispatch_v2.core.gates as GATES
import dispatch_v2.core.selection as SELECTION

_SRC_PATH = Path(DP.__file__)
# K10/K12 refaktoru: bramki wejściowe (early_bird, geokod-defense) w core/gates.py,
# bramki werdyktu (stale/geometry/commit-divergence/low-score/best_effort/no_solo)
# w core/selection.py — skaner MUSI czytać wszystkie źródła, inaczej bramka znika
# z pola widzenia strażnika (martwy strażnik = teatr, klasa C13). Parser jest
# liniowy i lokalny, więc konkatenacja tekstów jest bezpieczna.
_SRC_PATHS = [Path(DP.__file__), Path(GATES.__file__), Path(SELECTION.__file__)]


def _read_all_sources() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _SRC_PATHS)

# ── Rejestr klasyfikacji (JEDNO źródło prawdy) ───────────────────────────────
# quality    = bramka jakości; ALWAYS-PROPOSE ją neutralizuje → MUSI mieć guard.
# operational = eskalacja operacyjna (safety/pusta pula/stan); ZAWSZE KOORD → BEZ guardu.
EXPECTED_GATES = {
    "early_bird":                   "operational",
    "state_likely_stale":           "operational",
    "geometry_blind_fallback":      "operational",
    "all_candidates_low_score":     "quality",
    "commit_divergence_gate":       "operational",
    "difficult_geometry_redirect":  "operational",
    "best_effort_r6_breach_v2":     "quality",
    "best_effort_r6_breach":        "quality",
    "best_effort_low_score":        "quality",
    "no_solo_candidates":           "operational",
}


def _koord_gates_from_source():
    """Zwraca {gate_id: guarded_bool} przez inspekcję źródła dispatch_pipeline.

    Dla każdego `verdict="KOORD"`:
      - gate_id = pierwszy token reason-stringa poniżej,
      - guarded = `_always_propose_on()` występuje w warunku `if` poprzedzającym
        otwarcie PipelineResult (czyli między najbliższym `if ` w górę a samym
        `PipelineResult(`).
    """
    lines = _read_all_sources().splitlines()
    out = {}
    for i, ln in enumerate(lines):
        if re.search(r'verdict\s*=\s*"KOORD"', ln):
            # gate_id: najbliższy `reason=` w dół, pierwszy token f-stringa
            gate_id = None
            for j in range(i, min(i + 6, len(lines))):
                if "reason" in lines[j]:
                    m = re.search(r'reason\s*=\s*\(?\s*f?["\'](\w+)', lines[j]) \
                        or re.search(r'f["\'](\w+)', lines[j + 1] if j + 1 < len(lines) else "")
                    if m:
                        gate_id = m.group(1)
                        break
            if gate_id is None:
                gate_id = f"__unparsed_line_{i+1}"
            # guarded: w górę do otwarcia PipelineResult, potem do najbliższego `if `
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
            guarded = any("_always_propose_on()" in c for c in cond_lines)
            out[gate_id] = guarded
    return out


def gate_guard_polarity(source_text):
    """POLARYTET guardu per bramka KOORD z DOWOLNEGO tekstu źródła (reużywalne —
    także dla mutation-probe: karmimy zmutowany string i sprawdzamy, czy checker
    łapie inwersję). Zwraca {gate_id: 'negated'|'bare'|'absent'}:

      - 'negated' = warunek bramki zawiera `not _always_propose_on()` (poprawny
        guard bramki QUALITY — pod ALWAYS-PROPOSE przepada do PROPOSE),
      - 'bare'    = zawiera `_always_propose_on()` BEZ `not` (INWERSJA — mutant
        `if not X`→`if X`; bramka QUALITY zaczęłaby KOORD-ować mimo ALWAYS-PROPOSE),
      - 'absent'  = brak helpera w warunku (poprawne dla bramki OPERATIONAL).

    Kluczowa różnica vs `_koord_gates_from_source` (test_guard_helper): tamten
    wykrywa OBECNOŚĆ tokenu (L09 teatr — mutacja `not X`→`X` przechodzi zielona),
    ten rozróżnia POLARYTET.
    """
    lines = source_text.splitlines()
    out = {}
    for i, ln in enumerate(lines):
        if not re.search(r'verdict\s*=\s*"KOORD"', ln):
            continue
        gate_id = None
        for j in range(i, min(i + 6, len(lines))):
            if "reason" in lines[j]:
                m = re.search(r'reason\s*=\s*\(?\s*f?["\'](\w+)', lines[j]) \
                    or re.search(r'f["\'](\w+)', lines[j + 1] if j + 1 < len(lines) else "")
                if m:
                    gate_id = m.group(1)
                    break
        if gate_id is None:
            gate_id = f"__unparsed_line_{i+1}"
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
        cond = "\n".join(cond_lines)
        if re.search(r'not\s+_always_propose_on\(\)', cond):
            out[gate_id] = "negated"
        elif "_always_propose_on()" in cond:
            out[gate_id] = "bare"
        else:
            out[gate_id] = "absent"
    return out


def _gate_polarity_from_disk():
    return gate_guard_polarity(_read_all_sources())


def test_quality_gates_use_negated_guard():
    """WARIANT POLARYTETOWY (C13 anti-teatr): bramka QUALITY MUSI mieć guard w
    postaci `not _always_propose_on()` — sama OBECNOŚĆ tokenu (stary test) NIE
    wystarcza. Mutacja `if not _always_propose_on()`→`if _always_propose_on()`
    zmienia polaryzację na 'bare' → ten test PADA (mutant zabity), podczas gdy
    `test_quality_gates_are_guarded` (token-presence) przeżywa."""
    pol = _gate_polarity_from_disk()
    for gate, cls in EXPECTED_GATES.items():
        if cls == "quality":
            assert pol.get(gate) == "negated", (
                f"bramka QUALITY '{gate}' polaryzacja={pol.get(gate)!r} (oczekiwano "
                f"'negated'); 'bare' = inwersja `not` (mutant), 'absent' = utrata guardu")


def test_operational_gates_have_no_propose_guard_polarity():
    """WARIANT POLARYTETOWY: bramka OPERATIONAL nie może mieć `_always_propose_on()`
    w warunku w ŻADNEJ polaryzacji — inaczej ALWAYS-PROPOSE stłumiłby eskalację."""
    pol = _gate_polarity_from_disk()
    for gate, cls in EXPECTED_GATES.items():
        if cls == "operational":
            assert pol.get(gate) == "absent", (
                f"bramka OPERATIONAL '{gate}' MA guard propose (polaryzacja="
                f"{pol.get(gate)!r}) — regresja eskalacji operacyjnej")


def test_all_koord_gates_are_classified():
    """Każda bramka verdict=KOORD w kodzie MUSI być w rejestrze (i odwrotnie).
    Nowa bramka bez klasy / usunięta bramka / przemianowany reason → fail."""
    found = set(_koord_gates_from_source())
    expected = set(EXPECTED_GATES)
    unparsed = {g for g in found if g.startswith("__unparsed_line_")}
    assert not unparsed, f"nie sparsowano reason dla bramek KOORD: {unparsed}"
    missing = expected - found  # rejestr ma, kod nie → usunięta/przemianowana
    extra = found - expected    # kod ma, rejestr nie → NOWA niesklasyfikowana
    assert not extra, (
        f"NOWE bramki KOORD bez klasyfikacji w EXPECTED_GATES: {extra} — "
        f"dodaj jako 'quality' (neutralizowana ALWAYS-PROPOSE, MUSI mieć guard) "
        f"albo 'operational' (zawsze eskaluje, BEZ guardu)")
    assert not missing, (
        f"bramki z rejestru zniknęły/przemianowane w kodzie: {missing} — "
        f"zweryfikuj i zaktualizuj EXPECTED_GATES")


def test_quality_gates_are_guarded():
    """Bramka QUALITY MUSI mieć guard `not _always_propose_on()` — inaczej
    pod ALWAYS-PROPOSE zaczęłaby KOORD-ować zamiast PROPOSE best_effort."""
    found = _koord_gates_from_source()
    for gate, cls in EXPECTED_GATES.items():
        if cls == "quality":
            assert found.get(gate) is True, (
                f"bramka QUALITY '{gate}' BEZ guardu `_always_propose_on()` — "
                f"regresja: milczałaby/KOORD mimo ALWAYS-PROPOSE")


def test_operational_gates_are_unguarded():
    """Bramka OPERATIONAL NIE może mieć guardu — inaczej ALWAYS-PROPOSE
    stłumiłby eskalację bezpieczeństwa (pusta pula / stale / early_bird)."""
    found = _koord_gates_from_source()
    for gate, cls in EXPECTED_GATES.items():
        if cls == "operational":
            assert found.get(gate) is False, (
                f"bramka OPERATIONAL '{gate}' MA guard `_always_propose_on()` — "
                f"regresja: ALWAYS-PROPOSE stłumiłby eskalację operacyjną")


def test_guard_helper_exists():
    """`_always_propose_on()` istnieje i czyta flagę (kontrakt neutralizacji)."""
    import inspect
    assert hasattr(DP, "_always_propose_on")
    src = inspect.getsource(DP._always_propose_on)
    assert "ENABLE_ALWAYS_PROPOSE_ON_SATURATION" in src

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

_SRC_PATH = Path(DP.__file__)

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
    lines = _SRC_PATH.read_text(encoding="utf-8").splitlines()
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

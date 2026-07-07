"""S28-B — INV-POS-NO-PRODUCE: ratchet entropii producentów pozycji-placeholder.

Inwariant (ZIOMEK_INVARIANTS ⑤/DANE): ŻADNA ścieżka silnika NIE *produkuje*
`(0,0)` ani `BIALYSTOK_CENTER` jako pozycji. Pełne domknięcie = flip L2.1
(`ENABLE_COORD_SENTINEL_INGEST_GUARD`, zbudowany, czeka na ACK) + wyeliminowanie
świadomej fikcji no_gps (osobna fala, filar #3). Do tego czasu — **ratchet
kierunku**: liczba miejsc-producentów może TYLKO maleć. Nowy producent =
regresja entropii = test RED. Meta-reguła „entropia niżej" uczyniona wykonalną.

Dwie KLASY producenta (osobno śledzone, CLAUDE.md je rozróżnia):
  A. coord-placeholder `or (0.0, 0.0)` — fallback braku geokodu (dispatch_pipeline);
     docelowo → jawny sentinel/skip (L2.1), NIE cichy (0,0).
  B. `.pos = BIALYSTOK_CENTER` — syntetyczna pozycja no_gps/pre_shift
     (courier_resolver); świadoma polityka „Unknown", ale kierunek = malejący.

ZERO zmian silnika (regression-guard). Skan pomija `.claude` (worktree'y
sąsiednich sesji — lekcja S28-A) + tests/tools/eod_drafts/docs.
"""
import re
from pathlib import Path

# Kanon LUB worktree — samo-lokalizacja (parents[1] = …/dispatch_v2)
REPO = Path(__file__).resolve().parents[1]
_EXCLUDE = {"tests", "tools", "eod_drafts", "docs", "__pycache__",
            "dispatch_state", "venv", ".git", ".claude"}

# Idiomy WYŁĄCZNIE producenckie (guardy używają `!= (0.0,0.0)` / `== (0.0,0.0)`
# — te NIE są łapane; producent = fallback-assign `or (0.0,0.0)` albo
# przypisanie `.pos = BIALYSTOK_CENTER`).
_PROD_ZERO = re.compile(r"\bor \(0\.0, 0\.0\)")
_PROD_BIALYSTOK = re.compile(r"\.pos\s*=\s*BIALYSTOK_CENTER\b")

# BASELINE zamrożony 2026-07-07 (S28-B). Kierunek: TYLKO w dół — gdy L2.1 flip
# usunie producentów, OBNIŻ baseline (ratchet się zaciska). Nowy producent
# przebijający baseline = RED (dodaj sentinel/skip u źródła, nie kolejny (0,0)).
_BASELINE_ZERO = 4       # dispatch_pipeline.py (1622, 3450, 3452, 3928)
_BASELINE_BIALYSTOK = 6  # courier_resolver.py (1074, 1673, 1682, 1736, 1776, 1788)


def _engine_sources(root: Path):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in _EXCLUDE for part in rel.parts):
            continue
        if ".bak" in p.name or p.name.startswith("."):
            continue
        try:
            yield str(rel), p.read_text(encoding="utf-8")
        except OSError:
            continue


def _count(root: Path, rx):
    hits = []
    for rel, src in _engine_sources(root):
        n = len(rx.findall(src))
        if n:
            hits.append((rel, n))
    return sum(n for _, n in hits), hits


def test_no_new_coord_zero_placeholder_producers():
    """Klasa A: `or (0.0, 0.0)` fallback — kierunek malejący (docelowo 0 via L2.1)."""
    total, hits = _count(REPO, _PROD_ZERO)
    assert total <= _BASELINE_ZERO, (
        f"NOWY producent coord-placeholder (0,0): {total} > baseline {_BASELINE_ZERO}. "
        f"Fix u ŹRÓDŁA (jawny sentinel/skip, NIE cichy (0,0)) — INV-POS-NO-PRODUCE. "
        f"Miejsca: {hits}")


def test_no_new_bialystok_center_position_producers():
    """Klasa B: `.pos = BIALYSTOK_CENTER` syntetyczna pozycja — kierunek malejący."""
    total, hits = _count(REPO, _PROD_BIALYSTOK)
    assert total <= _BASELINE_BIALYSTOK, (
        f"NOWY producent BIALYSTOK_CENTER: {total} > baseline {_BASELINE_BIALYSTOK}. "
        f"no_gps/pre_shift = typ Unknown, NIE nowa fikcja pozycji — INV-POS-NO-PRODUCE. "
        f"Miejsca: {hits}")


def test_ratchet_detects_new_producer(tmp_path):
    """Mutation-probe (C13): syntetyczne drzewo silnika z DODATKOWYM producentem
    ponad baseline → ratchet MUSI zapalić. Dowód, że guard ma zęby (nie przepuszcza
    nowego (0,0)), a jednocześnie NIE zapala na guardach (`!= (0.0,0.0)`)."""
    eng = tmp_path
    # baseline-safe: guard (nie producent) — musi być zignorowany
    (eng / "feasibility_like.py").write_text(
        "def ok(c):\n    return c != (0.0, 0.0)\n", encoding="utf-8")
    total0, _ = _count(eng, _PROD_ZERO)
    assert total0 == 0, "guard `!= (0.0,0.0)` fałszywie policzony jako producent"
    # dodaj REALNEGO producenta
    (eng / "leaky.py").write_text(
        "def f(e):\n    return e.get('pickup') or (0.0, 0.0)\n", encoding="utf-8")
    total1, hits1 = _count(eng, _PROD_ZERO)
    assert total1 == 1 and hits1 == [("leaky.py", 1)], \
        f"ratchet OŚLEPŁ na nowego producenta (0,0): {total1} {hits1}"
    # i BIALYSTOK_CENTER
    (eng / "synth.py").write_text(
        "def g(cs):\n    cs.pos = BIALYSTOK_CENTER\n", encoding="utf-8")
    totb, hitsb = _count(eng, _PROD_BIALYSTOK)
    assert totb == 1 and hitsb == [("synth.py", 1)], \
        f"ratchet OŚLEPŁ na nowego producenta BIALYSTOK_CENTER: {totb} {hitsb}"


def test_ratchet_ignores_adjacent_claude_worktree(tmp_path):
    """S28-A lekcja: producent w worktree sąsiedniej sesji (.claude/worktrees)
    NIE może przebić baseline aktualnego pkg (fałszywy RED)."""
    (tmp_path / ".claude/worktrees/agent-x").mkdir(parents=True)
    (tmp_path / ".claude/worktrees/agent-x/leaky.py").write_text(
        "x = a or (0.0, 0.0)\n", encoding="utf-8")
    total, _ = _count(tmp_path, _PROD_ZERO)
    assert total == 0, "skan wciągnął producenta z .claude/worktrees"

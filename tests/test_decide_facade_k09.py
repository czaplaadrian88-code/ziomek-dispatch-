"""K09 (refaktor, 2026-07-06): fasada core.decide — czysta delegacja 1:1.

Trzy strażniki:
1. delegacja 1:1 — decide() przekazuje DOKŁADNIE te obiekty (identity) do
   dispatch_pipeline.assess_order i zwraca jego wynik bez dotykania;
2. parytet fasada↔bezpośrednio na deterministycznej ścieżce (SKIP bez geokodu);
3. strażnik kompletności call-site'ów (AST, nie grep — odporny na docstringi):
   nikt poza allowlistą nie woła assess_order bezpośrednio. To domyka mapę
   kompletności K09 na przyszłość (nowy caller MUSI iść przez fasadę).
"""
import ast
from datetime import datetime, timezone
from pathlib import Path

import dispatch_v2.dispatch_pipeline as dp
from dispatch_v2.core.decide import decide
from dispatch_v2.core.world_state import WorldState

REPO = Path(__file__).resolve().parents[1]


def test_delegation_1to1_identity(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_assess(order_event, fleet_snapshot, restaurant_meta=None, now=None, *,
                    pending_queue=None, demand_context=None, _bypass_early_bird=False):
        captured.update(
            order_event=order_event, fleet_snapshot=fleet_snapshot,
            restaurant_meta=restaurant_meta, now=now, pending_queue=pending_queue,
            demand_context=demand_context, _bypass_early_bird=_bypass_early_bird,
        )
        return sentinel

    monkeypatch.setattr(dp, "assess_order", fake_assess)
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    world = WorldState(fleet_snapshot={"77": "cs"}, restaurant_meta={"m": 1}, now=now,
                       pending_queue=["p"], demand_context={"d": 2})
    order_event = {"order_id": "K09"}
    res = decide(world, order_event, _bypass_early_bird=True)
    assert res is sentinel, "fasada MUSI zwrócić wynik assess_order bez zmian"
    assert captured["order_event"] is order_event
    assert captured["fleet_snapshot"] is world.fleet_snapshot
    assert captured["restaurant_meta"] is world.restaurant_meta
    assert captured["now"] is now
    assert captured["pending_queue"] is world.pending_queue
    assert captured["demand_context"] is world.demand_context
    assert captured["_bypass_early_bird"] is True


def test_delegation_defaults(monkeypatch):
    captured = {}

    def fake_assess(order_event, fleet_snapshot, restaurant_meta=None, now=None, **kw):
        captured.update(kw, restaurant_meta=restaurant_meta, now=now)
        return None

    monkeypatch.setattr(dp, "assess_order", fake_assess)
    decide(WorldState(fleet_snapshot={}), {"order_id": "K09D"})
    assert captured["restaurant_meta"] is None
    assert captured["now"] is None
    assert captured["pending_queue"] is None
    assert captured["demand_context"] is None
    assert captured["_bypass_early_bird"] is False


def test_parity_facade_vs_direct():
    # Deterministyczna ścieżka bez I/O: brak geokodu pickup → SKIP (defense gate L1).
    order_event = {"order_id": "K09PAR", "restaurant": "T", "delivery_address": "Testowa 1"}
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    r_direct = dp.assess_order(order_event, {}, None, now)
    r_facade = decide(WorldState(fleet_snapshot={}, now=now), order_event)
    assert (r_direct.verdict, r_direct.reason) == (r_facade.verdict, r_facade.reason)
    assert r_direct.order_id == r_facade.order_id


# Allowlista wywołań bezpośrednich (K09):
#  - dispatch_pipeline.py: definicja wrappera + rekurencyjny kontrfaktyk early-bird,
#  - core/decide.py: fasada (jedyny legalny most),
#  - tools/world_replay.py: własność sesji B sprintu równoległego (fasada jest
#    przezroczysta, parytet nietknięty) — migracja u właściciela, N-D w K09.
_ALLOWED_ASSESS_CALLERS = {
    "dispatch_pipeline.py",
    "core/decide.py",
    "tools/world_replay.py",
}


def _scan_direct_assess_callsites(root):
    """Zwraca listę `rel:lineno` bezpośrednich wywołań assess_order poza
    allowlistą, skanując `root`. Pominięte: tests/eod_drafts/docs, `.bak`
    ORAZ `.claude/` (worktree'y sąsiednich sesji, ADR-007 — ich kopia
    dispatch_pipeline.py woła assess_order legalnie, ale pod inną ścieżką
    niż allowlista → fałszywy offender; skanujemy TYLKO ten pkg)."""
    offenders = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("tests/", "eod_drafts/", "docs/", ".claude/")) or ".bak" in rel:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else None)
                if name == "assess_order" and rel not in _ALLOWED_ASSESS_CALLERS:
                    offenders.append(f"{rel}:{node.lineno}")
    return offenders


def test_no_direct_assess_order_callsites_outside_facade():
    offenders = _scan_direct_assess_callsites(REPO)
    assert not offenders, (
        "assess_order wołany bezpośrednio poza fasadą core.decide "
        f"(K09 — nowy call-site idzie przez decide()): {offenders}"
    )


def test_scan_ignores_adjacent_claude_worktree(tmp_path):
    """S28-A: worktree sąsiedniej sesji (`.claude/worktrees/agent-*`, ADR-007)
    ma własną kopię dispatch_pipeline.py wołającą assess_order — pod inną
    ścieżką niż allowlista → fałszywy offender (ugryzło dziś). Skan MUSI
    pomijać `.claude`. Mutation-probe: taki sam call pod NIE-wykluczoną
    ścieżką (nowy_moduł.py) NADAL łapany → skan nie oślepł."""
    (tmp_path / ".claude/worktrees/agent-x").mkdir(parents=True)
    (tmp_path / ".claude/worktrees/agent-x/dispatch_pipeline.py").write_text(
        "def f():\n    return assess_order(1, 2)\n", encoding="utf-8")
    assert _scan_direct_assess_callsites(tmp_path) == [], \
        "skan wciągnął worktree z .claude jako offendera"

    # mutation-probe: prawdziwy nowy call-site poza fasadą → wykryty
    (tmp_path / "nowy_modul.py").write_text(
        "def g():\n    return assess_order(3, 4)\n", encoding="utf-8")
    off = _scan_direct_assess_callsites(tmp_path)
    assert any("nowy_modul.py" in o for o in off), \
        f"skan OŚLEPŁ — nowy bezpośredni call-site musi być offenderem: {off}"

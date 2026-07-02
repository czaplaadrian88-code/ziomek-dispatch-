"""Behawioralne testy observability/log_rotation.py (audyt 2.0 L13, C13).

Import PRZEZ ŚCIEŻKĘ PLIKU (nie przez pakiet dispatch_v2), bo conftest wpina
KANONICZNY /scripts na sys.path, gdzie log_rotation.py był usunięty 2026-06-11.
Ścieżka liczona względem tego pliku → testuje moduł LEŻĄCY OBOK (worktree teraz,
kanon po merge'u).

Fixtures: sztuczne pliki z podrobionym mtime (os.utime) + wstrzykiwany `now`, więc
granice wieku są deterministyczne. Katalog testowy = PODKATALOG tmp_path
(`obs_dir`), bo autouse-fixture conftestu kopiuje flags.json do samego tmp_path.
Zasady sprawdzane behawioralnie:
  - dry-run: NIC nie znika (liczba plików przed == po)
  - apply: kasuje TYLKO allowlist starsze niż retention
  - denylist NIETYKALNY nawet gdy pasuje wiekiem i datą (klucz bezpieczeństwa)
  - 13.9d zostaje, 14.1d leci (granica retencji)
  - --max-delete respektowany (kasuje najstarsze, reszta zostaje, capped=True)
  - UNMATCHED (datowany .jsonl spoza allowlisty) NIE jest kasowany
"""
import importlib.util
import os
import time
from pathlib import Path

import pytest

# ── Import modułu pod testem po ścieżce (co-located) ────────────────────────
_MOD_PATH = Path(__file__).resolve().parent.parent / "observability" / "log_rotation.py"
_spec = importlib.util.spec_from_file_location("observability_log_rotation_under_test", _MOD_PATH)
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)

# "Teraz" = realny czas importu. Dzięki temu testy z wstrzykniętym now=NOW ORAZ
# testy CLI (main() używa time.time()) widzą ten sam, spójny punkt odniesienia
# (dryf sub-sekundowy jest pomijalny przy granularności dni).
NOW = time.time()
DAY = 86400.0


@pytest.fixture
def obs_dir(tmp_path):
    """Czysty podkatalog na logi observability (izolacja od autouse flags.json)."""
    d = tmp_path / "observability"
    d.mkdir()
    return d


def _mk(dirp: Path, name: str, age_days: float, content: str = "line\n") -> Path:
    """Tworzy plik i ustawia mtime na NOW - age_days (przez os.utime)."""
    p = dirp / name
    p.write_text(content)
    mt = NOW - age_days * DAY
    os.utime(p, (mt, mt))
    return p


def _count_files(dirp: Path) -> int:
    return sum(1 for e in dirp.iterdir() if e.is_file())


# ── 1. dry-run tylko listuje — NIC nie znika ────────────────────────────────
def test_dry_run_lists_but_deletes_nothing(obs_dir):
    _mk(obs_dir, "candidate_decisions_20260101.jsonl", age_days=40)
    _mk(obs_dir, "fleet_filter_20260101.jsonl", age_days=40)
    _mk(obs_dir, "candidate_decisions_20260701.jsonl", age_days=2)
    before = _count_files(obs_dir)

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=False, now=NOW)

    after = _count_files(obs_dir)
    assert before == after == 3, "dry-run NIE MOŻE nic skasować"
    assert summary["deleted_count"] == 0
    assert summary["candidates_total"] == 2  # dwa 40-dniowe kwalifikują się
    assert summary["mode"] == "DRY-RUN"


# ── 2. apply kasuje TYLKO stare allowlist ───────────────────────────────────
def test_apply_deletes_only_old_allowlist(obs_dir):
    old_a = _mk(obs_dir, "candidate_decisions_20260101.jsonl", age_days=40)
    old_b = _mk(obs_dir, "fleet_filter_20260101.jsonl", age_days=30)
    young = _mk(obs_dir, "candidate_decisions_20260701.jsonl", age_days=2)

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=True, now=NOW)

    assert not old_a.exists(), "stary allowlist ma zniknąć"
    assert not old_b.exists(), "stary allowlist ma zniknąć"
    assert young.exists(), "młody allowlist ma zostać"
    assert summary["deleted_count"] == 2
    assert summary["kept_count"] == 1
    assert summary["freed_bytes"] > 0


# ── 3. DENYLIST nietykalny — nawet stary i z datą w nazwie ──────────────────
def test_denylist_untouchable_even_when_old_and_datestamped(obs_dir):
    # Nazwy które PASUJĄ wiekiem (stare) i mają datę — ale denylist wygrywa.
    protected = [
        _mk(obs_dir, "shadow_decisions_20250101.jsonl", age_days=400),
        _mk(obs_dir, "decision_outcomes_20250101.jsonl", age_days=400),
        _mk(obs_dir, "gps_delivery_truth_20250101.jsonl", age_days=400),
        _mk(obs_dir, "sla_log_20250101.jsonl", age_days=400),
        _mk(obs_dir, "orders_state_20250101.jsonl", age_days=400),
        _mk(obs_dir, "courier_plans_20250101.jsonl", age_days=400),
        _mk(obs_dir, "courier_last_pos_20250101.jsonl", age_days=400),
        _mk(obs_dir, "pending_proposals_20250101.jsonl", age_days=400),
        _mk(obs_dir, "events.db", age_days=400),
        _mk(obs_dir, "candidate_logger.py", age_days=400),
    ]
    # jeden realny stary allowlist, żeby apply w ogóle coś robił
    victim = _mk(obs_dir, "candidate_decisions_20250101.jsonl", age_days=400)

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=True, now=NOW)

    for p in protected:
        assert p.exists(), f"denylist MUSI ochronić {p.name}"
    assert not victim.exists(), "allowlist stary ma zniknąć (kontrola dodatnia)"
    assert summary["deleted_count"] == 1
    assert summary["denied_count"] == len(protected)


# ── 4. granica 13.9d zostaje / 14.1d leci ───────────────────────────────────
def test_boundary_retention_edge(obs_dir):
    stays = _mk(obs_dir, "fleet_filter_20260610.jsonl", age_days=13.9)
    goes = _mk(obs_dir, "fleet_filter_20260609.jsonl", age_days=14.1)

    lr.run(log_dir=obs_dir, retention_days=14, apply=True, now=NOW)

    assert stays.exists(), "13.9d < 14d → zostaje"
    assert not goes.exists(), "14.1d > 14d → leci"


# ── 5. --max-delete respektowany (najstarsze najpierw, reszta zostaje) ──────
def test_max_delete_respected(obs_dir):
    # 5 starych plików o RÓŻNYM wieku → deterministyczna kolejność oldest-first.
    files = {
        "candidate_decisions_20260101.jsonl": 60,
        "candidate_decisions_20260102.jsonl": 55,
        "candidate_decisions_20260103.jsonl": 50,
        "candidate_decisions_20260104.jsonl": 45,
        "candidate_decisions_20260105.jsonl": 40,
    }
    paths = {n: _mk(obs_dir, n, a) for n, a in files.items()}

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=True, max_delete=2, now=NOW)

    assert summary["deleted_count"] == 2, "max-delete=2 → dokładnie 2 skasowane"
    assert summary["capped"] is True
    assert summary["candidates_total"] == 5
    # dwa NAJSTARSZE (60d, 55d) zniknęły; trzy młodsze zostały
    assert not paths["candidate_decisions_20260101.jsonl"].exists()
    assert not paths["candidate_decisions_20260102.jsonl"].exists()
    assert paths["candidate_decisions_20260103.jsonl"].exists()
    assert paths["candidate_decisions_20260104.jsonl"].exists()
    assert paths["candidate_decisions_20260105.jsonl"].exists()
    assert _count_files(obs_dir) == 3


# ── 6. classify() — denylist wygrywa, allowlist tylko datowane, reszta UNMATCHED
def test_classify_precedence_and_shapes():
    assert lr.classify("candidate_decisions_20260504.jsonl")[0] == "ALLOW"
    assert lr.classify("fleet_filter_20260702.jsonl")[0] == "ALLOW"
    # denylist wygrywa nad kształtem "datowany candidate/fleet"
    assert lr.classify("shadow_decisions_20260101.jsonl")[0] == "DENY"
    assert lr.classify("sla_log_20260101.jsonl")[0] == "DENY"
    assert lr.classify("events.db")[0] == "DENY"
    assert lr.classify("log_rotation.py")[0] == "DENY"
    # datowany .jsonl SPOZA allowlisty = UNMATCHED (nietykany)
    assert lr.classify("some_other_shadow_20260101.jsonl")[0] == "UNMATCHED"
    # allowlist wymaga 8-cyfrowej daty — bez daty NIE pasuje
    assert lr.classify("candidate_decisions.jsonl")[0] == "UNMATCHED"
    assert lr.classify("random_note.txt")[0] == "UNMATCHED"


# ── 7. datowany .jsonl spoza allowlisty NIE jest kasowany ───────────────────
def test_unmatched_datestamped_never_deleted(obs_dir):
    unmatched = _mk(obs_dir, "some_experiment_shadow_20250101.jsonl", age_days=300)
    victim = _mk(obs_dir, "fleet_filter_20250101.jsonl", age_days=300)

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=True, now=NOW)

    assert unmatched.exists(), "plik spoza allowlisty NIE może być kasowany"
    assert not victim.exists()
    assert summary["unmatched_count"] == 1
    assert summary["deleted_count"] == 1


# ── 7b. DENYLIST wygrywa z NACHODZĄCĄ allowlistą (future-proofing, cel mut-check #2)
def test_denylist_wins_over_overlapping_allowlist(obs_dir, monkeypatch):
    """Gdyby allowlist kiedyś rozszerzono na szeroki wzorzec .jsonl, denylist NADAL
    musi chronić ledgery. Tu allowlist celowo pasuje do wszystkiego *.jsonl, więc
    denylistowany plik pasuje do OBU list — precedencja denylist-first jest jedyną
    linią obrony (mutacja kolejności w classify() ubija ten test)."""
    import re as _re
    monkeypatch.setattr(
        lr, "ALLOWLIST_REGEXES", list(lr.ALLOWLIST_REGEXES) + [_re.compile(r".*\.jsonl$")]
    )
    protected = _mk(obs_dir, "shadow_decisions_20250101.jsonl", age_days=400)
    victim = _mk(obs_dir, "candidate_decisions_20250101.jsonl", age_days=400)

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=True, now=NOW)

    assert protected.exists(), "denylist MUSI wygrać z nachodzącą allowlistą"
    assert not victim.exists(), "zwykły stary allowlist ma zniknąć (kontrola)"
    assert lr.classify("shadow_decisions_20250101.jsonl")[0] == "DENY"
    assert summary["denied_count"] == 1
    assert summary["deleted_count"] == 1


# ── 8. brak katalogu = bezpieczny no-op ─────────────────────────────────────
def test_missing_dir_is_safe_noop(tmp_path):
    missing = tmp_path / "does_not_exist"
    summary = lr.run(log_dir=missing, retention_days=14, apply=True, now=NOW)
    assert summary["dir_missing"] is True
    assert summary["deleted_count"] == 0
    assert summary["candidates_total"] == 0


# ── 9. oldest_kept raportowany + freed_planned w dry-run ────────────────────
def test_dry_run_reports_freed_and_oldest_kept(obs_dir):
    _mk(obs_dir, "candidate_decisions_20260101.jsonl", age_days=40, content="A" * 100 + "\n")
    _mk(obs_dir, "candidate_decisions_20260628.jsonl", age_days=4)
    _mk(obs_dir, "candidate_decisions_20260629.jsonl", age_days=3)

    summary = lr.run(log_dir=obs_dir, retention_days=14, apply=False, now=NOW)

    assert summary["freed_planned_bytes"] > 0, "dry-run raportuje ile BY zwolnił"
    assert summary["freed_bytes"] == 0, "dry-run nie zwalnia realnie"
    assert summary["oldest_kept"] is not None
    # najstarszy ZACHOWANY to ten 4-dniowy
    assert summary["oldest_kept"][0] == "candidate_decisions_20260628.jsonl"


# ── 10. main() CLI: default dry-run nie kasuje; exit 0 ──────────────────────
def test_main_cli_default_is_dry_run(obs_dir):
    old = _mk(obs_dir, "fleet_filter_20260101.jsonl", age_days=40)
    rc = lr.main(["--dir", str(obs_dir), "--retention-days", "14"])
    assert rc == 0
    assert old.exists(), "domyślny CLI = dry-run, NIC nie kasuje"


def test_main_cli_apply_deletes(obs_dir):
    old = _mk(obs_dir, "fleet_filter_20260101.jsonl", age_days=40)
    young = _mk(obs_dir, "fleet_filter_20260701.jsonl", age_days=1)
    rc = lr.main(["--dir", str(obs_dir), "--retention-days", "14", "--apply"])
    assert rc == 0
    assert not old.exists()
    assert young.exists()


def test_main_cli_rejects_bad_retention(obs_dir):
    assert lr.main(["--dir", str(obs_dir), "--retention-days", "0"]) == 2

"""A-4 (2026-08-02, sesja a4-upsert-idempotent) — oracle IDEMPOTENCJI + PROPAGACJI
BŁĘDU kanonicznego writera `pending_proposals_store.upsert_proposals`.

Defekt: przy powtórnym upsercie TEJ SAMEJ propozycji (retry / kolejny tick) writer
RE-STEMPLOWAŁ `sent_at`/`expires_at` (nie-idempotentny → wiek claimu czytany przez
`active_proposal_claims` zerował się co upsert), a wyjątek zapisu był POŁYKANY przez
blankietowy `except Exception: return 0` (ślad ginął).

Fix U ŹRÓDŁA za flagą `ENABLE_UPSERT_PROPOSALS_IDEMPOTENT` (default OFF, shadow-first):
* OFF = LEGACY bajt-parytet (re-stempel + połknięcie błędu) — zachowane 1:1.
* ON  = powtórny upsert tej samej propozycji (best.courier_id niezmieniony, wpis żywy
        po sweep) = NO-OP (sent_at nietknięty); zmiana kuriera/brak/wygaśnięcie =
        świeży wpis; błąd zapisu PROPAGUJE do (fail-soft) callera.

Ten plik = negatywny oracle (repro defektu) + mutation-detektor: usunięcie no-opu →
`test_on_idempotent_same_proposal_is_noop` RED; przywrócenie połykania błędu →
`test_on_propagates_write_error` RED. Import przez pakiet (pkgroot z conftest);
BEZ hardkodu live-scripts, żeby ładować kod z worktree pod ZIOMEK_SCRIPTS_ROOT.
"""
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import pending_proposals_store as S

_N = datetime(2026, 8, 2, 20, 0, 0, tzinfo=timezone.utc)
FLAG = "ENABLE_UPSERT_PROPOSALS_IDEMPOTENT"


def _rec(cid="A"):
    return {"verdict": "PROPOSE",
            "best": {"courier_id": cid, "plan": {"sequence": ["o1"]}},
            "auto_route": "ACK"}


def _set_flag(monkeypatch, value):
    """Steruj flagą przez patch `common.flag` (flaga NIE jest w live flags.json —
    default OFF; test steruje jawnie, deterministycznie)."""
    real = C.flag
    monkeypatch.setattr(
        C, "flag", lambda n, d=False: value if n == FLAG else real(n, d))


# ── FLAGA OFF = LEGACY (parytet z baseline) ──────────────────────────────────────
def test_off_default_is_legacy_restamp(tmp_path):
    """Flaga OFF (default; brak klucza w live flags.json): re-upsert TEJ SAMEJ
    propozycji RE-STEMPLUJE sent_at (historyczne, bajt-parytet)."""
    p = str(tmp_path / "pp.json")
    assert C.flag(FLAG, False) is False, "flaga NIE może być w live flags.json (zero live)"
    n1 = S.upsert_proposals([("o1", _rec("A"))], _N, path=p)
    t1 = json.load(open(p))["o1"]["sent_at"]
    n2 = S.upsert_proposals([("o1", _rec("A"))], _N + timedelta(minutes=5), path=p)
    t2 = json.load(open(p))["o1"]["sent_at"]
    assert n1 == 1 and n2 == 1                 # LEGACY liczy każdy upsert
    assert t1 != t2                             # RE-STEMPEL (nie-idempotentny)
    assert t2 == (_N + timedelta(minutes=5)).isoformat()


def test_off_swallows_write_error(tmp_path, monkeypatch):
    """Flaga OFF: błąd zapisu POŁKNIĘTY → 0 (fail-soft; parytet z baseline
    test_upsert_failsoft_returns_zero)."""
    _set_flag(monkeypatch, False)
    monkeypatch.setattr(
        S, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    n = S.upsert_proposals([("o1", _rec())], _N, path=str(tmp_path / "x.json"))
    assert n == 0


# ── FLAGA ON = IDEMPOTENTNY (oracle repro defektu) ───────────────────────────────
def test_on_idempotent_same_proposal_is_noop(tmp_path, monkeypatch):
    """ORACLE #1: flaga ON — re-upsert TEJ SAMEJ propozycji (ten sam kurier) = NO-OP:
    sent_at NIETKNIĘTY, 1 rekord (nie 2), drugi upsert zwraca 0.
    MUTACJA: usuń gałąź no-op (zawsze build_entry) → sent_at zmienia się → RED."""
    _set_flag(monkeypatch, True)
    p = str(tmp_path / "pp.json")
    n1 = S.upsert_proposals([("o1", _rec("A"))], _N, path=p)
    d1 = json.load(open(p))
    t1, exp1 = d1["o1"]["sent_at"], d1["o1"]["expires_at"]
    # retry / kolejny tick (późniejszy now), TA SAMA propozycja:
    n2 = S.upsert_proposals([("o1", _rec("A"))], _N + timedelta(minutes=3), path=p)
    d2 = json.load(open(p))
    assert n1 == 1
    assert n2 == 0                              # NO-OP → 0 realnie zapisanych
    assert set(d2.keys()) == {"o1"}             # 1 REKORD, nie duplikat
    assert d2["o1"]["sent_at"] == t1            # wiek claimu STABILNY
    assert d2["o1"]["expires_at"] == exp1       # cały wpis nietknięty


def test_on_changed_courier_rebuilds(tmp_path, monkeypatch):
    """ON: zmiana kuriera = INNA propozycja → świeży wpis (legalny reset zegara)."""
    _set_flag(monkeypatch, True)
    p = str(tmp_path / "pp.json")
    S.upsert_proposals([("o1", _rec("A"))], _N, path=p)
    t1 = json.load(open(p))["o1"]["sent_at"]
    n2 = S.upsert_proposals([("o1", _rec("B"))], _N + timedelta(minutes=3), path=p)
    d2 = json.load(open(p))
    assert n2 == 1
    assert d2["o1"]["decision_record"]["best"]["courier_id"] == "B"
    assert d2["o1"]["sent_at"] != t1


def test_on_expired_prev_rebuilds(tmp_path, monkeypatch):
    """ON: poprzedni wpis WYGASŁY (sweep go usuwa) → traktuj jak nowy → świeży sent_at."""
    _set_flag(monkeypatch, True)
    p = str(tmp_path / "pp.json")
    S.save({"o1": {"message_id": None,
                   "sent_at": (_N - timedelta(hours=1)).isoformat(),
                   "expires_at": (_N - timedelta(minutes=1)).isoformat(),
                   "decision_record": _rec("A")}}, p)
    n = S.upsert_proposals([("o1", _rec("A"))], _N, path=p)
    d = json.load(open(p))
    assert n == 1
    assert d["o1"]["sent_at"] == _N.isoformat()


def test_on_no_identity_rebuilds(tmp_path, monkeypatch):
    """ON: brak stabilnej tożsamości (best/courier puste) → NIE dedupuj (świeży wpis,
    zachowawczo — nigdy nie „przyklejaj" claimu bez pewnej tożsamości)."""
    _set_flag(monkeypatch, True)
    p = str(tmp_path / "pp.json")
    rec = {"verdict": "PROPOSE", "best": None}
    S.upsert_proposals([("o1", rec)], _N, path=p)
    t1 = json.load(open(p))["o1"]["sent_at"]
    n2 = S.upsert_proposals([("o1", rec)], _N + timedelta(minutes=1), path=p)
    assert n2 == 1
    assert json.load(open(p))["o1"]["sent_at"] != t1


def test_on_propagates_write_error(tmp_path, monkeypatch):
    """ORACLE #2: flaga ON — błąd zapisu PROPAGUJE (nie ginie po cichu).
    MUTACJA: owiń ścieżkę ON w `try/except: return 0` → nie podnosi → RED."""
    _set_flag(monkeypatch, True)
    monkeypatch.setattr(
        S, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        S.upsert_proposals([("o1", _rec())], _N, path=str(tmp_path / "x.json"))


# ── FLAGA ON ≠ OFF (kontrast bezpośredni) ────────────────────────────────────────
def test_flag_on_differs_from_off(tmp_path, monkeypatch):
    """ON≠OFF: identyczna sekwencja (2× ten sam upsert) → OFF re-stempluje i liczy
    (t1≠t2, n2=1), ON no-opuje (t1==t2, n2=0)."""
    def run(flag_value):
        _set_flag(monkeypatch, flag_value)
        p = str(tmp_path / f"pp_{flag_value}.json")
        S.upsert_proposals([("o1", _rec("A"))], _N, path=p)
        t1 = json.load(open(p))["o1"]["sent_at"]
        n2 = S.upsert_proposals([("o1", _rec("A"))], _N + timedelta(minutes=5), path=p)
        t2 = json.load(open(p))["o1"]["sent_at"]
        return t1, t2, n2
    off_t1, off_t2, off_n2 = run(False)
    on_t1, on_t2, on_n2 = run(True)
    assert off_t1 != off_t2 and off_n2 == 1     # OFF: re-stempel
    assert on_t1 == on_t2 and on_n2 == 0         # ON: no-op (idempotentny)


# ── WYŚCIG: współbieżny upsert tej samej propozycji (flaga ON) ───────────────────
def _idem_worker(path, oid, cid, now_iso):
    """Worker w OSOBNYM PROCESIE (realny fcntl cross-process). Ustawia flagę ON
    lokalnie (flaga nie jest w flags.json → subprocess musi ją włączyć sam)."""
    from datetime import datetime as _dt
    import dispatch_v2.common as _C
    import dispatch_v2.pending_proposals_store as _s
    _real = _C.flag
    _C.flag = lambda n, d=False: (
        True if n == "ENABLE_UPSERT_PROPOSALS_IDEMPOTENT" else _real(n, d))
    rec = {"verdict": "PROPOSE", "best": {"courier_id": cid}}
    return _s.upsert_proposals([(oid, rec)], _dt.fromisoformat(now_iso), path=path)


def test_on_concurrent_same_proposal_single_record(tmp_path):
    """WYŚCIG: 6 procesów upsertuje TĘ SAMĄ propozycję (flaga ON) pod LOCK_EX →
    dokładnie 1 rekord (zero duplikatów), a sumaryczny `written` == 1 (jeden proces
    realnie pisze, reszta widzi tę samą tożsamość → no-op). sent_at = pierwszego
    pisarza. Dowodzi: idempotencja HOLDuje także pod współbieżnością (lock serializuje
    RMW, dedup po best.courier_id eliminuje re-stempel)."""
    path = str(tmp_path / "pending_proposals.json")
    S.save({}, path)
    nows = [(_N + timedelta(seconds=i)).isoformat() for i in range(6)]
    with ProcessPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_idem_worker, path, "o1", "A", ni) for ni in nows]
        results = [f.result() for f in futs]
    final = S.load(path)
    assert set(final.keys()) == {"o1"}                 # 1 rekord, NIE duplikat
    assert final["o1"]["decision_record"]["best"]["courier_id"] == "A"
    assert sum(results) == 1                            # dokładnie jeden realny zapis
    assert final["o1"]["sent_at"] in nows              # sent_at pierwszego pisarza

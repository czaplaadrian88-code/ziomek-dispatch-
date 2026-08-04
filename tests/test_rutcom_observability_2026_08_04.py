"""Oracle obserwowalności ścieżki rutcom (04.08).

Blind V29 ustalił, że `committed_pickup_authority` i `coordinator_time_recheck`
nie emitowały ANI JEDNEJ linii operacyjnej — „zero błędów" znaczyło tam „zero
widoczności". Ten moduł pilnuje dwóch rzeczy naraz:

  1. że emisja ISTNIEJE w kluczowych punktach decyzyjnych (usunięcie linii albo
     dekoratora czerwieni te testy — to jest ratchet),
  2. że emisja jest CZYSTYM efektem ubocznym: wynik funkcji musi być bajtowo
     ten sam z logowaniem i bez, a awaria loggera nie może wywrócić decyzji.

Scenariusze APPLY/SUPPRESS są dokładnie te, na których stał negatywny oracle
recenzji auto-koorda i2 (czasówka, `status_id=1`, `decision_deadline`).
"""
from __future__ import annotations

import logging

import pytest

from dispatch_v2 import committed_pickup_authority as authority
from dispatch_v2 import coordinator_time_recheck as queue
from dispatch_v2.committed_pickup_authority import ResolutionOutcome
from tests.test_rutcom_committed_authority_491578 import (
    _seed_pending_initial_time_contract,
)

DEADLINE = "2099-08-02T18:51:00+02:00"
PRZED_DEADLINE = "2099-08-02T18:50:59.999999+02:00"


def _scenariusz(tmp_path, monkeypatch, oid: str):
    """Realna wisząca intencja: Rutcom nieaktywny + twardy decision_deadline."""
    _pw, sm, _original = _seed_pending_initial_time_contract(
        tmp_path,
        monkeypatch,
        oid=oid,
        payload_overrides={
            "prep_minutes": 431,
            "status_id": 1,
            "decision_deadline": DEADLINE,
        },
    )
    current = sm.get_order_strict(oid)
    return current, current["pending_committed_time_intent"]


# ------------------------------------------------------------ emisja istnieje

def test_suppress_emituje_linie_z_powodem(tmp_path, monkeypatch, caplog):
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-suppress")
    with caplog.at_level(logging.INFO, logger=authority.__name__):
        res = authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=PRZED_DEADLINE
        )
    assert res.outcome is ResolutionOutcome.SUPPRESS
    linie = [r.getMessage() for r in caplog.records]
    trafienia = [x for x in linie if "RUTCOM_AUTHORITY" in x]
    assert trafienia, "SUPPRESS nie zostawil sladu — to jest dokladnie defekt V29"
    assert any("outcome=SUPPRESS" in x for x in trafienia)
    assert any("reason=rutcom_status_not_active" in x for x in trafienia)
    assert any("oid=obs-suppress" in x for x in trafienia)


def test_apply_emituje_linie_z_powodem(tmp_path, monkeypatch, caplog):
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-apply")
    with caplog.at_level(logging.INFO, logger=authority.__name__):
        res = authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=DEADLINE
        )
    assert res.outcome is ResolutionOutcome.APPLY
    trafienia = [
        r.getMessage() for r in caplog.records if "RUTCOM_AUTHORITY" in r.getMessage()
    ]
    assert any("outcome=APPLY" in x for x in trafienia)
    assert any("reason=decision_deadline_declared" in x for x in trafienia)


def test_przekroczenie_deadline_i_bypass_sa_WARNING(tmp_path, monkeypatch, caplog):
    """Zdjęcie bramki statusu Rutcom nie może być cichym INFO."""
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-deadline")
    with caplog.at_level(logging.INFO, logger=authority.__name__):
        authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=DEADLINE
        )
    ostrzezenia = [
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("deadline_boundary_crossed" in x for x in ostrzezenia)
    assert any(
        "bypass=enforce_active_rutcom_status_off" in x for x in ostrzezenia
    )


def test_wszystkie_publiczne_wejscia_sa_obserwowane():
    """Ratchet: zdjęcie dekoratora z któregokolwiek wejścia = czerwone."""
    for nazwa in (
        "resolve_czasowka_assignment_ck",
        "resolve_czasowka_committed_observation",
        "resolve_czasowka_pickup_observation",
        "resolve_czasowka_initial_time_intent",
    ):
        fn = getattr(authority, nazwa)
        assert hasattr(fn, "__wrapped__"), (
            f"{nazwa} nie jest owiniete emisja — obserwowalnosc cofnieta"
        )


# ------------------------------------- emisja NIE zmienia wyniku (parytet)

@pytest.mark.parametrize("as_of", [PRZED_DEADLINE, DEADLINE, None])
def test_wynik_identyczny_z_logowaniem_i_bez(tmp_path, monkeypatch, as_of):
    """Parytet: wyłączenie logowania nie może zmienić werdyktu ani zdarzenia."""
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-parytet")

    logging.disable(logging.CRITICAL)
    try:
        bez = authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=as_of
        )
    finally:
        logging.disable(logging.NOTSET)
    z_logiem = authority.resolve_czasowka_initial_time_intent(
        current, intent, as_of=as_of
    )

    assert bez.outcome is z_logiem.outcome
    assert bez.reason == z_logiem.reason
    assert bez.event == z_logiem.event


def test_awaria_loggera_nie_wywraca_decyzji(tmp_path, monkeypatch):
    """Logger, który rzuca, nie może zabrać decyzji o czasie odbioru."""
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-awaria")
    oczekiwany = authority.resolve_czasowka_initial_time_intent(
        current, intent, as_of=DEADLINE
    )

    def _wybuch(*_args, **_kwargs):
        raise RuntimeError("logger padl")

    monkeypatch.setattr(authority._log, "info", _wybuch)
    monkeypatch.setattr(authority._log, "warning", _wybuch)
    faktyczny = authority.resolve_czasowka_initial_time_intent(
        current, intent, as_of=DEADLINE
    )
    assert faktyczny.outcome is oczekiwany.outcome
    assert faktyczny.reason == oczekiwany.reason
    assert faktyczny.event == oczekiwany.event


# ------------------------------------------------------------ fence / rollout

def test_emiter_fence_istnieje_i_jest_odporny(caplog):
    """`_log_fence` musi logować i nie może propagować awarii loggera."""
    with caplog.at_level(logging.INFO, logger=queue.__name__):
        queue._log_fence("release_start", fence_id="abc-123")
    assert any("RUTCOM_FENCE release_start" in r.getMessage() for r in caplog.records)
    assert any("fence_id=abc-123" in r.getMessage() for r in caplog.records)


def test_emiter_fence_polyka_awarie_loggera(monkeypatch):
    def _wybuch(*_args, **_kwargs):
        raise RuntimeError("logger padl")

    monkeypatch.setattr(queue._log, "info", _wybuch)
    queue._log_fence("release_ok", fence_id="abc-123")  # nie moze rzucic


def test_fence_acquire_i_release_maja_emisje_w_zrodle():
    """Ratchet na punktach, których nie da się wywołać hermetycznie tanio."""
    import inspect

    zrodlo_acquire = inspect.getsource(queue.acquire_forward_rollout_fence)
    zrodlo_release = inspect.getsource(queue.release_forward_rollout_fence)
    assert '_log_fence("acquire_ok"' in zrodlo_acquire
    assert '_log_fence("acquire_noop_existing"' in zrodlo_acquire
    assert '_log_fence("release_start"' in zrodlo_release
    assert '_log_fence("release_ok"' in zrodlo_release
    assert '_log_fence("release_noop"' in zrodlo_release


def test_cli_rollbacku_loguje_start_i_koniec(caplog):
    from dispatch_v2.tools import rutcom_committed_authority_rollback as cli

    with caplog.at_level(logging.INFO, logger=cli.__name__):
        cli._log_cli("start", command="forward-status")
        cli._log_cli("done", command="forward-status", exit_code=0)
    wiadomosci = [r.getMessage() for r in caplog.records]
    assert any("RUTCOM_ROLLBACK_CLI start" in x for x in wiadomosci)
    assert any("RUTCOM_ROLLBACK_CLI done" in x for x in wiadomosci)
    assert any("exit_code=0" in x for x in wiadomosci)


# ------------------------------------------------------------------ bez PII

def test_linie_nie_zawieraja_danych_klienta(tmp_path, monkeypatch, caplog):
    """order_id/CID wolno; adres, restauracja i uwagi — nie."""
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-pii")
    current = dict(current)
    current["delivery_address"] = "Testowa 7/3 Bialystok"
    current["restaurant"] = "Testowa Restauracja"
    current["uwagi"] = "10 pietro, kod 1234"
    with caplog.at_level(logging.INFO, logger=authority.__name__):
        authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=DEADLINE
        )
    zlaczone = "\n".join(r.getMessage() for r in caplog.records)
    for zakazane in ("Testowa 7/3", "Testowa Restauracja", "10 pietro", "1234"):
        assert zakazane not in zlaczone, f"PII wycieklo do logu: {zakazane}"

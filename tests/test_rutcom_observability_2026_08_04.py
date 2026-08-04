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
import os
import sys

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
    with caplog.at_level(logging.INFO, logger=authority._LOG_NAME):
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
    with caplog.at_level(logging.INFO, logger=authority._LOG_NAME):
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
    with caplog.at_level(logging.INFO, logger=authority._LOG_NAME):
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

    monkeypatch.setattr(authority._logger(), "info", _wybuch)
    monkeypatch.setattr(authority._logger(), "warning", _wybuch)
    faktyczny = authority.resolve_czasowka_initial_time_intent(
        current, intent, as_of=DEADLINE
    )
    assert faktyczny.outcome is oczekiwany.outcome
    assert faktyczny.reason == oczekiwany.reason
    assert faktyczny.event == oczekiwany.event


# ------------------------------------------------------------ fence / rollout

def test_emiter_fence_istnieje_i_jest_odporny(caplog):
    """`_log_fence` musi logować i nie może propagować awarii loggera."""
    with caplog.at_level(logging.INFO, logger=queue._log.name):
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

    with caplog.at_level(logging.INFO, logger=cli._log.name):
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
    with caplog.at_level(logging.INFO, logger=authority._LOG_NAME):
        authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=DEADLINE
        )
    zlaczone = "\n".join(r.getMessage() for r in caplog.records)
    for zakazane in ("Testowa 7/3", "Testowa Restauracja", "10 pietro", "1234"):
        assert zakazane not in zlaczone, f"PII wycieklo do logu: {zakazane}"


# =================================================== D-1: widoczność w demonie
# Drugi niezależny blind (04.08) obalił oracle iteracji 1: KAŻDY test emisji
# używał `caplog.at_level(..., logger=...)`, co samo wymusza poziom INFO i
# podpina handler pytesta. Dowodziło to wyłącznie „rekord powstaje, gdy ktoś
# wymusi INFO" — a nie „linia jest w logu operacyjnym". W realnym demonie root
# ma poziom WARNING i zero handlerów, więc 12 z 13 punktów emisji nie zostawiało
# NICZEGO, a jedyny WARNING uciekał przez `logging.lastResort` na stderr.
# Ten ratchet biegnie w OSOBNYM procesie bez markerów pytesta (root
# nieskonfigurowany, dokładnie jak w serwisie) i sprawdza treść PLIKU docelowego.
# Plik jest tymczasowy: `setup_logger` jest przechwycony i każdy cel przepisany
# na tmp, więc test nie może dotknąć żywego dispatch.log.

_KANONICZNY_LOG = "/root/.openclaw/workspace/scripts/logs/dispatch.log"

_DZIECKO_D1 = '''
import json
import logging
import os
import sys

sys.path.insert(0, os.environ["PKGROOT"])
cel = sys.argv[1]

from dispatch_v2 import common as C

_prawdziwy = C.setup_logger
zadane_sciezki = []


def _przekieruj(name, log_file=None):
    # KAZDY cel laduje w pliku tymczasowym — zywy log jest nieosiagalny.
    zadane_sciezki.append(log_file)
    return _prawdziwy(name, cel)


C.setup_logger = _przekieruj

from dispatch_v2 import committed_pickup_authority as A
from dispatch_v2 import coordinator_time_recheck as Q


def _logger_modulu(mod):
    # Dziala po OBU stronach: na masterze modul ma goly `_log`, na kandydacie
    # leniwy `_logger()`. Roznicuje nas ZACHOWANIE (czy linia trafia do pliku),
    # a nie ksztalt API — inaczej ratchet czerwienilby od kazdego refaktoru.
    fabryka = getattr(mod, "_logger", None)
    return fabryka() if callable(fabryka) else mod._log


root = logging.getLogger()
diag = {
    "root_handlers": len(root.handlers),
    "root_level": logging.getLevelName(root.level),
    "zadane_sciezki": zadane_sciezki,
}

# Emisja przez REALNE punkty: dekorator wejscia publicznego + emiter fence'u.
res = A.CommittedPickupResolution(
    outcome=A.ResolutionOutcome.SUPPRESS, reason="ratchet_probe", event=None
)
A._observed("ratchet_entry")(lambda existing: res)({"order_id": "ratchet-oid"})
Q._log_fence("ratchet_fence", fence_id="ratchet-fence-id")

diag["info_wlaczone_authority"] = _logger_modulu(A).isEnabledFor(logging.INFO)
diag["info_wlaczone_queue"] = _logger_modulu(Q).isEnabledFor(logging.INFO)
diag["zadane_sciezki"] = zadane_sciezki
logging.shutdown()
print(json.dumps(diag))
'''


def test_D1_linie_docieraja_do_pliku_w_konfiguracji_demona(tmp_path):
    """RATCHET D-1: rekord INFO realnie przechodzi do pliku docelowego.

    Czerwony na każdej wersji z bare `logging.getLogger` (m.in. na masterze
    6288e1ec9), bo wtedy moduł nie ma żadnego handlera i plik zostaje pusty.
    """
    import json
    import subprocess

    pkgroot = os.environ.get(
        "ZIOMEK_SCRIPTS_ROOT", "/root/.openclaw/workspace/scripts"
    )
    cel = tmp_path / "dispatch_ratchet.log"
    assert not str(cel).startswith("/root/.openclaw/workspace/scripts/logs")

    skrypt = tmp_path / "dziecko_d1.py"
    skrypt.write_text(_DZIECKO_D1)

    # Świeży proces BEZ markerów pytesta: root nieskonfigurowany, file-handler
    # nie jest wyciszany — czyli warunki demona (wzorzec z
    # test_setup_logger_test_hygiene.test_prod_without_markers_writes).
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in ("PYTEST_CURRENT_TEST", "DISPATCH_UNDER_PYTEST", "ALLOW_FILE_LOG_IN_TEST")
    }
    env["PKGROOT"] = pkgroot
    env["PYTHONPATH"] = pkgroot

    r = subprocess.run(
        [sys.executable, str(skrypt), str(cel)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    diag = json.loads(r.stdout.strip().splitlines()[-1])

    # 1. Proces JEST skonfigurowany jak demon — inaczej test nie dowodzi niczego.
    assert diag["root_handlers"] == 0, f"root ma handlery: {diag}"
    assert diag["root_level"] == "WARNING", diag

    # 2. ISTOTA RATCHETU: linia jest w PLIKU docelowym, bez żadnego caplog.
    #    To jest asercja, która czerwieni się na masterze 6288e1ec9.
    assert cel.exists(), (
        f"plik docelowy nie powstal — emisja martwa w demonie (D-1). diag={diag}"
    )
    tresc = cel.read_text()
    assert "RUTCOM_AUTHORITY entry=ratchet_entry" in tresc, tresc
    assert "reason=ratchet_probe" in tresc, tresc
    assert "RUTCOM_FENCE ratchet_fence" in tresc, tresc
    assert "fence_id=ratchet-fence-id" in tresc, tresc

    # 3. Oba moduły proszą o KANONICZNY cel (a nie o przypadkowy plik).
    assert diag["zadane_sciezki"], "zaden modul nie wywolal setup_logger — D-1 wrocil"
    assert set(diag["zadane_sciezki"]) == {_KANONICZNY_LOG}, diag["zadane_sciezki"]

    # 4. Poziom INFO faktycznie przechodzi (to było `False` w iteracji 1).
    assert diag["info_wlaczone_authority"] is True, diag
    assert diag["info_wlaczone_queue"] is True, diag


# ============================================ D-2: zagnieżdżenie rozróżnialne
# Jedno rozstrzygnięcie zostawiało 4 rekordy, w tym DWIE linie
# `SUPPRESS reason=rutcom_status_not_active` przy realnym werdykcie APPLY —
# dokładnie ten napis diagnozowano w pętli 03.08. Operator liczący wystąpienia
# dostawał wynik ~3x zawyżony. Linie pośrednie zostają (są diagnostyczne), ale
# niosą `depth`/`final`, a kontraktem dla operatora jest DOKŁADNIE jedna linia
# `final=yes` na rozstrzygnięcie.


def _werdykty(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if "RUTCOM_AUTHORITY entry=" in r.getMessage()
    ]


def test_D2_zagniezdzenie_daje_dokladnie_jeden_werdykt_finalny(
    tmp_path, monkeypatch, caplog
):
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-zagniezdzenie")
    with caplog.at_level(logging.INFO, logger=authority._LOG_NAME):
        res = authority.resolve_czasowka_initial_time_intent(
            current, intent, as_of=DEADLINE
        )
    assert res.outcome is ResolutionOutcome.APPLY

    werdykty = _werdykty(caplog)
    finalne = [x for x in werdykty if "final=yes" in x]
    posrednie = [x for x in werdykty if "final=no" in x]

    assert len(werdykty) == 3, werdykty
    assert len(finalne) == 1, finalne
    assert len(posrednie) == 2, posrednie

    # Finalna linia niesie PRAWDZIWY werdykt rozstrzygnięcia.
    assert "entry=initial_time_intent" in finalne[0]
    assert "outcome=APPLY" in finalne[0]
    assert "depth=0" in finalne[0]

    # Liczenie po `final=yes` nie zawyża powodu z incydentu 03.08...
    assert not [x for x in finalne if "rutcom_status_not_active" in x]
    # ...a linie pośrednie nadal go pokazują (diagnostyka nie została skasowana).
    assert len(
        [x for x in posrednie if "reason=rutcom_status_not_active" in x]
    ) == 2, posrednie
    assert all("depth=1" in x for x in posrednie), posrednie


def test_D2_wejscie_niezagniezdzone_jest_od_razu_finalne(
    tmp_path, monkeypatch, caplog
):
    """Płaskie wywołanie publicznego wejścia = jedna linia `depth=0 final=yes`."""
    current, _intent = _scenariusz(tmp_path, monkeypatch, "obs-plaskie")
    with caplog.at_level(logging.INFO, logger=authority._LOG_NAME):
        authority.resolve_czasowka_pickup_observation(
            current,
            {"pickup_at_warsaw": "2099-08-02T18:00:00+02:00", "source": "panel_re_check"},
            is_czasowka=True,
        )
    werdykty = _werdykty(caplog)
    assert len(werdykty) == 1, werdykty
    assert "depth=0" in werdykty[0] and "final=yes" in werdykty[0]


def test_D2_licznik_glebokosci_wraca_do_zera_po_rozstrzygnieciu(
    tmp_path, monkeypatch
):
    """Licznik jest thread-local i MUSI się domykać — inaczej `final` dryfuje."""
    current, intent = _scenariusz(tmp_path, monkeypatch, "obs-glebokosc")
    authority.resolve_czasowka_initial_time_intent(current, intent, as_of=DEADLINE)
    assert getattr(authority._entry_depth, "value", 0) == 0


# ================================== D-3: ratchety na regresje samego dekoratora
# Blind wykazał, że dwie najgroźniejsze mutacje dekoratora PRZEŻYWAŁY oracle
# iteracji 1: „połknięcie wyjątku owiniętej funkcji" i „zwrócenie kopii zamiast
# tego samego obiektu". Dzisiejszy kod jest poprawny — brakowało wyłącznie
# ratchetu, który utrwala oba kontrakty.


def _atrapa_werdyktu(reason: str = "probe"):
    return authority.CommittedPickupResolution(
        outcome=ResolutionOutcome.SUPPRESS, reason=reason, event=None
    )


def test_D3_dekorator_nie_polyka_wyjatku_owinietej_funkcji():
    """Mutacja „wrapper łapie wyjątek" MUSI czerwienić: wyjątek wychodzi TEN SAM."""
    wybuch = RuntimeError("regula padla")

    def _reguła(existing):
        raise wybuch

    owiniete = authority._observed("probe_wyjatek")(_reguła)
    with pytest.raises(RuntimeError) as zlapany:
        owiniete({"order_id": "probe-oid"})

    assert zlapany.value is wybuch, "wyjatek zostal podmieniony albo opakowany"
    # Emisja NIE może udawać werdyktu, którego nie było, a licznik ma się domknąć.
    assert getattr(authority._entry_depth, "value", 0) == 0


def test_D3_dekorator_zwraca_DOKLADNIE_ten_sam_obiekt():
    """Mutacja „wrapper zwraca kopię" MUSI czerwienić: liczy się tożsamość."""
    werdykt = _atrapa_werdyktu("probe_tozsamosc")
    owiniete = authority._observed("probe_tozsamosc")(lambda existing: werdykt)

    wynik = owiniete({"order_id": "probe-oid"})

    assert wynik is werdykt, "dekorator zwrocil INNY obiekt (kopie) — regresja"


def test_D3_emiter_nie_podmienia_werdyktu():
    """Ten sam kontrakt na poziomie samego emitera (`_log_resolution`)."""
    werdykt = _atrapa_werdyktu("probe_emiter")
    assert authority._log_resolution("probe", {"order_id": "x"}, werdykt) is werdykt

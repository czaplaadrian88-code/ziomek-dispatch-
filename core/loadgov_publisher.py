"""G5 — KANONICZNY PRODUCENT atomowego snapshotu load-governora.

Druga połowa kontraktu, którego konsumenta dostarczył WB2
(`core/loadgov_snapshot.py`). Powód istnienia jest wprost z RUN3-b sekcja 3:

    „`plan_recheck` nie może dostać aktualnego `loadgov_ewma` z obecnego
    mechanizmu: EWMA jest stanem pamięci `dispatch_pipeline`. Recompute
    w oneshocie stworzyłby drugi, niestabilny governor. Potrzebny jest JEDEN
    kanoniczny producer publikujący atomowy, wersjonowany snapshot."

Stąd trzy twarde własności tego modułu:

1. **Jeden producent.** Publikuje wyłącznie proces, który JAWNIE zgłosił się
   przez `claim_producer_role()` i którego rola zgadza się z
   `C.LOADGOV_SNAPSHOT_PRODUCER_ROLE`. `assess_order` biega też w procesach
   krótkożyjących (czasówka co minutę, plan-recheck, panel-quote) — tam EWMA
   po pierwszym ticku RÓWNA SIĘ próbce chwilowej, więc publikowanie stamtąd
   dałoby dokładnie tego „drugiego niestabilnego governora". Zgłoszenie jest
   związane z PID-em, żeby fork nie odziedziczył prawa do publikacji.

2. **Mianownik EQUAL-TREATMENT.** `eligible_couriers` liczy RÓWNO kurierów
   z GPS i bez. Seria legacy (`dispatch_pipeline._loadgov_compute`) dzieli
   przez `len(fleet_snapshot)`, a `courier_resolver.dispatchable_fleet`
   odrzuca z niej kurierów z `pos is None` (powód `no_position`) — kurier na
   zmianie, który nie nadaje GPS, WYPADA z mianownika, a jego zamówienia
   ZOSTAJĄ w liczniku. Bias idzie w stronę ZAWYŻENIA obciążenia, czyli
   w stronę poluzowania tolerancji okna — kierunek NIEBEZPIECZNY. Raport
   adopcji GPS z 27.07 pokazuje 5 kurierów bez ani jednego fixa w 7 dni, więc
   to nie jest defekt teoretyczny. Snapshot niesie obie liczby, żeby rozjazd
   był MIERZALNY, a nie domniemany.

3. **Fail-safe w stronę ostrzejszą.** Każdy powód, dla którego nie da się
   opublikować UCZCIWEGO snapshotu (brak świeżych statystyk floty, EWMA
   nierozgrzana, błąd zapisu), kończy się BRAKIEM pliku albo pozostawieniem
   poprzedniego, który sam wygaśnie. Konsument bez ważnego snapshotu bierze
   STRICT 5. Publikacja nigdy nie rzuca w stronę decyzji.

Zakres świadomie NIE obejmuje: Alarm certificate (OD-04 — bez niego ścieżka
loose w czytniku jest nieosiągalna, więc publikacja NIE MA DZIŚ WPŁYWU NA
ŻADNĄ DECYZJĘ) oraz unifikacji mianownika serii legacy (rekalibracja progów
2,7/3,5/3,0 = osobna bramka i ACK ownera).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from dispatch_v2 import common as _C
from dispatch_v2.core.loadgov_ewma import ewma_step
from dispatch_v2.core.loadgov_snapshot import SNAPSHOT_PATH

_log = logging.getLogger("dispatch.loadgov_publisher")

#: Kill-switch. NIEdecyzyjny: dopóki nie istnieje producent Alarm certificate,
#: `loadgov_snapshot.window_tol_min` zwraca strict niezależnie od EWMA, więc
#: obecność snapshotu nie zmienia ŻADNEJ decyzji (ratchet w testach). Poza
#: ETAP4_DECISION_FLAGS — precedens `ENABLE_LEX_WINDOW_LEDGER_V2` (WB1).
#: ⚠ W dniu, w którym powstanie producent Alarm certificate, flaga STAJE SIĘ
#: decyzyjna i MUSI przenieść się do ETAP4_DECISION_FLAGS.
FLAG_PUBLISH = "ENABLE_LOADGOV_SNAPSHOT_PUBLISH"

#: Wersja kontraktu. Zmiana kształtu = nowa wersja, nigdy cicha mutacja v1.
SCHEMA = "loadgov_snapshot.v1"

#: Stan serii EQUAL-TREATMENT (wyłączny właściciel — patrz `loadgov_ewma`).
_STATE: Dict[str, Any] = {"ts": None, "ewma": None, "samples": 0}
#: Stan publikacji (osobno od serii: EWMA aktualizuje się co próbkę, zapis jest
#: dławiony, więc mieszanie tych dwóch liczników zacierałoby ciągłość serii).
_PUB: Dict[str, Any] = {"last_at": None, "generation": 0}
#: Zgłoszenie roli producenta. `pid` chroni przed dziedziczeniem prawa po forku.
_PRODUCER: Dict[str, Any] = {"role": None, "pid": None, "started_at": None}

_CODE_FP: Optional[str] = None


def _sha16(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _cfg(name: str, default: Any) -> Any:
    """Próg/parametr: flags.json (hot-reload) → stała `common` → default.

    Ta sama kolejność co `core.lex_window_guards._thresholds` — strojenie
    producenta w cieniu ma iść przez `flags.json`, nie przez restart shadow.
    """
    try:
        return _C.load_flags().get(name, getattr(_C, name, default))
    except Exception:
        return getattr(_C, name, default)


def claim_producer_role(role: str, *, now: Optional[datetime] = None) -> None:
    """Zgłoś TEN proces kanonicznym producentem snapshotu.

    Wołane RAZ, na starcie długo żyjącej pętli silnika (`shadow_dispatcher.run`).
    Samo zgłoszenie niczego nie publikuje ani nie włącza — publikacja wymaga
    jeszcze zgodności `role` z konfiguracją i włączonego kill-switcha.
    """
    _PRODUCER["role"] = str(role)
    _PRODUCER["pid"] = os.getpid()
    _PRODUCER["started_at"] = now or datetime.now(timezone.utc)
    _log.info("loadgov snapshot: zgłoszono rolę producenta role=%s pid=%s",
              role, _PRODUCER["pid"])


def reset_state() -> None:
    """Wyzeruj serię, licznik publikacji i zgłoszenie roli (testy)."""
    _STATE.update(ts=None, ewma=None, samples=0)
    _PUB.update(last_at=None, generation=0)
    _PRODUCER.update(role=None, pid=None, started_at=None)


def state_snapshot() -> Dict[str, Any]:
    """Migawka stanu producenta — diagnostyka i testy, bez efektów ubocznych."""
    return {"series": dict(_STATE), "publication": dict(_PUB),
            "producer": dict(_PRODUCER)}


def is_producer() -> bool:
    """Czy TEN proces jest kanonicznym producentem (rola + zgodny PID).

    Rola czytana WYŁĄCZNIE ze stałej `common` — to tożsamość procesu, nie
    pokrętło strojenia, więc świadomie nie ma jej w `flags.json`: hot-reload
    mógłby w środku pracy przenieść prawo do publikacji na inny proces.
    """
    role = _PRODUCER.get("role")
    if not role or _PRODUCER.get("pid") != os.getpid():
        return False
    return str(role) == str(getattr(_C, "LOADGOV_SNAPSHOT_PRODUCER_ROLE",
                                    "dispatch-shadow"))


def enabled() -> bool:
    """Czy `observe()` ma w ogóle co robić w TYM procesie.

    Tania bramka dla call-site'u w `dispatch_pipeline`: gdy producent jest
    bezczynny (kill-switch OFF albo to nie ta rola), ścieżka decyzji nie
    buduje nawet argumentów ani wpisu w buforze efektów. `observe()` i tak
    sprawdza jedno i drugie po swojej stronie — bramka jest optymalizacją,
    nie jedynym zabezpieczeniem.
    """
    try:
        return bool(_C.decision_flag(FLAG_PUBLISH)) and is_producer()
    except Exception:
        return False


def _code_fingerprint() -> str:
    """Odcisk ŹRÓDŁA rachunku loadgov — wykrywa zmianę kodu bez zmiany flag.

    Obejmuje jądro EWMA, złożenie snapshotu i (o ile moduł jest już
    zaimportowany) serię legacy. Sięgamy po `sys.modules`, a nie po import,
    bo `dispatch_pipeline` importuje TEN moduł — import zwrotny byłby cyklem.
    """
    global _CODE_FP
    if _CODE_FP is None:
        try:
            import inspect
            import sys

            parts = [inspect.getsource(ewma_step), inspect.getsource(_build_snapshot)]
            _dp = sys.modules.get("dispatch_v2.dispatch_pipeline")
            if _dp is not None and hasattr(_dp, "_loadgov_compute"):
                parts.append(inspect.getsource(_dp._loadgov_compute))
            _CODE_FP = _sha16("\n".join(parts))
        except Exception:
            _CODE_FP = ""
    return _CODE_FP


def _effective_ttl_s() -> float:
    """TTL snapshotu, przycięty od dołu do 2× okresu publikacji.

    Wymaganie spójności: `valid_until` MUSI przeżyć przerwę między dwoma
    zapisami zdrowego producenta, inaczej konsument widziałby `expired`
    w środku normalnej pracy i degradował do strict bez powodu. Ktoś, kto
    ustawi w `flags.json` TTL krótszy niż dławienie, dostaje przycięcie
    i ostrzeżenie, a nie cichą dziurę.
    """
    ttl = float(_cfg("LOADGOV_SNAPSHOT_TTL_S", 180.0))
    period = float(_cfg("LOADGOV_SNAPSHOT_MIN_INTERVAL_S", 30.0))
    floor = 2.0 * max(0.0, period)
    if ttl < floor:
        _log.warning("loadgov snapshot: TTL %.1fs < 2× okres publikacji %.1fs "
                     "— przycinam do %.1fs", ttl, period, floor)
        return floor
    return ttl


def _fleet_gap() -> Optional[Dict[str, Any]]:
    """Liczba kurierów odrzuconych z puli WYŁĄCZNIE za brak pozycji.

    Źródłem jest `courier_resolver`, bo to ON jest kanonicznym właścicielem
    decyzji „kto jest dostępny" — my tylko czytamy liczbę, którą on i tak już
    policzył. Statystyka musi pochodzić z TEGO procesu i z bieżącego ticku;
    inaczej mianownik pochodziłby z innego stanu świata niż licznik i
    zwracamy None (⇒ nie publikujemy).
    """
    try:
        from dispatch_v2 import courier_resolver as _cr
        stats = _cr.last_fleet_filter_stats()
    except Exception as exc:
        _log.warning("loadgov snapshot: brak statystyk puli: %s: %s",
                     type(exc).__name__, exc)
        return None
    if not stats or stats.get("pid") != os.getpid():
        return None
    return stats


def _build_snapshot(*, now: datetime, ewma: float, load_now: float,
                    active_orders: int, eligible_couriers: int,
                    couriers_dispatchable: int, couriers_no_position: int,
                    fleet_stats_age_s: float, legacy_ewma: Optional[float],
                    generation: int, ttl_s: float) -> Dict[str, Any]:
    """Złóż ciało snapshotu. Czyste — całe I/O jest w `_publish`."""
    flag_fp = ""
    try:
        flag_fp = _C.flag_fingerprint()
    except Exception:
        pass
    flag_fp_sha = _sha16(flag_fp) if flag_fp else ""
    code_fp = _code_fingerprint()
    return {
        "schema": SCHEMA,
        # ── rdzeń kontraktu (REQUIRED_FIELDS czytnika) ──
        "ewma": ewma,
        "observed_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=ttl_s)).isoformat(),
        "generation": generation,
        "fingerprint": _sha16(f"{flag_fp_sha}|{code_fp}"),
        # ── z czego policzone (RUN3-b sekcja 3) ──
        "active_orders": active_orders,
        "eligible_couriers": eligible_couriers,
        "load_now": load_now,
        "denominator_basis": "equal_treatment",
        # ── kto i czym policzył ──
        "producer_role": _PRODUCER.get("role"),
        "producer_pid": _PRODUCER.get("pid"),
        "producer_started_at": (_PRODUCER["started_at"].isoformat()
                                if _PRODUCER.get("started_at") else None),
        "flag_fingerprint_sha": flag_fp_sha,
        "code_fingerprint": code_fp,
        # ── jakość pomiaru ──
        "ewma_samples": _STATE["samples"],
        "ewma_tau_min": float(_cfg("LOADGOV_EWMA_TAU_MIN", 15.0)),
        "ttl_s": ttl_s,
        "fleet_stats_age_s": round(fleet_stats_age_s, 1),
        # ── rozjazd equal-treatment vs seria legacy (materiał do decyzji ownera
        #    o unifikacji mianownika; NIE jest wejściem żadnej polityki) ──
        "couriers_dispatchable": couriers_dispatchable,
        "couriers_no_position": couriers_no_position,
        "legacy_ewma": legacy_ewma,
    }


def _publish(snapshot: Dict[str, Any], path: str) -> None:
    """Zapis ATOMOWY: temp w katalogu docelowym → fsync → rename → fsync dir.

    Rename w obrębie jednego katalogu jest na POSIX atomowy, więc czytelnik
    nigdy nie zobaczy pliku częściowego — widzi albo poprzedni snapshot, albo
    kompletny nowy. `fsync` pliku i katalogu domyka to na wypadek utraty
    zasilania: bez nich rename mógłby przetrwać restart, wskazując na treść,
    której nigdy nie utrwalono.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".loadgov_snapshot_",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def observe(*, now: datetime, active_orders: Optional[int],
            couriers_dispatchable: int, legacy_ewma: Optional[float],
            path: Optional[str] = None) -> str:
    """Przyjmij próbkę obciążenia i — jeśli wolno — opublikuj snapshot.

    Wołane z `dispatch_pipeline` DOKŁADNIE tam, gdzie aktualizuje się seria
    legacy: to jedyny moment, w którym istnieje świeży licznik i mianownik
    z tego samego stanu świata. Zwraca powód decyzji (string) — do logu,
    testów i ewentualnej telemetrii; NIGDY nie rzuca.

    Powody: `flag_off`, `not_producer`, `no_orders`, `no_fleet_stats`,
    `stale_fleet_stats`, `no_eligible`, `warmup`, `throttled`, `write_error`,
    `error`, `published`.
    """
    try:
        if not _C.decision_flag(FLAG_PUBLISH):
            return "flag_off"
        if not is_producer():
            return "not_producer"
        if active_orders is None:
            return "no_orders"

        stats = _fleet_gap()
        if stats is None:
            return "no_fleet_stats"
        computed_at = stats.get("computed_at")
        if not isinstance(computed_at, datetime):
            return "no_fleet_stats"
        age_s = (now - computed_at).total_seconds()
        max_age = float(_cfg("LOADGOV_FLEET_STATS_MAX_AGE_S", 120.0))
        if age_s < 0 or age_s > max_age:
            return "stale_fleet_stats"

        no_position = int(stats.get("no_position") or 0)
        eligible = int(couriers_dispatchable) + no_position
        if eligible <= 0:
            return "no_eligible"

        load_now = round(int(active_orders) / eligible, 3)
        tau = float(_cfg("LOADGOV_EWMA_TAU_MIN", 15.0))
        ewma = ewma_step(_STATE["ewma"], _STATE["ts"], load_now, now, tau)
        _STATE.update(ts=now, ewma=ewma, samples=int(_STATE["samples"]) + 1)

        # Rozgrzewka: po JEDNEJ próbce „EWMA" jest dosłownie obciążeniem
        # chwilowym. Publikowanie tego jako wygładzonej miary byłoby tym samym
        # kłamstwem, przed którym broni zakaz recompute w oneshocie.
        min_samples = int(_cfg("LOADGOV_SNAPSHOT_MIN_SAMPLES", 2))
        if _STATE["samples"] < min_samples:
            return "warmup"

        # Dławienie ZAPISU. Warunek jest jawnie jednostronny: przy cofniętym
        # zegarze (ujemna różnica) publikujemy, zamiast zablokować producenta
        # na czas skoku wstecz — pusty snapshot jest gorszy niż jeden zapis
        # więcej.
        last_at = _PUB.get("last_at")
        period = float(_cfg("LOADGOV_SNAPSHOT_MIN_INTERVAL_S", 30.0))
        if last_at is not None and 0 <= (now - last_at).total_seconds() < period:
            return "throttled"

        ttl_s = _effective_ttl_s()
        snapshot = _build_snapshot(
            now=now, ewma=ewma, load_now=load_now,
            active_orders=int(active_orders), eligible_couriers=eligible,
            couriers_dispatchable=int(couriers_dispatchable),
            couriers_no_position=no_position, fleet_stats_age_s=age_s,
            legacy_ewma=legacy_ewma, generation=int(_PUB["generation"]) + 1,
            ttl_s=ttl_s)
        try:
            _publish(snapshot, path or SNAPSHOT_PATH)
        except Exception as exc:
            # Nieudany zapis NIE cofa serii (próbka była prawdziwa) i NIE
            # przesuwa licznika generacji — kolejny tick spróbuje ponownie.
            _log.warning("loadgov snapshot: zapis nieudany: %s: %s",
                         type(exc).__name__, exc)
            return "write_error"
        _PUB.update(last_at=now, generation=int(_PUB["generation"]) + 1)
        return "published"
    except Exception as exc:  # fail-safe: publikacja nigdy nie dotyka decyzji
        _log.warning("loadgov snapshot: publikacja pominięta: %s: %s",
                     type(exc).__name__, exc)
        return "error"

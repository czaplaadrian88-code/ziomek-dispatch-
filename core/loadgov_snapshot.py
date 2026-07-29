"""G5 — czytnik atomowego snapshotu load-governora (WB2, STRICT-STUB).

Zakres świadomie WĄSKI. Sol RUN3-b (sekcja 3, G5) wykazał, że `plan_recheck`
NIE MOŻE dziś poznać aktualnego `loadgov_ewma`: EWMA jest stanem PAMIĘCI procesu
`dispatch_pipeline` (`common._LOADGOV_STATE`), a wyliczanie jej po raz drugi w
innym procesie tworzyłoby drugiego writera tej samej prawdy — dokładnie wzorzec
zakazany przez „NAPRAWA U ŹRÓDŁA".

Dlatego tu jest wyłącznie KONSUMENT kontraktu:

  * kanoniczny PRODUCENT snapshotu = OSOBNE zadanie (poza WB2, wymaga własnej
    bramki i ACK). Dopóki nie istnieje, `read_snapshot()` zwraca `None`;
  * `None` albo snapshot przeterminowany ⇒ tolerancja okna STRICT
    (`OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN` = 5). Nigdy loose przez pomyłkę —
    kierunek degradacji jest zawsze w stronę ostrzejszą;
  * tolerancja LOOSE (10) wymaga wg OD-04 kanonicznego **Alarm certificate**,
    a nie samego wysokiego EWMA. Certyfikatu nie ma → ścieżka loose jest
    nieosiągalna i tak zostaje do osobnej decyzji ownera.

Ratchet (test WB2): dopóki `alarm_certificate` nie istnieje, `window_tol_min()`
NIE MOŻE zwrócić wartości innej niż strict — mutacja poluzowująca to czerwieni
`tests/test_wb2_conditional_guards.py`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from dispatch_v2 import common as _C

_log = logging.getLogger("dispatch.loadgov_snapshot")

#: Ścieżka kontraktowa. Plik NIE JEST dziś przez nic produkowany (patrz docstring).
SNAPSHOT_PATH = str(_C.STATE_DIR / "loadgov_snapshot.json")

#: Pola wymagane kontraktem RUN3-b sekcja 3 (G5). Brak któregokolwiek =
#: snapshot nieważny; kontrakt jest all-or-none, żeby częściowy plik nigdy nie
#: uchodził za świeży pomiar.
REQUIRED_FIELDS = ("ewma", "observed_at", "valid_until", "generation", "fingerprint")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def read_snapshot(now: datetime,
                  path: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Zwróć `(snapshot|None, meta)`. NIGDY nie rzuca.

    `meta` trafia 1:1 do sekcji `loadgov` ledgera v2 (pola już są w schemacie):
    `source`, `age_s`, `fingerprint`, `ewma`, `observed_at`, `valid_until`,
    `generation`. `source` mówi, DLACZEGO snapshot jest albo go nie ma:
    `absent` / `unreadable` / `incomplete` / `expired` / `snapshot`.
    """
    meta: Dict[str, Any] = {k: None for k in
                            ("source", "age_s", "fingerprint", "ewma",
                             "observed_at", "valid_until", "generation")}
    try:
        with open(path or SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        meta["source"] = "absent"
        return None, meta
    except Exception as exc:
        meta["source"] = "unreadable"
        _log.warning("loadgov snapshot nieczytelny: %s: %s", type(exc).__name__, exc)
        return None, meta

    if not isinstance(data, dict) or any(data.get(f) is None for f in REQUIRED_FIELDS):
        meta["source"] = "incomplete"
        return None, meta

    observed = _parse_ts(data.get("observed_at"))
    valid_until = _parse_ts(data.get("valid_until"))
    meta.update({
        "fingerprint": data.get("fingerprint"),
        "ewma": data.get("ewma"),
        "observed_at": data.get("observed_at"),
        "valid_until": data.get("valid_until"),
        "generation": data.get("generation"),
        "age_s": (None if observed is None
                  else round((now - observed).total_seconds(), 1)),
    })
    if valid_until is None or observed is None or now > valid_until:
        meta["source"] = "expired"
        return None, meta
    meta["source"] = "snapshot"
    return data, meta


def window_tol_min(now: datetime, *, snapshot: Optional[Dict[str, Any]] = None,
                   alarm_certificate: Optional[Dict[str, Any]] = None,
                   alarm_candidates=None,
                   strategy2_probe: Optional[Dict[str, Any]] = None) -> Tuple[float, str]:
    """Efektywna tolerancja okna odbioru + powód. Dziś ZAWSZE strict.

    Loose (10) wymaga JEDNOCZEŚNIE ważnego snapshotu z EWMA ≥ progu ORAZ
    kanonicznego Alarm certificate (OD-04). Certyfikatu nie produkuje dziś
    żadna warstwa, więc jedyną osiągalną gałęzią jest strict — i tak ma
    zostać do osobnej decyzji ownera.
    """
    strict = float(getattr(_C, "OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN", 5.0))
    loose = float(getattr(_C, "OBJ_COMMITTED_PICKUP_TOL_LOOSE_MIN", 10.0))
    threshold = float(getattr(_C, "OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD", 4.5))
    if snapshot is None:
        return strict, "strict_no_snapshot"
    if not alarm_certified(
        alarm_certificate,
        candidates=alarm_candidates,
        strategy2_probe=strategy2_probe,
    ):
        # EWMA ani dowolny dict NIE uprawniają do poluzowania (OD-04).
        # Ta sama walidacja kontrfaktu otwiera carry-cap i tolerancję okna.
        return strict, "strict_no_alarm_certificate"
    try:
        ewma = float(snapshot.get("ewma"))
    except (TypeError, ValueError):
        return strict, "strict_bad_ewma"
    if ewma < threshold:
        return strict, "strict_below_threshold"
    return loose, "loose_alarm_certified"


def alarm_certified(
    alarm_certificate: Optional[Dict[str, Any]] = None,
    *,
    candidates=None,
    strategy2_probe: Optional[Dict[str, Any]] = None,
) -> bool:
    """Czy zachodzi kanoniczny Alarm (jedyna przesłanka capa 40 zamiast 35).

    Osobna funkcja, bo cap świeżości i tolerancja okna to DWIE różne polityki
    o WSPÓLNEJ przesłance — a przesłanka ma mieć jedno miejsce w kodzie.
    """
    if alarm_certificate is None:
        return False
    try:
        from dispatch_v2.core import alarm_certificate as _alarm
        return _alarm.is_alarm(
            alarm_certificate,
            candidates=candidates,
            strategy2_probe=strategy2_probe,
        )
    except Exception:
        return False

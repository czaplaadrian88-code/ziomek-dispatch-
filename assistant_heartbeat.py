"""Puls Ziomka dla asystenta operacyjnego (Faza 1).

Zapisuje atomowo `dispatch_state/ziomek_heartbeat.json` na każdym HEARTBEAT ticku
shadow_dispatchera (~60s). Czytany przez:
  - assistant-watcher (alert R1 „Ziomek milczy >3 min" + mirror do tabeli PG),
  - narzędzie get_ziomek_status (asystent konwersacyjny).

ZASADA: ten moduł NIGDY nie rzuca wyjątku do pętli dispatchu. Każdy błąd =
log+return. Dispatch jest mózgiem produkcyjnym — heartbeat to dodatek obserwacyjny,
nie wolno mu wywrócić głównej pętli (analogicznie do GPS-01 / V328 hooków).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import types
from datetime import datetime, timezone

HEARTBEAT_PATH = os.getenv(
    "ZIOMEK_HEARTBEAT_PATH",
    "/root/.openclaw/workspace/dispatch_state/ziomek_heartbeat.json",
)
_RELEASE_ID_DEFAULT = "V3.28"
_RELEASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")


def _release_id_from_env() -> str:
    """Odczytaj bezpieczną etykietę pulsu albo jawnie użyj fallbacku.

    Nie logujemy surowej wartości env: log ma potwierdzić fallback, a nie
    przenosić potencjalnie wstrzyknięty tekst do telemetrii operatora.
    """
    raw = os.environ.get("ZIOMEK_MODEL_VERSION")
    if raw is None:
        reason = "unset"
    elif not raw.strip():
        reason = "blank"
    elif not _RELEASE_ID_PATTERN.fullmatch(raw):
        reason = "invalid"
    else:
        return raw
    logging.getLogger(__name__).warning(
        "release_id fallback: ZIOMEK_MODEL_VERSION %s; using built-in default",
        reason,
    )
    return _RELEASE_ID_DEFAULT


# release_id / etykieta wersji-generacji silnika: USTALONA RAZ z env
# ``ZIOMEK_MODEL_VERSION`` przy starcie procesu (imporcie modułu) i IMMUTABLE.
# Override wyłącznie przez env przy starcie/restarcie (bump wersji) — próba
# nadpisania w runtime jest ODRZUCANA (patrz ``_ReleaseIdImmutableModule`` niżej).
# Pole jest konsumowane WYŁĄCZNIE przez telemetrię pulsu; nie uczestniczy w
# decyzjach dispatchu ani w atestacji wydania.
MODEL_VERSION = _release_id_from_env()

# Token przeżywa świadomy importlib.reload i pozwala starej klasie modułu
# zaakceptować wyłącznie nową generację tej samej straży. Nie jest sekretem.
try:
    _RELEASE_CLASS_TOKEN
except NameError:
    _RELEASE_CLASS_TOKEN = object()


class _ReleaseIdImmutableModule(types.ModuleType):
    """Zatrzask immutability release_id (``MODEL_VERSION``) na poziomie modułu.

    ``MODEL_VERSION`` jest czytany raz z env przy imporcie (starcie procesu). Ten
    zatrzask ODRZUCA każdą próbę nadpisania go w runtime (przypisanie atrybutu do
    modułu, monkeypatch, przypadkowy zapis) INNĄ wartością — podnosi ``AttributeError``.
    Przypisanie identycznej wartości jest idempotentnym no-op. Pozostałe atrybuty
    modułu (np. ścieżki nadpisywane w testach) zostają zapisywalne — harness nietknięty.
    """

    _release_class_token = _RELEASE_CLASS_TOKEN
    _release_id = MODEL_VERSION

    @property
    def MODEL_VERSION(self) -> str:  # noqa: N802 — publiczny kontrakt legacy
        """Zatrzaśnięta wartość; deskryptor ma pierwszeństwo przed module dict."""
        return type(self)._release_id

    def __setattr__(self, name: str, value: object) -> None:
        if name == "MODEL_VERSION":
            current = type(self)._release_id
            if value != current:
                raise AttributeError(
                    "MODEL_VERSION (release_id) jest immutable — ustalony z env "
                    "ZIOMEK_MODEL_VERSION przy starcie procesu; runtime-mutacja odrzucona"
                )
            return
        if name == "__class__":
            current_class = type(self)
            is_guard_reload = (
                isinstance(value, type)
                and value.__module__ == current_class.__module__
                and value.__name__ == current_class.__name__
                and getattr(value, "_release_class_token", None)
                is current_class._release_class_token
            )
            if value is not current_class and not is_guard_reload:
                raise AttributeError(
                    "klasa modułu chroni immutable release_id; runtime-podmiana odrzucona"
                )
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"MODEL_VERSION", "__class__"}:
            raise AttributeError(f"{name} jest chroniony przed usunięciem w runtime")
        super().__delattr__(name)


# Zatrzask klasy modułu (idempotentny przy importlib.reload — patrz oracle A-5).
sys.modules[__name__].__class__ = _ReleaseIdImmutableModule
# Publiczna wartość jest od tej chwili własnością deskryptora klasy. Usunięcie
# surowego wpisu zamyka drugi, konkurencyjny writer w module dict. Bezpośredni
# zapis do dict może utworzyć wyłącznie martwy cień: deskryptor oraz emitter go
# ignorują. Pozostawienie prawdziwego dict zachowuje świadomy importlib.reload.
globals().pop("MODEL_VERSION", None)


def write_heartbeat(
    *,
    optimizer_running: bool,
    queue_depth: int,
    processed: int,
    failed: int,
    worker_alive: bool,
    fallback_active: bool,
    avg_assignment_ms: int | None = None,
    active_orders: int | None = None,
    active_couriers: int | None = None,
    pending_reoptimization: int = 0,
    logger=None,
) -> None:
    """Atomowy, fail-safe zapis pulsu. active_orders/active_couriers domyślnie None —
    wzbogaca je watcher przy mirrorze do PG (czyta orders_state niezależnie)."""
    try:
        payload = {
            "optimizer_running": bool(optimizer_running),
            "last_run": datetime.now(timezone.utc).isoformat(),
            "active_orders": active_orders,
            "active_couriers": active_couriers,
            "avg_assignment_ms": avg_assignment_ms,
            "pending_reoptimization": int(pending_reoptimization or 0),
            "model_version": sys.modules[__name__].MODEL_VERSION,
            "fallback_active": bool(fallback_active),
            "queue_depth": int(queue_depth or 0),
            "worker_alive": bool(worker_alive),
            "processed_total": int(processed or 0),
            "failed_total": int(failed or 0),
        }
        directory = os.path.dirname(HEARTBEAT_PATH)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hb_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, HEARTBEAT_PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001 — fail-safe boundary, nigdy nie propaguj
        if logger is not None:
            try:
                logger.warning(
                    f"assistant_heartbeat write fail "
                    f"({type(exc).__name__}: {exc}) — pominięto"
                )
            except Exception:
                pass

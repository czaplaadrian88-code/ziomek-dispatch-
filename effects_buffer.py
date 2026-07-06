"""effects_buffer — K08 programu refaktoru (2026-07-06, ADR-R02 „powłoka efektów").

Cel: efekty uboczne decyzji (append shadow-jsonli, zapis stanu alertów loadgov,
wysyłka alertu Telegram) wykonywane PO policzeniu decyzji, nie w jej środku.
Decyzja staje się czystsza (zero zapisów/sieci między kandydatami), a efekty
zachowują DOKŁADNIE tę samą treść — flush woła TE SAME helpery z TYMI SAMYMI
argumentami (zero kopii logiki zapisu; helper przy zdjętej aktywności bufora
wykonuje swój oryginalny kod).

Mechanika (wzorzec rekordera OSRM z K04): bufor proces-globalny pod lockiem —
pula wątków kandydatów NIE dziedziczy contextvarów, a `assess_order` biegnie
sekwencyjnie per decyzja, więc okno begin→flush = dokładnie jedna decyzja.
Kolejność flusha = FIFO zgłoszeń (kolejność linii W OBRĘBIE decyzji może się
różnić od legacy wyścigu wątków — dopuszczalne per plan K08; treść linii 1:1).

Gate: ENABLE_EFFECTS_AFTER_DECISION (ETAP4; brak klucza = OFF → begin() zwraca
False, divert() zawsze False → helpery piszą jak dotąd, bajt-parytet 1:1).
Fail-soft totalny: awaria bufora nigdy nie psuje decyzji ani efektów
(divert False → caller wykonuje oryginał; flush per-wpis w try/except —
helpery i tak są fail-soft same w sobie).

Wyjątek decyzji: caller (assess_order) flushuje w finally — zbuforowane efekty
sprzed wyjątku wykonują się jak w legacy (tam zapis zdążył się wydarzyć).
"""
from __future__ import annotations

import threading

from dispatch_v2 import common as C

_LOCK = threading.Lock()
_ACTIVE = False
_Q: list = []
_MAX_EFFECTS = 10000  # backstop na runaway (żadna decyzja nie generuje tylu efektów)


def begin() -> bool:
    """Start okna buforowania dla JEDNEJ decyzji. Zwraca True gdy flaga ON.
    Wołać wyłącznie z assess_order (sekwencyjnie); flush ZAWSZE w finally."""
    global _ACTIVE
    try:
        on = bool(C.flag("ENABLE_EFFECTS_AFTER_DECISION",
                         getattr(C, "ENABLE_EFFECTS_AFTER_DECISION", False)))
    except Exception:
        on = False
    with _LOCK:
        _Q.clear()
        _ACTIVE = on
    return on


def divert(fn, *args, **kwargs) -> bool:
    """Zbuforuj efekt (fn+argumenty) zamiast wykonać. True = zbuforowano
    (caller NIE wykonuje oryginału); False = bufor nieaktywny/pełny/awaria
    (caller wykonuje oryginał jak dotąd)."""
    if not _ACTIVE:
        return False
    try:
        with _LOCK:
            if _ACTIVE and len(_Q) < _MAX_EFFECTS:
                _Q.append((fn, args, kwargs))
                return True
    except Exception:
        pass
    return False


def flush() -> int:
    """Wykonaj zbuforowane efekty (FIFO) i zamknij okno. Zwraca liczbę
    wykonanych. Aktywność zdejmowana PRZED wykonaniem — helpery wołane z
    flusha przechodzą swoją oryginalną ścieżką zapisu (brak rekurencji)."""
    global _ACTIVE
    with _LOCK:
        _ACTIVE = False
        pending = list(_Q)
        _Q.clear()
    done = 0
    for fn, args, kwargs in pending:
        try:
            fn(*args, **kwargs)
            done += 1
        except Exception:
            # helpery są fail-soft; podwójny pas bezpieczeństwa — jeden zepsuty
            # efekt nie może zatrzymać pozostałych ani decyzji
            pass
    return done

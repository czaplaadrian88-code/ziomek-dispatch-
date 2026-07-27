"""JEDNA metryka świeżości niesionego jedzenia (handoff/carry) — WB2, CZASY 492.

Wymóg 13.2 p.3 diagnozy (`/root/handover/CZASY_INCYDENT_492_DIAGNOZA_2026-07-27.md`,
zbieżny werdykt 3 recenzentów): w jednym punkcie decyzyjnym żyły CZTERY polityki
świeżości liczone na RÓŻNYCH zegarach:

  * `CARRIED_FIRST_RELAX_SOFT_MAX_MIN` = 20 (relax L4),
  * `carry_cap = max(35.0, bcarry)` w `_lex_committed_window_reorder` (L5) —
    liczone na czasie PRZYJAZDU (przed dwell),
  * `O2_CAPZ_Z_MIN` = 20 w `route_simulator_v2._capz_reseq_plan` —
    liczone na `predicted_delivered_at`, czyli PO dwellu,
  * `BAG_TIME_HARD_MAX_MIN` = 35 (R6 / pin).

Rozjazd „przed dwell" vs „po dwell" to nie kosmetyka: dropoff dwell wynosi
domyślnie 3,5 min, czyli DOKŁADNIE tyle, ile wynosi cały budżet tolerancji
delty (D2: 3 min). Dwie warstwy porównujące „to samo" na zegarach różniących
się o więcej niż ich własna tolerancja nie są dwiema tolerancjami — to jeden
błąd rachunkowy w dwóch egzemplarzach.

Ten moduł jest KANONICZNYM właścicielem definicji. Nie ustanawia polityki:
progi zostają tam, gdzie były (Opcja 3 cap-Z = Z 20, G2 = 35/40) — moduł
odpowiada wyłącznie za to, CO się mierzy i JAK się to agreguje.

Definicja (za werdyktem Sola RUN3-b, sekcja 3 G2):

    handoff_at_i = arrival_at_i + dwell_dropoff_i      # moment przekazania
    carry_i      = handoff_at_i - possession_at_i      # wiek jedzenia u kuriera

`possession_at` to fizyczne wejście jedzenia do torby (`picked_up_at`, a dla
zleceń jeszcze nieodebranych kotwica ready). Dla TEGO SAMEGO zlecenia kotwica
jest stała między permutacjami, więc:

    carry_i(kandydat) - carry_i(baseline) == handoff_i(kandydat) - handoff_i(baseline)

Stąd względny guard świeżości i guard opóźnienia dostawy to MATEMATYCZNIE ten
sam predykat (Sol RUN3-b: „nie implementować dwóch niezależnych checkerów").
`delta_min()` niżej jest tym jednym predykatem — wołają go G1 i G2-delta.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

#: Handoff = przyjazd + dwell dostawy. Ratchet: test WB2 czerwienieje, jeśli
#: którakolwiek warstwa wróci do liczenia świeżości na czasie przyjazdu.
HANDOFF_INCLUDES_DROPOFF_DWELL = True


def handoff_min(arrival_min: Optional[float],
                dwell_min: Optional[float]) -> Optional[float]:
    """Moment PRZEKAZANIA paczki klientowi (OD-01: arrival ≠ handoff).

    Oba argumenty w tej samej skali (minuty względem wspólnej kotwicy albo
    minuty bezwzględne). `None` propaguje — brak czasu oznacza kandydata
    nieocenialnego, nigdy kandydata domyślnie dobrego.
    """
    if arrival_min is None:
        return None
    return float(arrival_min) + float(dwell_min or 0.0)


def carry_min(handoff: Optional[float],
              possession: Optional[float]) -> Optional[float]:
    """Wiek jedzenia w torbie w chwili przekazania = handoff − possession."""
    if handoff is None or possession is None:
        return None
    return float(handoff) - float(possession)


def max_carry_min(carry_by_order: Dict[str, Optional[float]]) -> float:
    """Najstarsza NIESIONA sztuka. Pusty zbiór = 0.0 (parytet z cap-Z).

    Jedyny agregator „max wieku niesionego" w repo — cap-Z reseq i G2 wołają
    ten sam kod, więc nie mogą się rozjechać po zaokrągleniu ani po tym, czy
    `None` liczy się jako zero.
    """
    vals = [float(v) for v in carry_by_order.values() if v is not None]
    return max(vals) if vals else 0.0


def delta_min(baseline: Optional[float],
              candidate: Optional[float]) -> Optional[float]:
    """Pogorszenie kandydata vs baseline (dodatnie = gorzej).

    JEDEN predykat delty dla G1 (opóźnienie dostawy) i G2 (świeżość) — patrz
    dowód równoważności w docstringu modułu. Porównanie na SUROWYCH minutach,
    bez `round()`: zaokrąglenie prezentacyjne w progu decyzyjnym potrafi
    przepuścić kandydata dokładnie na granicy tolerancji.
    """
    if baseline is None or candidate is None:
        return None
    return float(candidate) - float(baseline)


def worst_delta(baseline_by_order: Dict[str, Optional[float]],
                candidate_by_order: Dict[str, Optional[float]],
                order_ids: Iterable[str]):
    """Największa delta i jej zlecenie: `(oid, delta)` albo `(None, None)`.

    Brak którejkolwiek wartości dla rozważanego zlecenia zwraca
    `(oid, None)` — sygnał „nieocenialny", który wywołujący MUSI potraktować
    jako odrzucenie kandydata (fail-closed), nie jako brak pogorszenia.
    """
    worst_oid = None
    worst = None
    for oid in order_ids:
        d = delta_min(baseline_by_order.get(oid), candidate_by_order.get(oid))
        if d is None:
            return oid, None
        if worst is None or d > worst:
            worst_oid, worst = oid, d
    return worst_oid, worst

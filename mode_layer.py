"""mode_layer — W1 (advisory Tura 2, T2.4): FSM trybów S1/S2/S3 (kanon C5).

CZYSTY LIŚĆ (zero importów silnika) — liczy tryb z SYGNAŁÓW świata, testowalny w
izolacji. Wpięcie (jeden hook w dispatch_pipeline) + akcja S2-defer + propozycja
slotu do panelu = kolejne inkrementy; TU jest rdzeń klasyfikacji + histereza +
obserwowalność „would-be-mode" (shadow-first, przed jakimkolwiek flipem).

WEJŚCIE trybu (werdykt E-4, wariant GPT „2 z 3 podtrzymane ≥10 min"):
  S2 gdy ≥2 z 3 sygnałów przekroczone I UTRZYMANE ≥ SUSTAIN_MIN:
    - L = in-flight(assigned)/aktywni ≥ L_HI (6)
    - kolejka pending ≥ Q_HI (10)
    - latencja przydziału (mediana created→assigned) ≥ LAT_HI (5 min)
  S3 gdy rate „S1∧S2-infeasible" ≥ S3_RATE (param) LUB capitulation-marker
    (defery+przerzuty→0 ∧ kolejka ≥ Q_CAP (20)).
WYJŚCIE z histerezą: progi wyjścia < wejścia (L_LO/Q_LO/LAT_LO), dwell ≥ DWELL_MIN.

Efekty trybów (DEFINICJE — egzekwuje silnik w kolejnym inkremencie, NIE tu):
  S2 = defer jako akcja (pętla slotów +5..90′; budżet ≤3/Σ90′; completion-guard).
  S3 = R6→40 / R27→±10 dla WSZYSTKICH + priority-shed (czasówki+najstarsze chronione);
       jedzenie NIGDY >40 (kanon nietykalny — relaks R6 tylko do 40, wyłącznie w S3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── progi (E-4; env/flags nadpisze w silniku, tu stałe-fallback) ──
L_HI, L_LO = 6.0, 4.5          # in-flight/aktywni: wejście / wyjście (histereza)
Q_HI, Q_LO = 10, 7            # kolejka pending
LAT_HI, LAT_LO = 5.0, 3.5    # latencja przydziału med (min)
SUSTAIN_MIN = 10.0           # „podtrzymane ≥10 min" (2 z 3)
DWELL_MIN = 15.0             # min. czas w trybie przed wyjściem
Q_CAP = 20                   # kolejka dla capitulation-marker
S3_RATE_DEFAULT = 0.20       # rate S1∧S2-infeasible (grid 0,15/0,20 w replay)

S1, S2, S3 = "S1", "S2", "S3"


@dataclass
class ModeSignals:
    """Sygnały świata w chwili decyzji (dostarcza hook silnika / replay)."""
    load_inflight_per_active: float = 0.0   # L
    queue_pending: int = 0
    assign_latency_med_min: float = 0.0
    s2_infeasible_rate: float = 0.0         # rate S1∧S2-infeasible (do S3)
    defers_and_reassigns: int = 0           # 0 przy capitulation
    now_min: float = 0.0                    # znacznik czasu (min) — do sustain/dwell


@dataclass
class ModeState:
    """Stan FSM między decyzjami (histereza/sustain/dwell). Serializowalny."""
    mode: str = S1
    entered_at_min: float = 0.0             # kiedy weszliśmy w bieżący tryb
    # ile CIĄGŁEJ minuty utrzymuje się warunek 2-z-3 (do SUSTAIN)
    two_of_three_since_min: Optional[float] = None
    reason: str = "init"


def _parse_iso(v):
    from datetime import datetime, timezone
    if not v or not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def mode_signals_from_state(orders: dict, now, pending_count: int = 0,
                            latency_window_min: float = 15.0) -> ModeSignals:
    """Wyprowadza ModeSignals z żywego `orders_state` (read-only, czysta funkcja).
      - L = in-flight(assigned/picked_up, nie delivered) / aktywni kurierzy;
      - kolejka = pending_count (z pending_pool) + orders bez kuriera-a-utworzone;
      - latencja = mediana (assigned − created) [min] w oknie `latency_window_min`.
    now_min = minuty od północy UTC (spójne z replay/testami)."""
    import statistics
    from datetime import timedelta
    inflight_by_cid = {}
    queue_unassigned = 0
    lats = []
    now_dt = now
    for o in (orders or {}).values():
        if not isinstance(o, dict):
            continue
        st = o.get("status")
        cid = str(o.get("courier_id") or "")
        if st in ("assigned", "picked_up") and cid:
            inflight_by_cid[cid] = inflight_by_cid.get(cid, 0) + 1
        elif st in ("new", "pending", "", None) and not cid:
            queue_unassigned += 1
        at = _parse_iso(o.get("assigned_at"))
        ct = _parse_iso(o.get("created_at_utc"))
        if at is not None and ct is not None and now_dt is not None:
            if now_dt - timedelta(minutes=latency_window_min) <= at <= now_dt:
                dm = (at - ct).total_seconds() / 60.0
                if 0 <= dm < 180:
                    lats.append(dm)
    n_inflight = sum(inflight_by_cid.values())
    active = len(inflight_by_cid)
    L = n_inflight / active if active else 0.0
    now_min = 0.0
    if now_dt is not None:
        now_min = now_dt.hour * 60 + now_dt.minute + now_dt.second / 60.0
    return ModeSignals(
        load_inflight_per_active=round(L, 2),
        queue_pending=int(pending_count) + queue_unassigned,
        assign_latency_med_min=round(statistics.median(lats), 1) if lats else 0.0,
        defers_and_reassigns=99,  # brak sygnału deferów w state → NIE odpalaj capitulation
        now_min=now_min)


def _two_of_three(sig: ModeSignals) -> tuple[bool, list[str]]:
    hits = []
    if sig.load_inflight_per_active >= L_HI:
        hits.append(f"L={sig.load_inflight_per_active:.1f}≥{L_HI}")
    if sig.queue_pending >= Q_HI:
        hits.append(f"queue={sig.queue_pending}≥{Q_HI}")
    if sig.assign_latency_med_min >= LAT_HI:
        hits.append(f"lat={sig.assign_latency_med_min:.1f}≥{LAT_HI}")
    return (len(hits) >= 2, hits)


def _below_exit(sig: ModeSignals) -> bool:
    """Warunek WYJŚCIA z S2 (histereza): wszystkie 3 poniżej progów wyjścia."""
    return (sig.load_inflight_per_active < L_LO and sig.queue_pending < Q_LO
            and sig.assign_latency_med_min < LAT_LO)


def _capitulation(sig: ModeSignals) -> bool:
    return sig.defers_and_reassigns == 0 and sig.queue_pending >= Q_CAP


def step(state: ModeState, sig: ModeSignals, s3_rate: float = S3_RATE_DEFAULT) -> ModeState:
    """Jeden krok FSM (czysty: zwraca NOWY ModeState, nie mutuje). Histereza:
    wejście S2 wymaga 2-z-3 UTRZYMANE ≥SUSTAIN; wyjście wymaga poniżej-progów-wyjścia
    ∧ dwell≥DWELL. S3 nadrzędne (rate ∨ capitulation), wyjście S3 gdy rate spadnie ∧ dwell."""
    two, hits = _two_of_three(sig)
    # utrzymanie licznika sustain
    since = state.two_of_three_since_min
    if two:
        if since is None:
            since = sig.now_min
    else:
        since = None
    sustained = two and since is not None and (sig.now_min - since) >= SUSTAIN_MIN

    dwell_ok = (sig.now_min - state.entered_at_min) >= DWELL_MIN
    new = ModeState(mode=state.mode, entered_at_min=state.entered_at_min,
                    two_of_three_since_min=since, reason=state.reason)

    def to(mode, reason):
        new.mode = mode
        new.reason = reason
        if mode != state.mode:
            new.entered_at_min = sig.now_min

    # S3 — najwyższy priorytet (kryzys głęboki)
    s3_on = sig.s2_infeasible_rate >= s3_rate or _capitulation(sig)
    if s3_on:
        to(S3, f"S3: rate={sig.s2_infeasible_rate:.2f}≥{s3_rate}"
               + (" ∨ capitulation" if _capitulation(sig) else ""))
        return new
    if state.mode == S3:
        # wyjście z S3 tylko gdy rate opadł I dwell
        if dwell_ok:
            to(S2 if (two or not _below_exit(sig)) else S1, "S3→wyjście (rate↓, dwell)")
        else:
            to(S3, "S3: dwell<min")
        return new

    # S2 wejście/utrzymanie/wyjście
    if state.mode == S2:
        if _below_exit(sig) and dwell_ok:
            to(S1, "S2→S1 (poniżej progów wyjścia + dwell)")
        else:
            to(S2, "S2: utrzymanie" + (f" [{','.join(hits)}]" if hits else ""))
        return new

    # z S1
    if sustained:
        to(S2, f"S1→S2 (2-z-3 podtrzymane ≥{SUSTAIN_MIN}′: {','.join(hits)})")
    else:
        to(S1, "S1" + (f" (2-z-3 nieutrzymane: {','.join(hits)})" if two else ""))
    return new

"""GPS-02 — filtr jakości pozycji GPS (accuracy + teleport), SHADOW-first.

Cel (audyt 2026-06-10 GPS-02, korekta Adriana 13.06):
    Odrzucać tylko ZŁY / podejrzany fix GPS (słaba dokładność, skok
    teleportacyjny implikujący nierealną prędkość), a NIGDY nie karać BRAKU
    GPS. Brak GPS = celowa polityka treningowa (Ziomek ma działać też bez
    GPS, na kotwicach czasowych/roster) — patrz feedback_rules.md
    „Brak GPS = celowa polityka". Brak pola accuracy w fixie NIE oznacza
    złego fixu — degradujemy łagodnie (przepuszczamy).

Architektura:
    Czyste funkcje (zero I/O, deterministyczne, testowalne) wzorem
    auto_proximity_classifier. Konsument (courier_resolver.build_fleet_snapshot)
    woła `assess_gps_quality(...)` na świeżym fixie PO sanity-bbox FAIL-05, a
    PRZED utrwaleniem pos_source="gps". W trybie SHADOW (flaga OFF, domyślnie)
    wynik jest tylko logowany — decyzja floty bez zmian. Po flipie flagi
    werdykt "reject" sprawia, że fix nie wchodzi jako pos_source="gps" i
    następuje fall-through (bag/recent/last-known-pos/no_gps) — DOKŁADNIE jak
    przy GPS_BBOX_REJECT, więc zero nowych interakcji w scoringu/feasibility.

Współgranie z last-known-pos store (FIX 2026-06-08, lekcja #176):
    - Teleport-detekcja używa POPRZEDNIEJ wiarygodnej pozycji GPS jako kotwicy.
      Dostarcza ją caller (z last-known-pos store lub poprzedniego ticku).
    - Gdy fix odrzucony jako teleport, caller robi fall-through — last-known-pos
      store naturalnie odtworzy ostatnią dobrą pozycję (pos_from_store=True).
      Nie duplikujemy tej logiki tutaj.

Progi (DO KALIBRACJI — oparte na empirii 111 819 fixów gps_history 06-12,
patrz eod_drafts/2026-06-13/gps_quality_calib.md):
    accuracy>150m  →  ~1.2% fixów (najgorszy ogon — avg 19m, p~98 < 100m)
    teleport: skok >2 km implikujący >120 km/h  →  ~0.06-0.24% par.
    Konserwatywnie wymagamy OBU warunków (skok + prędkość), żeby drobny jitter
    w mieście (0.3-0.5 km w 1-2 s = nierealne km/h, ale nieszkodliwy) NIE był
    karany; karzemy tylko realne skoki przez pół miasta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple, List


# ── Progi (stałe modułu, „do kalibracji"; env/flags.json override w callerze) ──

# Accuracy (promień błędu w metrach z apki). Fix gorszy niż próg = niski-zaufany.
# Empiria 06-12: avg 19m, accuracy>150m = 1.2% fixów (rzadki, wyraźny śmieć).
# Konserwatywnie 150m (NIE 100m) — nie chcemy odrzucać legalnych fixów w mieście
# o słabszej widoczności nieba (dziedzińce, parkingi podziemne).
GPS_ACCURACY_MAX_M = 150.0

# Teleport: oba warunki muszą zajść jednocześnie.
# - skok dystansu większy niż próg (drobny jitter <X km ignorujemy — nieszkodliwy)
# - implikowana prędkość większa niż próg (realny ruch miejski p98 < 80 km/h)
GPS_TELEPORT_MIN_JUMP_KM = 2.0       # poniżej = jitter, nie teleport
GPS_TELEPORT_MAX_SPEED_KMH = 120.0   # powyżej = nierealne w mieście
# Kotwica do teleport-detekcji starsza niż to (min) jest bezużyteczna — przy
# dużej luce czasowej duży skok bywa realny (kurier naprawdę przejechał miasto).
GPS_TELEPORT_ANCHOR_MAX_AGE_MIN = 8.0
# Minimalny odstęp czasu między fixami do liczenia prędkości — przy ~0 dt
# prędkość eksploduje (0.5 km / 1 s = 1800 km/h) na drobnym jitterze. Poniżej
# tego progu prędkości NIE liczymy (chroni przed false-positive na sub-km skoku).
GPS_TELEPORT_MIN_DT_S = 3.0


def _haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Dystans wielkiego koła (km) między (lat,lon). Zero gdy dane złe."""
    try:
        lat1, lon1 = float(a[0]), float(a[1])
        lat2, lon2 = float(b[0]), float(b[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


@dataclass
class GpsQualityVerdict:
    """Werdykt jakości pojedynczego fixu GPS.

    accept: czy fix jest wiarygodny (True = wpuść jako pos_source="gps" gdy
            flaga ON). SHADOW: caller ignoruje, tylko loguje.
    reasons: lista przyczyn obniżenia zaufania (puste = czysty fix).
    low_accuracy / teleport: które testy zadziałały (do telemetrii).
    accuracy_m / implied_speed_kmh / jump_km: zmierzone wartości (None gdy n/d).
    """
    accept: bool = True
    reasons: List[str] = field(default_factory=list)
    low_accuracy: bool = False
    teleport: bool = False
    accuracy_m: Optional[float] = None
    has_accuracy_field: bool = True
    implied_speed_kmh: Optional[float] = None
    jump_km: Optional[float] = None
    anchor_age_min: Optional[float] = None

    def to_log_dict(self) -> dict:
        """Zwięzły dict do shadow-logu (gps_quality)."""
        return {
            "accept": self.accept,
            "reasons": list(self.reasons),
            "low_accuracy": self.low_accuracy,
            "teleport": self.teleport,
            "accuracy_m": round(self.accuracy_m, 1) if self.accuracy_m is not None else None,
            "has_accuracy_field": self.has_accuracy_field,
            "implied_speed_kmh": round(self.implied_speed_kmh, 1) if self.implied_speed_kmh is not None else None,
            "jump_km": round(self.jump_km, 3) if self.jump_km is not None else None,
            "anchor_age_min": round(self.anchor_age_min, 1) if self.anchor_age_min is not None else None,
        }


def assess_accuracy(
    accuracy_raw,
    *,
    accuracy_max_m: float = GPS_ACCURACY_MAX_M,
) -> Tuple[bool, Optional[float], bool]:
    """Czysty test dokładności fixu.

    Zwraca (low_accuracy, accuracy_m, has_accuracy_field).
    - accuracy_raw None / nieparsowalne / ≤0 → BRAK pola → low_accuracy=False
      (degraduj łagodnie — brak danych NIE jest złym fixem; korekta Adriana).
    - accuracy_m > accuracy_max_m → low_accuracy=True (słaba dokładność).
    """
    if accuracy_raw is None:
        return (False, None, False)
    try:
        acc = float(accuracy_raw)
    except (TypeError, ValueError):
        return (False, None, False)
    if acc != acc or acc <= 0:  # NaN lub niesensowne ≤0 → brak wiarygodnej dokładności
        return (False, None, False)
    return (acc > accuracy_max_m, acc, True)


def assess_teleport(
    new_pos: Tuple[float, float],
    anchor_pos: Optional[Tuple[float, float]],
    dt_seconds: Optional[float],
    *,
    min_jump_km: float = GPS_TELEPORT_MIN_JUMP_KM,
    max_speed_kmh: float = GPS_TELEPORT_MAX_SPEED_KMH,
    min_dt_s: float = GPS_TELEPORT_MIN_DT_S,
    anchor_max_age_min: float = GPS_TELEPORT_ANCHOR_MAX_AGE_MIN,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """Czysty test teleportu względem POPRZEDNIEJ wiarygodnej pozycji.

    Zwraca (teleport, jump_km, implied_speed_kmh). Brak kotwicy / za stara /
    dt za małe → (False, jump?, None) — nie da się ocenić, łagodnie przepuść.

    Teleport WYMAGA obu: skok > min_jump_km AND prędkość > max_speed_kmh.
    """
    if anchor_pos is None or dt_seconds is None:
        return (False, None, None)
    try:
        dt = float(dt_seconds)
    except (TypeError, ValueError):
        return (False, None, None)
    if dt <= 0:
        # nie-monotoniczny / równoczesny fix — nie liczymy prędkości
        return (False, None, None)
    if dt / 60.0 > anchor_max_age_min:
        # kotwica zbyt stara — duży skok może być realny (kurier przejechał)
        jump = _haversine_km(new_pos, anchor_pos)
        return (False, jump, None)
    jump = _haversine_km(new_pos, anchor_pos)
    if dt < min_dt_s:
        # zbyt krótki odstęp → prędkość niestabilna; oceniamy SAM skok, ale
        # nie wyliczamy km/h (chroni przed eksplozją na sub-sekundowym jitterze).
        # Bez wiarygodnej prędkości NIE orzekamy teleportu (konserwatywnie).
        return (False, jump, None)
    speed = jump / (dt / 3600.0)
    teleport = (jump > min_jump_km) and (speed > max_speed_kmh)
    return (teleport, jump, speed)


def assess_gps_quality(
    new_pos: Tuple[float, float],
    accuracy_raw=None,
    *,
    anchor_pos: Optional[Tuple[float, float]] = None,
    dt_seconds: Optional[float] = None,
    anchor_age_min: Optional[float] = None,
    accuracy_max_m: float = GPS_ACCURACY_MAX_M,
    teleport_min_jump_km: float = GPS_TELEPORT_MIN_JUMP_KM,
    teleport_max_speed_kmh: float = GPS_TELEPORT_MAX_SPEED_KMH,
    teleport_min_dt_s: float = GPS_TELEPORT_MIN_DT_S,
    teleport_anchor_max_age_min: float = GPS_TELEPORT_ANCHOR_MAX_AGE_MIN,
) -> GpsQualityVerdict:
    """Złożony werdykt jakości fixu (accuracy + teleport).

    accept=False gdy WEJDZIE accuracy-low LUB teleport. Brak danych
    (accuracy/kotwica/dt) → łagodnie accept=True. Funkcja czysta —
    nie odczytuje globalnego stanu ani plików.
    """
    v = GpsQualityVerdict()
    v.anchor_age_min = anchor_age_min

    low_acc, acc_m, has_acc = assess_accuracy(accuracy_raw, accuracy_max_m=accuracy_max_m)
    v.low_accuracy = low_acc
    v.accuracy_m = acc_m
    v.has_accuracy_field = has_acc
    if low_acc:
        v.reasons.append(f"low_accuracy({acc_m:.0f}m>{accuracy_max_m:.0f}m)")

    tele, jump, speed = assess_teleport(
        new_pos, anchor_pos, dt_seconds,
        min_jump_km=teleport_min_jump_km,
        max_speed_kmh=teleport_max_speed_kmh,
        min_dt_s=teleport_min_dt_s,
        anchor_max_age_min=teleport_anchor_max_age_min,
    )
    v.teleport = tele
    v.jump_km = jump
    v.implied_speed_kmh = speed
    if tele:
        v.reasons.append(
            f"teleport(jump={jump:.2f}km,speed={speed:.0f}km/h)"
        )

    v.accept = not (low_acc or tele)
    return v

"""Pamięć dokładnego punktu GPS dla powtarzających się adresów (Etap 1).

PO CO: adres uczy się swojego realnego miejsca dostawy z punktów GPS, które
kurier zostawia przy „doręczone" (status 7, geofence/ręczne). Powtarzające się
adresy (`address_id`) z czasem „znają swoje wejście", żeby kurier trafiał bez
odpalania Google Maps, a koordynator widział pinezkę na mapie.

ZASADA (Adrian, opcja B): każda dostawa = świeży punkt-kandydat. Pojedynczy
punkt jest niepewny (raz pod klatką, raz w środku, raz już odjeżdżając), więc
NIE nadpisujemy ślepo — trzymamy okno ostatnich próbek i liczymy ROBUSTNY
środek (mediana + odrzut odstających). Z wielu dostaw pinezka się „utwardza":
skupisko wygrywa, pojedyncze strzały „w biegu" odpadają.

CZYSTA BIBLIOTEKA — zero I/O na import, zero zależności od silnika dispatchu.
Zapis atomowy (temp→fsync→rename). Magazyn = JSON keyed by address_id; każdy
serwis (agregator / konsola / apka) czyta ten sam plik jako kontrakt.

Na tym etapie NIKT tego nie konsumuje decyzyjnie — additive (wzorzec #8),
zero wpływu na feasibility/scoring/selekcję Ziomka.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import tempfile
import time

# --- Parametry doboru pinezki (kalibrowalne) ---------------------------------
MAX_SAMPLES = 15            # rolling window próbek per adres (opcja B: świeże wypychają stare)
OUTLIER_M = 40.0           # punkt dalej niż tyle od mediany = odstający (np. „w biegu")
ACCURACY_REJECT_M = 80.0   # odczyt GPS gorszy niż tyle metrów = odrzucony (gdy accuracy znane)
MIN_INLIERS_HIGH_CONF = 3  # tyle zgodnych próbek → pinezka „pewna"
MAX_SPREAD_HIGH_M = 25.0   # i rozrzut skupiska ≤ tyle → „pewna"
GEOFENCE_TRIGGER = "auto_geofence"  # fizyczne wejście w strefę — preferowane nad ręcznym
TRAIL_TRIGGER = "trail"    # punkt z trasy GPS (gps_history) przy delivered_at — prawda-przyciskowa, najsłabszy

# Ranga wiarygodności źródła punktu: niższa = lepsza. Pinezkę liczymy z NAJLEPSZEGO
# dostępnego tieru (geofence > ręczne > trail). trail = „prawda-przyciskowa"
# (delivered_at ±~3min od fizyki) → nigdy nie daje statusu „pewny".
def _provenance_rank(trigger) -> int:
    if trigger == GEOFENCE_TRIGGER:
        return 0
    if trigger == TRAIL_TRIGGER:
        return 2
    return 1  # ręczne / inne zgłoszenie z apki


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Dystans w metrach między dwoma punktami (Haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _valid_point(lat, lon) -> bool:
    """(0,0) / None / poza zakresem = brak punktu."""
    try:
        lat = float(lat); lon = float(lon)
    except (TypeError, ValueError):
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _accuracy_ok(acc) -> bool:
    """accuracy nieznane (None) = akceptuj (nie karz braku danych); znane → próg."""
    if acc is None:
        return True
    try:
        return float(acc) <= ACCURACY_REJECT_M
    except (TypeError, ValueError):
        return True


def normalize_address(text) -> str | None:
    """Klucz adresu ze znormalizowanego tekstu.

    ⚠ `address_id` z panelu NIE jest stabilnym kluczem fizycznego adresu (jest
    recyklingowany — 71 wartości na 2643 realne adresy), dlatego grupujemy po
    TEKŚCIE. Konserwatywnie: lower + zbij białe znaki + usuń kod pocztowy.
    NIE scala różnych budynków (zostawia numer/lokal/piętro) — woli mniej próbek
    na adres niż błędne scalenie dwóch miejsc.
    """
    if not text or not str(text).strip():
        return None
    t = str(text).strip().lower()
    t = re.sub(r"\d{2}-\d{3}", "", t)   # kod pocztowy 15-001
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def select_best_pin(samples: list) -> dict | None:
    """Z listy próbek {lat,lon,accuracy,trigger,ts} → najlepsza pinezka.

    Robustny środek: mediana współrzędnych + odrzut odstających (>OUTLIER_M).
    Preferencja: jeśli jest ≥MIN_INLIERS_HIGH_CONF próbek z geofence (fizyczne
    wejście), liczymy środek WYŁĄCZNIE z nich (najwiarygodniejsze). Zwraca też
    confidence/n_samples/spread, żeby UI mogło pokazać „sprawdzone, X dostaw".
    """
    pts = [s for s in samples if _valid_point(s.get("lat"), s.get("lon"))]
    if not pts:
        return None
    n_total = len(pts)

    # 1) odrzut słabej dokładności (gdy znana), z fallbackiem gdy by wszystko wycięło
    acc_ok = [s for s in pts if _accuracy_ok(s.get("accuracy"))]
    base = acc_ok or pts

    # 2) licz pinezkę z NAJLEPSZEGO dostępnego tieru źródła (geofence > ręczne > trail) —
    #    słabsze źródła są tylko bootstrapem, lepsze nadpisuje gdy się pojawi
    best_rank = min(_provenance_rank(s.get("trigger")) for s in base)
    pool = [s for s in base if _provenance_rank(s.get("trigger")) == best_rank]

    # 3) mediana jako wstępny środek (odporna na pojedyncze odstrzały)
    c_lat = statistics.median(float(s["lat"]) for s in pool)
    c_lon = statistics.median(float(s["lon"]) for s in pool)

    # 4) inliers w promieniu OUTLIER_M od mediany; przelicz środek na nich
    inliers = [s for s in pool
               if haversine_m(float(s["lat"]), float(s["lon"]), c_lat, c_lon) <= OUTLIER_M]
    if not inliers:
        inliers = pool
    b_lat = statistics.median(float(s["lat"]) for s in inliers)
    b_lon = statistics.median(float(s["lon"]) for s in inliers)
    spread = max((haversine_m(float(s["lat"]), float(s["lon"]), b_lat, b_lon)
                  for s in inliers), default=0.0)

    # „pewny" tylko dla wiarygodnego źródła (geofence/ręczne) — trail (prawda-przyciskowa)
    # nigdy nie jest high, choćby skupisko było ciasne
    confidence = ("high" if best_rank <= 1 and len(inliers) >= MIN_INLIERS_HIGH_CONF
                  and spread <= MAX_SPREAD_HIGH_M else "low")
    triggers = {s.get("trigger") for s in inliers}
    source = (triggers.pop() if len(triggers) == 1 else "mixed")

    return {
        "lat": round(b_lat, 7),
        "lon": round(b_lon, 7),
        "confidence": confidence,
        "n_samples": n_total,
        "n_inliers": len(inliers),
        "spread_m": round(spread, 1),
        "source": source,
    }


def add_sample(store: dict, address_text, sample: dict,
               now: float | None = None) -> dict:
    """Dokłada próbkę do adresu (rolling window) i przelicza najlepszą pinezkę.

    Klucz = `normalize_address(address_text)` (NIE address_id — patrz docstring
    normalize_address). Idempotentne względem tej samej dostawy (dedup po
    (order_id, ts)). Zwraca wpis adresu po aktualizacji ({} gdy zły adres/punkt).
    """
    now = time.time() if now is None else now
    key = normalize_address(address_text)
    if key is None or not _valid_point(sample.get("lat"), sample.get("lon")):
        return store.get(key, {}) if key else {}
    entry = store.get(key) or {"address_key": key, "samples": []}
    entry["address_key"] = key
    if address_text:
        entry["address_text"] = str(address_text).strip()

    sm = {
        "lat": round(float(sample["lat"]), 7),
        "lon": round(float(sample["lon"]), 7),
        "accuracy": sample.get("accuracy"),
        "trigger": sample.get("trigger"),
        "ts": sample.get("ts"),
        "order_id": sample.get("order_id"),
    }
    samples = entry.get("samples", [])
    # dedup: ta sama dostawa nie wchodzi dwa razy
    dedup = (sm.get("order_id"), sm.get("ts"))
    if dedup != (None, None) and any((x.get("order_id"), x.get("ts")) == dedup for x in samples):
        return entry
    samples.append(sm)
    samples = samples[-MAX_SAMPLES:]  # opcja B: najświeższe N
    entry["samples"] = samples

    best = select_best_pin(samples)
    if best:
        entry.update(best)
        entry["updated_at"] = int(now)
    store[key] = entry
    return entry


# --- Magazyn (atomic JSON) ---------------------------------------------------
def load_store(path: str) -> dict:
    """Cały magazyn jako dict {address_id: entry}. {} gdy brak/zepsuty."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_store(path: str, store: dict) -> None:
    """Zapis atomowy: temp→fsync→rename (jak reszta dispatch_state)."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".address_pins.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def public_pin(entry: dict) -> dict | None:
    """Lekki widok dla konsumenta (konsola/apka) — bez surowych próbek."""
    if not entry or "lat" not in entry:
        return None
    return {
        "address_key": entry.get("address_key"),
        "address_text": entry.get("address_text"),
        "lat": entry["lat"],
        "lon": entry["lon"],
        "confidence": entry.get("confidence", "low"),
        "source": entry.get("source"),
        "deliveries": entry.get("n_inliers", entry.get("n_samples", 0)),
        "updated_at": entry.get("updated_at"),
    }

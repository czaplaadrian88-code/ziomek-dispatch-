"""FAZA 2 (2026-06-08) — warstwa weryfikacji poprawności geokodu.

Bbox guard sprawdza TYLKO „czy w mieście" — nie łapie błędu wewnątrz miasta
(Magazynowa↔Malachitowa, 7.5 km, oba w bboxie). Ta warstwa łączy 3 niezależne
sygnały, żeby Ziomek „nie miał prawa się pomylić" co do ulicy:

  (2) Google location_type / partial_match — APPROXIMATE/GEOMETRIC_CENTER lub
      partial_match = niepewne dopasowanie.
  (3) zgodność dzielnicy — dzielnica wyniku (reverse-lookup po lat/lon) vs
      dzielnica wynikająca z tekstu adresu (drop_zone_from_address). Inna i
      NIE-sąsiednia = „dobre miasto, zła ulica".
  (4) cross-source — drugie źródło (Nominatim/OSM); rozjazd > próg = podejrzane.

Werdykt wymaga DWÓCH niezależnych sygnałów „źle" do odrzucenia (reject), żeby
pojedynczy szum (np. adres przy granicy dzielnic) nie blokował realnych zamówień.
Pojedynczy sygnał → „low" (flaga, log). Brak → „ok".

Moduł jest czysty (logika) + jedno I/O (Nominatim). Funkcje lookupów wstrzykiwane
przez callera (geocoding.py) — testowalne bez ciężkich importów.
"""
import json
import math
import urllib.parse
import urllib.request
from typing import Callable, Optional, Tuple


def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    R = 6371000.0
    la1, lo1 = math.radians(a[0]), math.radians(a[1])
    la2, lo2 = math.radians(b[0]), math.radians(b[1])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def nominatim_geocode(
    address: str, city: Optional[str], timeout: float = 3.0,
    user_agent: str = "ziomek-dispatch/1.0",
) -> Optional[Tuple[float, float]]:
    """Drugie źródło (OSM/Nominatim). Zwraca (lat, lon) lub None. Fail-soft."""
    q = f"{address}, {city}, Polska" if city else f"{address}, Polska"
    params = urllib.parse.urlencode({
        "q": q, "format": "json", "limit": "1", "countrycodes": "pl",
        "addressdetails": "0",
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        if not data:
            return None
        return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        return None


def verify(
    address: str,
    city: Optional[str],
    lat: float,
    lon: float,
    *,
    location_type: Optional[str] = None,
    partial_match: bool = False,
    low_conf_location_types: frozenset = frozenset({"APPROXIMATE", "GEOMETRIC_CENTER"}),
    district_check: bool = True,
    expected_district_fn: Optional[Callable[[str, Optional[str]], str]] = None,
    actual_district_fn: Optional[Callable[[float, float], str]] = None,
    districts_adjacent_fn: Optional[Callable[[str, str], bool]] = None,
    cross_source: bool = True,
    cross_source_coords: Optional[Tuple[float, float]] = None,
    cross_source_max_disagree_m: float = 400.0,
) -> dict:
    """Zwraca {confidence: ok|low|reject, reasons:[...], checks:{...}}.

    cross_source_coords: wynik drugiego źródła (caller robi I/O i podaje); None =
    check pominięty (brak danych ≠ niezgodność).
    """
    checks = {}
    wrong_signals = []   # mocne sygnały „źle"
    soft_signals = []    # słabe sygnały „niepewne"

    # (2) location_type / partial_match
    lt = (location_type or "").upper()
    if partial_match:
        soft_signals.append("partial_match")
    if lt and lt in low_conf_location_types:
        soft_signals.append(f"location_type={lt}")
    checks["location_type"] = lt or None
    checks["partial_match"] = bool(partial_match)

    # (3) zgodność dzielnicy
    exp = act = None
    if district_check and expected_district_fn and actual_district_fn:
        try:
            exp = expected_district_fn(address, city)
        except Exception:
            exp = None
        try:
            act = actual_district_fn(lat, lon)
        except Exception:
            act = None
        checks["expected_district"] = exp
        checks["actual_district"] = act
        known = exp and act and exp not in ("Unknown", "") and act not in ("Unknown", "")
        if known and exp != act:
            adjacent = False
            if districts_adjacent_fn:
                try:
                    adjacent = bool(districts_adjacent_fn(exp, act))
                except Exception:
                    adjacent = False
            checks["districts_adjacent"] = adjacent
            if not adjacent:
                wrong_signals.append(f"district {exp}!={act}")
            else:
                soft_signals.append(f"district_adjacent {exp}~{act}")

    # (4) cross-source
    if cross_source and cross_source_coords is not None:
        dist_m = haversine_m((lat, lon), cross_source_coords)
        checks["cross_source_disagree_m"] = round(dist_m, 1)
        if dist_m > cross_source_max_disagree_m:
            wrong_signals.append(f"cross_source {int(dist_m)}m")

    # werdykt: ≥2 mocne sygnały, LUB 1 mocny + 1 słaby → reject; 1 sygnał → low
    n_wrong = len(wrong_signals)
    n_soft = len(soft_signals)
    if n_wrong >= 2 or (n_wrong >= 1 and n_soft >= 1):
        confidence = "reject"
    elif n_wrong >= 1 or n_soft >= 1:
        confidence = "low"
    else:
        confidence = "ok"

    return {
        "confidence": confidence,
        "reasons": wrong_signals + soft_signals,
        "checks": checks,
    }

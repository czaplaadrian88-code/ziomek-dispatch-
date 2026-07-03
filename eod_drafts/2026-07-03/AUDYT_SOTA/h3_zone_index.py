#!/usr/bin/env python3
"""
DRAFT (audyt SOTA 2026-07-03) — hierarchiczny indeks stref na Uber H3.

NIE JEST WPIĘTY. Wymaga: pip install h3 (v4). Zamiennik/uzupełnienie dla:
  * common.py:drop_zone_from_address — ręczny słownik ulica→dzielnica
    (~97/100 top ulic + aliasy; nowa ulica spoza słownika = "Unknown"),
  * district_reverse_lookup.py — kd-tree NN po address_cache.json (dokładność
    zależna od pokrycia cache; NN potrafi przypisać złą dzielnicę na granicy).

Co daje H3 w skali Białegostoku:
  * deterministyczne, bezsłownikowe strefowanie KAŻDEJ pary (lat,lng) — koniec
    z "Unknown" dla nowych ulic i z utrzymywaniem V327_STREET_ALIASES,
  * sąsiedztwo za darmo (grid_disk) zamiast ręcznej BIALYSTOK_DISTRICT_ADJACENCY,
  * hierarchia rozdzielczości: res 7 (~5 km² — strefa popytu do prognozy EWMA),
    res 8 (~0.7 km² — feature LGBM district_match), res 9 (~0.1 km² — colocation
    dostaw w bundlingu zamiast progu km na centroidach).
Migracja bezpieczna: shadow — licz h3_district OBOK dzisiejszego district,
loguj rozjazdy do JSONL, flip per-konsument po parytecie (protokół #0).
"""
from __future__ import annotations

import json
from pathlib import Path

import h3

RES_DEMAND = 7    # prognoza popytu / heatmapa
RES_DISTRICT = 8  # odpowiednik "dzielnicy" (feature LGBM, bonusy R06)
RES_COLOC = 9     # kolokacja dostaw w bundlingu

# seed: nazwy ludzkie dla komórek res-8 pokrywających znane dzielnice.
# Budowane JEDNORAZOWO z istniejącego address_cache.json (patrz build_seed()).
SEED_PATH = Path(__file__).with_name("h3_district_seed.json")


def cell(lat: float, lng: float, res: int = RES_DISTRICT) -> str:
    return h3.latlng_to_cell(float(lat), float(lng), res)


def cells_are_adjacent(cell_a: str, cell_b: str) -> bool:
    """Zamiennik BIALYSTOK_DISTRICT_ADJACENCY — sąsiedztwo z geometrii."""
    return cell_a == cell_b or cell_b in h3.grid_disk(cell_a, 1)


def coloc_key(lat: float, lng: float) -> str:
    """Klucz kolokacji dostaw (res 9, ~350 m) do bundle_deliv_coloc —
    zamiast progu BUNDLE_DELIV_COLOC_KM na centroidach."""
    return cell(lat, lng, RES_COLOC)


class H3DistrictIndex:
    """district(lat,lng) → nazwa ludzka; poza seedem → id komórki (nigdy None).

    Nazwa ludzka utrzymuje kompatybilność z dzisiejszymi konsumentami
    (feature LGBM `district`, bonusy R06, raporty)."""

    def __init__(self, seed_path: Path = SEED_PATH):
        self._names: dict[str, str] = {}
        if seed_path.exists():
            self._names = json.loads(seed_path.read_text(encoding="utf-8"))

    def district(self, lat: float, lng: float) -> str:
        c = cell(lat, lng)
        if c in self._names:
            return self._names[c]
        # granica dzielnic: spróbuj sąsiadów (pierścień 1) zanim padnie surowe id
        for nb in h3.grid_disk(c, 1):
            if nb in self._names:
                return self._names[nb]
        return c  # surowa komórka — nadal użyteczny, stabilny klucz strefy

    def match(self, lat_a: float, lng_a: float,
              lat_b: float, lng_b: float) -> dict:
        """Odpowiednik cech district_match_pickup / district_adjacent_pickup."""
        ca, cb = cell(lat_a, lng_a), cell(lat_b, lng_b)
        return {"same": ca == cb, "adjacent": cells_are_adjacent(ca, cb)}


def build_seed(address_cache_path: Path, out_path: Path = SEED_PATH) -> int:
    """Jednorazowo: address_cache.json (adres→coords+dzielnica z istniejącego
    drop_zone_from_address) → mapa komórka-res8 → dominująca nazwa dzielnicy."""
    cache = json.loads(address_cache_path.read_text(encoding="utf-8"))
    votes: dict[str, dict[str, int]] = {}
    for entry in cache.values():
        try:
            lat, lng = float(entry["lat"]), float(entry["lng"])
            name = str(entry.get("district") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not name or name.lower() == "unknown":
            continue
        c = cell(lat, lng)
        votes.setdefault(c, {}).setdefault(name, 0)
        votes[c][name] += 1
    seed = {c: max(names, key=names.get) for c, names in votes.items()}
    out_path.write_text(json.dumps(seed, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return len(seed)


if __name__ == "__main__":
    idx = H3DistrictIndex()
    centrum = (53.1325, 23.1688)
    print("cell res8:", cell(*centrum))
    print("district:", idx.district(*centrum))
    print("match:", idx.match(53.1325, 23.1688, 53.14, 23.18))

# City bug audit — 2026-04-19

**Bug:** #466975 (Chicago Pizza → Kraszewskiego 10a/15 **Kleosin**) i #466978
(Retrospekcja → Kraszewskiego 21A/20 **Białystok**) fałszywie zbundlowane jako
"po drodze 0.3 km". Realna odległość Kleosin↔Białystok po ul. Kraszewskiego ≈ **5.3 km**.

## Verdict (TL;DR)

Bug to **trzy warstwy tego samego problemu — brak propagacji miasta**:

1. **`panel_client.normalize_order` (L349-352)** NIE parsuje `raw.get("miasto")`
   z odpowiedzi `edit-zamowienie`. Zwracany dict ma `delivery_address` złożone z
   `street + nr_domu + nr_mieszkania` — **zero city field**.
2. **`geocoding.geocode()` (L143)** ma `hint_city='Białystok'` jako hardcoded default.
   Wszyscy callerzy (`state_machine:243`, `panel_watcher:192`,
   `shadow_dispatcher:302/314`) wywołują **bez argumentu city** → wpadają w default.
3. **`_normalize` (L93-102)** dokleja `, białystok` do cache key jeśli stringu
   nie ma "białystok" → adres Kleosin zostaje zcachowany pod kluczem
   `"kraszewskiego 10a, białystok"` z **coords Białystok 53.136, 23.173**
   zamiast Kleosin 53.104, 23.119.

Google Maps API dostaje query `"Kraszewskiego 10a/15, Białystok, Polska"` —
z `region=pl + language=pl` bez `components=country:PL|locality:Kleosin`.
Google zwraca najlepsze dopasowanie ignorujące faktyczne miasto klienta
(fallback na `region=pl` hint).

## A. Pole `miasto` w pipeline

**Tylko 3 pliki** mają słowo `city`/`miasto` w całym `dispatch_v2/`:

| File | Kontekst | Runtime pipeline? |
|---|---|---|
| `bootstrap_restaurants.py:88` | `info.get("city")` przy tworzeniu restaurant_coords.json | **NIE** (one-shot script) |
| `extract_restaurant_addresses.py:56, 122` | norm_city podczas scrape | **NIE** (helper) |
| `tests/test_decision_engine_f21.py` | test fixture | test only |

**`panel_client.normalize_order` (L320-395) — runtime pipeline — ZERO miasta.**
Zwracany dict ma pola: `order_id, status_id, order_type, restaurant, pickup_address,
address_id, delivery_address, pickup_at_warsaw, prep_minutes, created_at_utc,
age_minutes, decision_deadline, id_kurier, is_koordynator, dzien_odbioru,
czas_doreczenia, zmiana_czasu_odbioru, position_in_pack, phone, uwagi`.
**Brak `delivery_city`, `customer_city` lub podobnego.**

Adres montowany (L349-352):
```python
adres_parts = [raw.get("street") or "", raw.get("nr_domu") or ""]
adres_dostawa = " ".join(p for p in adres_parts if p).strip()
if raw.get("nr_mieszkania"):
    adres_dostawa += f"/{raw['nr_mieszkania']}"
# → "Kraszewskiego 10a/15"  (brak miasta)
```

**`raw.get("miasto")`** z `edit-zamowienie` response **nigdy nie jest czytany**.
User potwierdza że pole jest w panelu (vide opis bugu), więc dostępne — tylko ignorowane.

## B. Address assembly dla geocodera

3 miejsca w runtime wołają `geocode(addr)` **bez** `hint_city`:

```
state_machine.py:243    r = geocode(deliv_addr)
panel_watcher.py:192    _dcoords = geocode(_del_addr, timeout=2.0)
shadow_dispatcher.py:302 coords = geocode(addr) if addr else None
shadow_dispatcher.py:314 coords = geocode(addr) if addr else None
```

Wszystkie wpadają w default `hint_city='Białystok'`. `geocode_restaurant()`
(L187-217) hardcoduje `Białystok` w L200. Brak różnicy między geocoderem
restauracji a klienta — oba zakładają Białystok.

## C. Nominatim/Google call parameters

`_google_geocode` (L105-128) buduje query:
```
https://maps.googleapis.com/maps/api/geocode/json
  ?address=<ENCODED>       # "{addr}, {hint_city}, Polska"
  &key=<GMAPS_KEY>
  &region=pl
  &language=pl
```

**Brak** `components=country:PL|locality:<city>`, **brak** `bounds`, **brak**
`countrycodes=pl`. Query zawiera `"Polska"` tylko jako luźny string w address.
CLAUDE.md wspomina "Geocoding uses Nominatim (Google denied)" ale kod używa
Google — niespójność (może klucz odblokowany, może cache starsze). Na to
fixem się nie zajmujemy — liczy się że *jakiekolwiek* API dostaje sprzeczny city.

OSRM fallback (L131-140) zwraca `None` — tekstowy geocoding nie jest możliwy
przez OSRM. Jedyne źródło = Google.

## D. Cache sanity check (`geocode_cache.json`)

- **Total entries:** 2511
- **Wszystkie 2511 mają `"białystok"` w kluczu** — `_normalize` dokleja zawsze.
- **Keys z `kleosin`:** 9 (tylko gdy użytkownik/skrypt podał "Kleosin" w stringu adresu)
- **Keys z `choroszcz`:** 8
- **Keys z `supraśl`:** 3
- **Keys z `warszawa`/`grajewo`:** 0 (nigdy nie trafiły do pipeline)
- **Entries poza bbox Białystok+Kleosin (lat 52.85-53.35, lng 22.90-23.35):** 8

Przykłady ewidentnie złych coords:
```
'bohaterów 8, białystok'      → 50.49, 23.94  (Lubelskie)
'piłsudskiego 26, białystok'   → 51.92, 19.14  (Łódzkie)
'wierzbowa 20a 31, białystok'  → 51.82, 17.48  (Wielkopolska)
```

Google dla tych adresów miał lepsze dopasowanie w innych miastach mimo
`region=pl` + ", Białystok, Polska" w address. Stare złe wpisy zostaną w cache
po fixie — wymagana inwalidacja.

## E. Reprodukcja bugu (konkretny match z cache)

Cache entries dla `kraszewskiego`:
```
'kraszewskiego 10a, białystok'          → 53.1362, 23.1727  [klient #466975, ZŁE — to Białystok, nie Kleosin]
'kraszewskiego 10a kleosin, białystok'  → 53.1042, 23.1185  [CORRECT Kleosin — bo string zawierał "Kleosin"]
'józefa ignacego kraszewskiego 21a, białystok' → 53.1378, 23.1756  [klient #466978, OK Białystok]
```

Odległości (haversine):
- **Kleosin 10a (prawdziwe) ↔ Białystok 21A** = **5.33 km**
- **Białystok 10a (błędnie zcachowane) ↔ Białystok 21A** = **0.26 km** ≈ `0.3 km` w Telegramie ✓

**Mechanizm:** klient Kleosin został zgeokodowany jako Białystok bo `delivery_address`
nie zawierało "Kleosin" (panel_client nie parsuje miasta). Score bundle_level3
i bundle_level2_dist liczone na tych błędnych coords → system widzi oba drops
jako **0.26 km** od siebie → bundle wins.

### Learning_log cross-confirmation

Entry 466978 (2026-04-19T08:58:08) — best=Michał Rom, **bl3=True** ("po drodze"),
score=35.88. W kolejnych propozycjach dla *innych* orderów Michał Rom
konsekwentnie pokazuje `bl2=Chicago Pizza` z małym `bl2_dist`:
- Kebab Król: bl2_dist=0.37
- Enklawa: bl2_dist=0.38
- Bar Eljot 09:55:59: **bl2_dist=1.15, score=125.79 (top)**

Wszystkie te `bl2_dist` wyliczone od **błędnego drop pointu Chicago Pizza klienta**
(Kleosin zcachowany jako Białystok 10a). Bug propagował się przez cały blok
decyzji przez 60+ minut.

## F. Blast radius

### Pliki do zmiany (core fix)

| Plik | Zmiana | LoC szacunek |
|---|---|---|
| `panel_client.py` | `normalize_order`: dodać `delivery_city` z `raw.get("miasto")` | +2/-0 |
| `geocoding.py` | `geocode()` + `_normalize()` + `geocode_restaurant()`: city explicit, bez hardcoded default | +15/-10 |
| `state_machine.py` | L243: przekazać `city` z order dict | +1/-1 |
| `panel_watcher.py` | L192: przekazać `city` z order dict | +1/-1 |
| `shadow_dispatcher.py` | L302, L314: przekazać `city` z order dict | +2/-2 |
| `common.py` | feature flag `CITY_AWARE_GEOCODING` (domyślnie True — bugfix) | +2 |

### Pliki NIEZMIENIane (confirmed)

`feasibility_v2.py`, `scoring.py`, `dispatch_pipeline.py`, `wave_scoring.py` — bug jest **niżej** (coords już błędne jak trafiają do tych modułów). Fix coords → fix bundlingu bez dotykania decision logic.

### Cache invalidation strategy

- `geocode_cache.json`: 2511 wpisów, wszystkie mają `białystok` w kluczu.
- **Option A (ostrożna):** zostawić cache, fix pisze nowe wpisy pod nowym schema kluczy. Backward compat ok — stare złe entries nadal serwują złe coords dopóki klient się nie zgłosi 2+ razy.
- **Option B (inwalidacja selektywna):** przepisać cache na nowy schemat `f"{street_lower}, {city_lower}"`. Kleosin + Białystok rozbić. Ryzyko: rate limit Google przy re-geokodowaniu ~2500 adresów.
- **Option C (minimalna):** skasować tylko 8 ewidentnie błędnych (poza bbox) + 9 z `kleosin` w kluczu (muszą być re-sprawdzone bo normalizer tworzył różne klucze). Reszta w większości OK.

Rekomendacja: **A + C** — minimalna inwalidacja + nowe wpisy poprawnie cache'ują się pod nowym schematem. Szczegóły do uzgodnienia w planie.

### Testy do aktualizacji

- Istniejące 137/137 powinny przejść bez zmian — żaden test nie asertuje
  zachowania `hint_city=Białystok`.
- Trzeba dodać `tests/test_city_aware_geocoding.py` (min 5 testów).

### Ryzyka

1. **Backward compat cache key format** — jeśli zmienimy schemat klucza w
   `_normalize`, stare 2511 wpisów staną się stale (cache miss → re-geokodowanie).
   Mitigacja: pozostawić stary format jako fallback przez X dni, albo
   zaakceptować rate limit burst.
2. **`raw.get("miasto")` może być `None`** dla starych zleceń — fallback na
   `restaurant.city` z `restaurant_coords.json` (ta restauracja wie w którym
   mieście stoi).
3. **learning_log schema** — zawiera już stare decyzje z błędnymi coords.
   Backward compat: NIE dotykamy schema, tylko przyszłe wpisy będą poprawne.
4. **Warszawa-ready:** fix musi być multi-city, bo ekspansja Warszawa za ~5 tyg.
   `restaurant_coords.json` ma `city` field (L88 bootstrap) — OK, można użyć
   jako fallback gdy klient bez miasta.

## Podsumowanie (dla planu KROK 2)

- Bug jest **3-warstwowy, ale fix ortogonalny do całej Sprint C logic.**
- Core fix: `panel_client` (1 linia) + `geocoding` (redesign signature) +
  3 callers (po 1 linii). Feature flag `CITY_AWARE_GEOCODING` default True jako
  kill-switch na wypadek regresji.
- Cache invalidation: minimalna (8 złych + 9 kleosin), nowe wpisy szybko
  się dopełniają nowym schema.
- Regresja testowa: 5 testów + sanity check że 137/137 nadal pass.
- Shadow weryfikacja: re-run 24h learning_log przez naprawiony pipeline, policzyć
  cross-city bundle eliminations.

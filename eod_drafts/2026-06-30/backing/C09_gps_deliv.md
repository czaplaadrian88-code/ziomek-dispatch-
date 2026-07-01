# C09 — ORACLE: gps_delivery_validation (#5b) — lane C (RUNTIME-ORACLE)

**Agent:** C09-gps-deliv-validation · **Tryb:** READ-ONLY · **Data:** 2026-06-30 ~17:10 UTC
**Metoda:** NIE czytam samego kodu — re-querowałem RAW GPS z `courier_api.db` (`gps_history`, 169156 wierszy, mode=ro) + własna implementacja haversine; przeliczyłem CAŁY zbiór delt od zera z `customer_dwell.json`+`orders_state.json` (NIE z `gps_delivery_truth.jsonl`); re-detekcja przyjazdu na próbie 40 zleceń; rozbicie pokrycia dzienne. **2 odpalenia — wynik identyczny (determinizm OK).**

**WERDYKT: `validated` — ground-truth — z 3 certyfikowanymi zastrzeżeniami które OGRANICZAJĄ użycie downstream (nie unieważniają pomiaru).**

---

## 0. CO TO ZA PRZYRZĄD + CO NAPĘDZA
- **Producent:** `tools/gps_delivery_validation_review.py` (timer `gps-delivery-validation` 5min, `--write`). Czyta `customer_dwell.json`+`orders_state.json` → pisze `gps_delivery_truth.jsonl` (per-zlecenie fizyczna prawda) + `gps_delivery_validation_verdict.txt`.
- **Źródło fizyki:** `nadajesz_clone/panel/backend/tools/customer_dwell_detector.py` (CROSS-REPO) — geofence 80m wokół `delivery_coords`, `arrived_at_customer` = START wizyty-klastra GPS ZAWIERAJĄCEJ doręczenie (±180s), raw GPS z `courier_api.db:gps_history`.
- **Co flipuje:** FUNDAMENT. Rozbraja ORACLE-CAVEAT „delivered_at=prawda-przyciskowa, 0/377 fizycznych GT". Karmi (gate) fizyczny pomiar **feas-carry (#3/B2)** i **O2/bundle-calib (flip-gate 02.07)** — zamiast ufać klikowi ±3min.

---

## 1. ORACLE 484363 — PASS (przyjazd dokładny; „2655→40m" nieprecyzyjne ale fizyka realna)
Niezależna re-detekcja z raw GPS courier 492, okno `[20:51:58 .. 21:38:17]` (picked_up OBECNY w state), 135 punktów:

```
trop dystansu do klienta (Filipowicza 12, dest=53.0974361,23.1331562):
 20:52:10  4216 m   (start okna — DALEKO)
 21:22:18  2061 m   ← podejście zaczyna spadać
 21:22:58   858 m
 21:23:39   218 m
 21:24:49    40 m  <-- PIERWSZY w geofence(<=80m) = arrived_at_customer
 21:29:27    27 m  <-- (po _computed_at 21:27:36)
 21:30:11    75 m  <-- (po _computed_at)
 21:36:24  2643 m   (ODJAZD)
```
- **arrived_at_customer = 21:24:49 — INDEP MATCH=True** (zgodne ze stored). Klik (button) 21:26:17 → **delta=+1.47 min** (klik 88s PO fizycznym przyjeździe). ✅
- **Fizyka realna:** monotoniczne podejście 4216m→40m (makro-trend jednoznaczny; 68/134 kroków nie-rosnących, reszta = jitter GPS ~±200m, NIE „ściśle monotoniczne").
- ⚠ **„2655→40m" = nieprecyzyjne:** start okna to **4216m**, nie 2655. 2655 nie jest punktem podejścia — najbliżej to 2643m ale to ODJAZD (21:36:24). Endpoint (40m) i kierunek (podejście) ✅; liczba startowa zmyślona/pomylona.
- ⚠ **stored `min_dist=40`/`n_in=1` ≠ moja re-detekcja `27m`/`n_in=3`:** bo detektor liczył o `_computed_at 21:27:36`, a punkty 21:29:27(27m)+21:30:11(75m) doszły PÓŹNIEJ. Detektor NIE przelicza (`customer_dwell_detector.py:223` `if dwell_min is not None and not --force: continue`; timer bez `--force`) → **first-pass zamrożony**. Przyjazd (start wizyty) STABILNY mimo to.

## 2. „KLIK ZAWYŻA MEDIANĘ +2.08min" — VALIDATED (dziś +2.12; +2.08 = stary snapshot)
Niezależny recompute CAŁOŚCI (947 zleceń gps_geofence z buttonem), **bez czytania truth.jsonl**:

| metryka | mój recompute | verdict.txt | match |
|---|---|---|---|
| median delta (button−physical) | **+2.12** | +2.12 | ✅ |
| mean | +2.26 | +2.26 | ✅ |
| p10 / p90 | +0.3 / +4.2 | +0.3 / +4.2 | ✅ |
| min / max | −3.0 / +16.9 | −3.0 / +16.9 | ✅ |
| \|delta\|>3min | 234/947 (25%) | 25% | ✅ |
| klik PRZED (neg) | 46/947 (5%) | 5% | ✅ |
| HIGH-CONF (n_in≥2) median | +2.28 (n=785) | +2.28 | ✅ |

**Klik systematycznie ~+2.1 min PO fizycznym wejściu w geofence.** Zadanie cytowało +2.08 (starszy tick) — dziś +2.12; różnica = świeżość, nie defekt.

## 3. SERIALIZACJA truth.jsonl — VALIDATED (wierna)
947 wierszy truth.jsonl vs 947 recompute → **0 niezgodności** delta. Instrument wiernie serializuje to co liczy. ✅

## 4. RE-DETEKCJA PRZYJAZDU NA PRÓBIE — 39/40 EXACT (484363 NIE jest cherry-pick)
Re-detekcja `arrived_at_customer` z raw GPS dla 40 świeżych zleceń (06-28→06-30):
- **arrival EXACT match: 39/40 (97,5%)**; within 1 GPS-tick (≤40s): 39/40.
- **stale-confidence: 3/40 (7,5%)** — re-detekcja n_in≥2 ale stored n_in=1 (zamrożony first-pass z §1 generalizuje → ~7,5% prawdziwie-„high" oznaczone „low"). Konserwatywne, NIE psuje mediany/przyjazdu.
- **1 MISMATCH (483976):** stored arrival 15:16:53, re-detekcja 15:05:53 (11 min wcześniej) — klaster wizyty (gap 12min) wciągnął punkt drive-by/pośredni → przyjazd cofnięty. delta skoczyłaby +0,6→+11,6 min. ~2,5% zleceń = niestabilny przyjazd per-order (czułość klastra/drive-by). Agregat odporny (947 próbek), per-order szum ±1-2min.
- neg-delta przy podłodze −3.0: tylko 2/46 (tolerancja ±180s w `contain` ogranicza delta≥−3.0 by-construction — artefakt immaterialny).

## 5. POKRYCIE „0/377 czy rośnie?" — NIE 0, NIE rośnie monotonicznie; VOLATILE ~19%
ALL-TIME: gps_geofence=**947** / gps_no_fix=**3818** / gps_no_contain=60 / none=54 → **fizyczne-GT = 947/4879 = 19,4%**.

| dzień | geofence(GT) | no_fix | %GT |
|---|---|---|---|
| 06-20 | 40 | 142 | 22% |
| 06-21 | 54 | 303 | 15% |
| 06-22 | 91 | 83 | **52%** |
| 06-23 | 53 | 175 | 23% |
| 06-24 | 32 | 169 | 16% |
| 06-25 | 45 | 165 | 21% |
| 06-26 | 57 | 224 | 20% |
| **06-27** | **1** | **214** | **0%** |
| 06-28 | 16 | 259 | 6% |
| 06-29 | 44 | 185 | 19% |
| 06-30 (część) | 16 | 150 | 10% |

- „0/377" z FUNDAMENT-CAVEAT = realny DZIEŃ słabego GPS (06-27: 1/215 ≈ 0%), NIE stan ustalony. Pokrycie **volatile 0-52%**, NIE rosnące. **78% dostaw = gps_no_fix** (apka floty GPS off/rzadki przy dostawie).
- ⚠ **Live verdict NIGDY nie liczy coverage%** — `ExecStart` = `--write` BEZ `--since` → `cov_pct=None` → zawsze „(coverage: podaj --since)". Operator widzi „947 z fizyczną prawdą" bez info że to 19% i zmienne.

---

## 6. CERTYFIKACJA proxy-vs-ground (KLUCZOWY CAVEAT)
- **`physical_delivered_at` (wejście w geofence 80m) = GROUND-TRUTH** dla „kurier dotarł w okolicę klienta" — niezależnie odtworzony z raw GPS (484363 + 39/40 próby). To realny pomiar fizyczny, nie proxy.
- **ALE delta +2.12 = (klik − wejście-w-geofence)** MIESZA: realne ostatnie metry (80m→drzwi) + przekazanie + opóźnienie kliku. **NIE jest czystym błędem przycisku.** Interpretacja „przycisk zawyża dostawę o 2min" = **proxy-certified** (górna granica lag-u kliku); sam pomiar przyjazdu = ground-truth.
- **PUŁAPKA dla downstream (feas_carry/O2/#2):** podmiana `button→physical` do liczenia spóźnienia R6/SLA da wynik OPTYMISTYCZNY o czas przekazania (~2min). Physical=DOLNA granica momentu doręczenia, nie sam moment.

## 7. INWARIANTY-TRIPWIRE (dostosowane do tego przyrządu, nie route)
- ✅ recompute = verdict (delta median/mean/percentyle/odsetki — 1:1).
- ✅ truth.jsonl wiernie serializuje (947/947, 0 mismatch).
- ✅ przyjazd reprodukowalny (39/40 exact, 484363 PASS).
- ✅ determinizm (2 odpalenia identyczne).
- ⚠ ZERO fikcyjnych punktów: detektor odrzuca drive-by (`gps_no_contain`=60 poprawnie), ale klaster 12-min MOŻE wciągnąć punkt pośredni (483976) → ~2,5% niestabilny przyjazd.
- ⚠ first-pass min_dist/n_in/confidence zamrożone (brak --force) → ~7,5% stale-low confidence.

## 8. CO FLIPUJE / ZNACZENIE DLA ROJU
- Przyrząd **VALIDATED jako producent fizycznej prawdy** — feas_carry/O2/#2 MOGĄ go używać zamiast kliku, ALE: (a) traktować `physical` jako DOLNĄ granicę (offset ~+2min przekazania, spójnie), (b) liczyć się z **19% volatile coverage** (na dniach low-GPS, np. 06-27, BRAK prawdy fizycznej → walidacja flipu niemożliwa tego dnia; próba NIE-losowa, biased ku kurierom z GPS on).
- A4 status „🟢 VALIDATED (484363 PASS, 2655→40 monotonicznie)" — **duch potwierdzony**, ale `2655` i `monotonicznie` nieprecyzyjne (4216→40, jitter); A4 należy skorygować przy scalaniu.

---

## TABELA POKRYCIA (coverage_declared / coverage_gaps)

| Zbadane (coverage_declared) | Jak |
|---|---|
| `gps_delivery_validation_review.py` (pełny) | Read + recompute jego output 2-tą metodą |
| `customer_dwell_detector.py` cross-repo (pełny) | Read + re-implementacja geofence/haversine |
| `customer_dwell.json` (4876 kluczy) | Rozbicie źródeł + recompute delt |
| `gps_delivery_truth.jsonl` (947) | Cross-check serializacji 947/947 |
| `courier_api.db:gps_history` (169156) | Niezależne re-query raw GPS (mode=ro) |
| `gps_delivery_validation_verdict.txt` + service/timer | Cat + ExecStart (brak --since) |
| Oracle 484363 + próba 40 zleceń | Re-detekcja przyjazdu z raw GPS |
| Pokrycie dzienne 06-20→06-30 | Agregacja per-day source |

| NIE zbadane (coverage_gaps) | Powód |
|---|---|
| `bundle_calib_review.py` jako 2-gi konsument dwell | Osobny instrument (inny C-agent); tu tylko #5b |
| INGEST raw GPS android→`gps_history` wierność | Granica apki (STOP na dyspozytorni); DB przyjęte jako wierne |
| `delivered_at` button vs panel rutcom | To proxy POD testem, nie re-weryfikowany u źródła |
| root 1 mismatch (483976) drive-by vs merge wizyt | Scharakteryzowany, nie wyczerpująco prześledzony |
| `restaurant_dwell_detector.py` (bliźniak pickup-side) | Dotyczy poślizgu odbioru, nie tego delivery-claim |

**Artefakty:** `scratchpad/c09_oracle.py` + `c09_part5.py` (read-only, output do scratchpad; ZERO zapisu do dispatch_state, ZERO --write/--notify).

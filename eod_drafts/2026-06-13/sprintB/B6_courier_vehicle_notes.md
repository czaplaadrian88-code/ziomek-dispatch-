# B6 — `courier_vehicle.draft.json` — notatka pokrycia / gaps

**Data:** 2026-06-13 (generated_at w `_meta` pliku)
**Deliverable:** `/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-13/sprintB/courier_vehicle.draft.json`
**Builder (reproducible, read-only):** `build_courier_vehicle_draft.py` (ten katalog)
**Konsument docelowy:** `dispatch_v2/pln_objective.py` → funkcja `_vehicle_for(cid)` (E7 re-tune wag, at#131 17.06)
**Status:** DRAFT. Podmiana na live `dispatch_state/courier_vehicle.json` = OSOBNA akcja za ACK — NIE teraz.

---

## Co to jest i po co

`pln_objective.py` (SP-B2-PLN, shadow ON od 11.06 14:13) liczy wartość PLN kandydata:

```
V = 6,33 − koszt_km(vehicle)·Δkm − 14·P(breach) − 0,20·leżenie − opp·(blokada+czekanie)
koszt_km = 0,90 (firmowe)  |  0,00 (własne)
```

`koszt_km` zależy od tego, czy kurier jeździ **autem firmowym** czy **własnym**.
Logika ekonomiczna (audyt Bartek 2.0, `agent_econ/REPORT.md`): kurierzy płaceni **od dostawy**
(8 PLN firmowe auto / 13 PLN własne); przy aucie firmowym Ziomek ponosi marginalny koszt paliwa
**0,90 PLN/km**, przy własnym aucie koszt km **nie obciąża** Ziomka (= 0). Dlatego `własne → 0`.

Bez tego pliku `_vehicle_for` zwraca dla KAŻDEGO cid `"firmowe"` (0,90) — konserwatywnie pełny koszt.
Ten draft daje E7 prawdziwe rozróżnienie dla 6 kurierów z autami własnymi.

## Schema (dokładnie taki, jaki czyta konsument)

`_vehicle_for` robi `json.load(plik).get(str(cid), "firmowe")` — czyli **klucze cid muszą być na TOP-LEVEL**.

```json
{
  "_meta": { ... },          // ignorowane przez konsumenta (klucz nie-cyfrowy)
  "179": "wlasne",
  "21":  "firmowe",
  ...                          // 48 par cid -> "firmowe"|"wlasne"
  "_detail": [ ... ]          // audyt, ignorowane przez konsumenta
}
```

Wartości: `"wlasne"` lub `"firmowe"` (konsument normalizuje też `"własne"/"own"` → wlasne; cokolwiek
innego → firmowe). Dozwolone wartości w panelu: `vehicle_owner ∈ {company, own}` → mapuję `own→wlasne`,
`company→firmowe`.

## Źródło danych (kanon)

**Panel Postgres `nadajesz_panel.courier`** @127.0.0.1:5433 (read-only SELECT):
- `external_id` = **cid** w gastro (klucz, którego używa Ziomek — zgodny z `courier_tiers.json`)
- `vehicle_owner` ∈ `{company, own}` — kolumna NOT NULL DEFAULT 'company' (migracja `4fc6a8422ba8`)
- panel = kanon kierowcy (FLT-04 „Kierowcy"); zmiana firmowe/własne robiona przez operatora w panelu,
  z append-only audytem w `courier_history` (kind='vehicle').

**Dlaczego NIE `fleet_analytics.db`** (był wymieniony w pamięci jako możliwe źródło):
sprawdziłem — `vehicles.cost_per_km` tam = **0,8 dla WSZYSTKICH 12 aut**, brak flagi firmowe/własne,
a jedyne „otwarte" przypisanie (valid_to IS NULL) to `cid=484 → SAMPLE` (seed MVP). Ta baza trzyma
przypisanie *rejestracji auta per dzień* (most read-only z grafiku), **nie** semantykę własności.
Do kosztu km / własności jest bezużyteczna. Kanon = panel `courier.vehicle_owner`.

## Pokrycie

| Metryka | Wartość |
|---|---|
| Kurierzy w panelu (total) | 359 |
| Aktywni + nie-zarchiwizowani | 49 |
| **Zmapowani w draftcie** | **48** (49 − wirtualny Koordynator cid=26) |
| `wlasne` (own) | **6** |
| `firmowe` (company) | 42 |
| Roster Ziomka (`courier_tiers.json`, bez `_meta`) | 53 cid |

**6 kurierów z autem własnym (`wlasne`, koszt km 0):**

| cid | imię | audyt zmiany (courier_history) |
|---|---|---|
| 179 | Gabriel | 2026-06-07 (operator, panel) |
| 207 | Marek | 2026-06-07 (operator, panel) |
| 413 | Mateusz O | 2026-06-07 (operator, panel) |
| 515 | Szymon P | 2026-06-07 (operator, panel) |
| 370 | Jakub OL | **brak wpisu w courier_history** (niższa proweniencja — patrz Gaps) |
| 457 | Adrian Cit | **brak wpisu w courier_history** (niższa proweniencja — patrz Gaps) |

Zgadza się to z TODO pamięci (FLT-05 NEXT: „auta 'własne' dla Gabriela O/Mateusza O/Marka") —
te są ustawione, plus Szymon P, Jakub OL, Adrian Cit.

## Walidacja krzyżowa z rosterem Ziomka

- **Wszystkie 6 cid `wlasne` są obecne** w `courier_tiers.json` (live roster dispatchu) → E7 ich użyje.
- 48/49 aktywnych kurierów panelu jest w rosterze Ziomka. Jedyny brakujący: **cid=26 = Koordynator**
  (wirtualny worek przetrzymujący czasówki, NIE kierowca) — świadomie pominięty w draftcie.
- Każdy cid z rostera Ziomka istnieje w panelu (zero sierot).

## Walidacja wykonawcza (przeszła)

Wycelowałem żywy `pln_objective._vehicle_for` w draft (przez `COURIER_VEHICLE_PATH`):
- 6 cid own → `wlasne` ✓; próbka company (123/21/393/508/531) → `firmowe` ✓; brakujący cid → `firmowe` ✓.
- end-to-end `compute_pln_value`: V(own) − V(firmowe) = **+4,50 PLN** dla Δkm=5 (= 0,90×5) ✓.
- JSON poprawny, 48 kluczy cid na top-level, `_meta`/`_detail` obecne i nieszkodliwe dla konsumenta.

## Fallbacki / decyzje projektowe

1. **Domyślnie firmowe.** Konsument i tak ma fallback `→ firmowe` dla brakującego cid, więc plik
   minimalny (tylko 6 `wlasne`) byłby funkcjonalnie wystarczający. Zapisuję jednak **wszystkie 48
   aktywnych jawnie** — auditowalność i czytelność (widać świadomie, że reszta = firmowe, nie „zapomniane").
2. **Mapowanie wartości, nie liczb.** Piszę `"firmowe"/"wlasne"` (a nie surowe `0.90/0.0`), bo to
   format, który `_vehicle_for` faktycznie czyta; stawki PLN/km są w `pln_objective.py`
   (`PLN_KM_COST_FIRMOWE=0.90`, `PLN_KM_COST_WLASNE=0.0`, env-overridable). Jeśli kiedyś trzeba
   per-kurier RÓŻNE stawki km (nie tylko 0/0,90) — to wymaga zmiany kontraktu `pln_objective.py`
   (dziś binarny). W `_detail` dorzuciłem `km_cost_pln` per kurier dla przyszłego użycia.
3. **cid=26 pominięty** (wirtualny Koordynator).
4. **Zarchiwizowani / nieaktywni pominięci** (307 archived, 305 inactive) — to historyczni kierowcy,
   nie ma ich w rosterze dispatchu.

## Gaps / ryzyka (uczciwie)

- **Proweniencja 2/6 own:** `cid=370` (Jakub OL) i `cid=457` (Adrian Cit) mają w panelu
  `vehicle_owner='own'`, ale **bez wpisu w `courier_history` kind='vehicle'** (wszystkie sześć
  ma `updated_at` 2026-06-10 12:14:44 — wygląda na zbiorczy import/seed, który ominął `record_attr`).
  To wciąż bieżąca prawda panelu, więc je uwzględniam, ale **przed flipem warto, żeby Adrian
  potwierdził te dwa** (czy faktycznie jeżdżą własnym autem). Pozostałe 4 (179/207/413/515) =
  operator-confirmed (panel) 2026-06-07.
- **Stawka km jednolita 0,90 / 0,00.** Draft niesie tylko własność (binarnie). Jeśli ekonomia kiedyś
  wymaga prawdziwego cost/km per pojazd (różne auta, spalanie), trzeba osobnego pola + rozszerzenia
  kontraktu `pln_objective.py`. Dziś `fleet_analytics.vehicles.cost_per_km` = 0,8 dla wszystkich
  (placeholder), więc i tak nie ma realnego per-vehicle cost do wzięcia.
- **Świeżość = „na teraz".** Własność może się zmienić (kurier przesiada się firmowe↔własne). Plik to
  snapshot 2026-06-13. Docelowo: regenerować z panelu cyklicznie (most), nie ręcznie. Dla E7 (at#131
  17.06) snapshot jest aktualny.

## Procedura podmiany na live (NIE teraz — za ACK)

1. ACK Adriana (szczególnie cid 370 + 457 — patrz Gaps).
2. (opcjonalnie) regeneracja `build_courier_vehicle_draft.py` tuż przed flipem (świeży snapshot panelu).
3. `cp dispatch_state/courier_vehicle.json dispatch_state/courier_vehicle.json.bak-<data>` jeśli istnieje
   (dziś NIE istnieje — będzie to pierwszy zapis).
4. Atomowy zapis draftu jako `dispatch_state/courier_vehicle.json` (temp+fsync+rename).
   `_vehicle_for` ma mtime-cache → podchwyci automatycznie, **bez restartu** (czysta funkcja PLN).
5. Weryfikacja: w shadow_decisions pola `pln_vehicle` dla cid 179/207/370/413/457/515 = `wlasne`.
6. Rollback: usuń plik (lub przywróć .bak) → konsument wraca do „wszyscy firmowe 0,90".

Uwaga: to zasila TYLKO telemetrię PLN w cieniu (`ENABLE_PLN_OBJECTIVE_SHADOW` ON). Użycie PLN
w DECYZJI dispatchu to oddzielny, dalszy ACK (E7).

# KANDYDAT do ziomek-change-protocol — „Ziomek re-sekwencjonuje worek globalnie (wypełnia martwy czas przed czasówką)"

**Data:** 2026-06-25 | **Źródło case:** Mateusz Ostapczuk (cid 413), worek 483257/483268/483234/483263 (Goodboy→Sek, Paradiso→42pp127, PKS-czasówka→Bełzy, PKS→42pp72j)
**Status: NIE-FLIP. Lewar JUŻ w cieniu (bundle_calib_shadow, review 02.07). Ten dokument = ETAP 0/1 werdykt + 1 actionable sub-task (pomiar).**

---

## MOJA ZMIANA (proponowany lewar)
Silnik, budując/odświeżając plan worka, ma **globalnie re-sekwencjonować wszystkie przeploty** (w tym wstawiać dostawę między odbiory, by zapełnić martwy czas czekania na czasówkę) pod **skalibrowanym objektywem** (ready-anchor R6 + lateness czasówki), zamiast zostawiać sekwencję z inkrementalnej insercji (`optimization_method=incremental`).

Geneza: konsola pokazała Mateuszowi O trasę „4 odbiory → 4 dostawy"; intuicja Adriana („Goodboy→Paradiso→[Sekcyjna jeśli czas]→PKS→…") = wypełnić ~8 min stania pod PKS (czasówka 16:46) dostawą Sekcyjnej 1 km obok.

---

## ETAP 0 — STAN NA ŻYWO (measure-first) → **lewar już cieniowany; case 413 = NO-GO**

1. **JUŻ ROBIONE W CIENIU.** `tools/bundle_calib_shadow.py` (timer `dispatch-bundle-calib-shadow`, **LIVE od 25.06 14:17 UTC, co 5 min**, env `ENABLE_BUNDLE_CALIB_SHADOW=1`, λ=1,5). Robi DOKŁADNIE ten lewar:
   - `_all_valid_perms` = **brute-force WSZYSTKICH poprawnych przeplotów** ≤5 zleceń (w tym dostawa-między-odbiorami).
   - `_walk_calib`: `czas_kuriera` jako **podłoga wyjazdu** → modeluje czekanie na czasówkę (martwy czas).
   - objektyw **O2 = overage + 1,5·czas_late**, finish jako tie-break → ceni i świeżość, i niemarnowanie czasu, i punktualność czasówki.
   - loguje served (silnik) vs CALIB (best) + `bundle_improved` + delty → `bundle_calib_shadow.jsonl`. **Review one-shot 02.07 07:00 UTC** (`tools/bundle_calib_review.py`, werdykt GO/NO-GO/INCONCLUSIVE na Telegram).
   - → **Budowanie nowego shadowa = redundancja. NIE robić** (protokół ETAP 0: „już LIVE → NIE rób, werdykt z liczbami").

2. **Case Mateusza O (413) — Ziomek JEST optymalny pod realnym ruchem.** Wierna rekonstrukcja worka przez objektyw bundle_calib (OSRM **z mnożnikiem korków**, drive 54,6 min ≈ ×1,55):
   | trasa | O2 | overage | finish | drive | Goodboy carry | Bełzy carry |
   |---|---|---|---|---|---|---|
   | **Ziomek (served=CALIB)** | **10,8** | 10,8 | 69,6 | 54,6 | 27,7 | 45,8 |
   | TWOJA (Sekcyjna w dziurze) | 12,1 | 12,1 | 70,9 | 56,4 | 21,1 | **47,1** |

   Brute-force **2520 przeplotów** wybrał trasę Ziomka. Przeplot Sekcyjnej: Goodboy świeższy (−6,6) ALE Bełzy gorszy (+1,3) + drive +1,8 → netto O2 GORZEJ. Pod ruchem peak nie ma martwego czasu do zapełnienia (kurier dobija PKS ~16:46 naturalnie), a objazd kosztuje więcej niż oszczędza.
   ⚠ **Mój pierwszy wynik („Twoja lepsza o 6 min") był liczony raw-OSRM (mult 1.0)** — ta sama pułapka, którą notatka `sweep-r6-anchor` już złapała (raw zawyża dziurę, zaniża objazd). Realny symulator obalił.

3. **Bełzy R6 (46 min > 35) jest nieunikniony w KAŻDYM przeplocie** — to samotny punkt NW vs 3-stopowy klaster E. Worek strukturalnie rozjechany, nie błąd kolejności.

---

## ETAP 1 — ŹRÓDŁO (nie objaw)
Gdyby lewar był realny, warstwa-przyczyna = **trójka RAZEM** (Załącznik A protokołu):
`feasibility_v2` (greedy/bruteforce ślepy na ck/R6 — P-3 audytu) ↔ `route_simulator_v2._count_sla_violations` (anchor pickup_at zamiast ready — bug kalibracyjny `sweep-r6-anchor`) ↔ `plan_recheck._sweep` (re-sekwencja). NIE łatka na konsoli. To jest dokładnie zakres GO bundle_calib_review (trójka, osobny sprint, flaga OFF→shadow→ON).

## ETAP 2 — HARD vs SOFT / inwersje
Dotyka **P-3** (frozen-window w greedy) i objektywu sweepa. NIE cofa świadomych inwersji P-1..P-7. Konflikt z **R-FLEET-LEVEL** (re-sekwencja per-worek ≠ balans floty) — ale to re-sekwencja WEWNĄTRZ już-przypisanego worka, nie re-alokacja. Decyzja progowa (count-R6 vs minuty-overage) = **PYTAJ Adriana** (niżej).

## ETAP 3 — MAPA KOMPLETNOŚCI (klasa: feasibility+selekcja-sekwencji+kanon)
`check_feasibility_v2` ORAZ `route_simulator_v2` (greedy+bruteforce+OR-Tools) ORAZ `plan_recheck` (`_gen_one_bag_plan`/`_sweep`/`_apply_canon_order_invariants`) — wszystkie trzy RAZEM, inaczej rozjazd. + serializer A+B + parytet konsola↔apka. (Pełne wdrożenie = osobny sprint PO GO 02.07.)

## ETAP 4/5 — DOWÓD POZYTYWNEGO WPŁYWU → **na razie INCONCLUSIVE (mała próbka)**
Korpus bundle_calib (25 rekordów multi-order, ~1 dzień):
- CALIB≠served (re-sekwencja zmienia trasę): **14/25 = 56%**
- materialna poprawa: **16% flagą / 20% kryterium augmented** (overage≥5 LUB finish≥5 min); gdy fires — **DUŻO** (median −20,6 min overage, −18 min finish)
- **regresje (CALIB gorszy R6-count): 12%** — powyżej progu NO-GO review (10%); artefakt O2 (handluje liczbę-R6 za minuty-overage; cid 393)
- median NETTO Δoverage +0,6 / Δfinish 0,0 → wygrane skupione w kilku workach (515, 484, jeden snapshot 413), większość (jak 413-peak) Ziomek już optymalny.
→ próbka < MIN_MULTI=20 unikalnych worków → **INCONCLUSIVE, przedłużyć do 02.07** (dokładnie plan).

---

## ⭐ ZROBIONE 25.06 — review zaktualizowany (o2-consistent) + 1 pytanie zostawione w werdykcie

**Luka pomiarowa zaniżająca werdykt 02.07 (NAPRAWIONA):** stara flaga `_bundle_improved` + bramka `bundle_improved_pct≥20%` + count-regres liczyły poprawę jako spadek *liczby* R6 → **sprzeczne z objektywem O2** (overage+1.5·czas_late, λ=1.5 już ACK Adrian), na którym shadow jest skalibrowany. Dowód zaniżenia (flaga=False mimo dużej poprawy): cid 515 overage 67→30/−29,5min finish; cid 515 77→38/−40,2; cid 484 86→67/−18,2.

**KLUCZ:** CALIB = argmin O2 po WSZYSTKICH poprawnych przeplotach, a **served jest jednym z nich → ΔO2 ≥ 0 by construction** (empirycznie 0/16 worków gorszych) → **regres-O2 ≡ 0**. Cała „regresja 12-19%" to artefakt count-vs-minutes (CALIB mniej minut łącznie, ale czasem +1 zlecenie ponad 35).

**Naprawa (`tools/bundle_calib_review.py`):** bramka PIERWOTNA = objektyw O2 (`improved_o2_pct≥20` ΔO2≥2min + `regress_o2_pct<5`); count-lens (late-klienci) WTÓRNIE w raporcie. Backup `.bak-pre-o2-consistent-2026-06-25`, py_compile OK, dry-run OK. Serwis odpala `python -m dispatch_v2.tools.bundle_calib_review` → **edycja pliku = update, bez restartu, fires 02.07.**
**Dry-run 25.06 (39 worków):** `improved_O2 23,1% · ΔO2 med 4,45 · regres_O2 0% · count-regres 18,8% · flaga-legacy 10,3%` → werdykt **`GO-DECYZJA`**. (Stara logika: NO-GO — flaga 10,3%<20% + count-regres 18,8%≥10% → **artefakt zabiłby lewar GO-pod-własnym-objektywem**.)

**✅ DECYZJA ADRIANA 25.06: BRAMKOWAĆ MINUTAMI (O2) + ACK NA FLIP** („zatwierdzam GO-DECYZJA przed flipem"). `_verdict` zmienione: `GO-DECYZJA`→**czysty GO** gdy `improved_o2_pct≥20 + regres_O2<5%`; count-lens = informacyjny (nie bramkuje). Re-dry-run (47 worków): improved_O2 **21,3%**, regres_O2 0% → **GO**. ⚠ outcome-join sla_log n=0 (sprawdzić przed 02.07).

## ETAP 6/7 — DEPLOY/ROLLBACK
Brak deployu SILNIKA. Zmieniony tylko read-only review tool (`bundle_calib_review.py`), rollback = `cp .bak-pre-o2-consistent-2026-06-25` / git. Zero wpływu na decyzje dispatchu. Rejestr joba: memory `shadow-jobs-registry`.

---

## WERDYKT
1. **NIE budować nowego shadowa** — `bundle_calib_shadow` to dokładnie ten lewar, LIVE, review 02.07.
2. **Case Mateusza O = Ziomek już optymalny** pod realnym ruchem (brute-force 2520 to potwierdza); Twój przeplot pomaga tylko przy luźnym ruchu, którego peak nie ma.
3. **Lewar globalnie ma wartość** (515/484/snapshot-413: −20..40 min overage/finish) — pod objektywem O2 regres ≡ 0 by construction, improved_O2 ~21-23%.
4. **Pomiar naprawiony (o2-consistent).** ✅ **Decyzja Adriana 25.06: bramka MINUTAMI (O2) + ACK na flip.** `GO-DECYZJA`→czysty GO; count-lens informacyjny.
5. **FLIP zbramkowany na review 02.07** (tydzień danych — dziś GO ledwie ponad progiem). PO GO → sprint silnika: trójka `feasibility_v2`+`route_simulator_v2`+`plan_recheck` RAZEM, flaga OFF→shadow→ON, pełna regresja+e2e+rollback (PRZYKAZANIE #0 ETAP 1-7). Wpis w `todo_master.md`. NIE flipuję teraz (measure-first).

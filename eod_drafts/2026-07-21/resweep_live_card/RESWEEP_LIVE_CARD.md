# KARTA GO/NO-GO — flip `PENDING_RESWEEP_LIVE` (de-pile LIVE)

**Data:** 2026-07-21 · **Autor:** subagent CTO (read-only) · **Status: PRE-STAGE, ZERO zmian w repo/flagach.**
**Decyzja o flipie = WYŁĄCZNIE Adrian.** Ten dokument liczy progi na realnym korpusie shadow i daje rekomendację.

Dane: `dispatch_state/pending_global_resweep.jsonl` (12 329 rekordów, 8 105 ticków, 24.06→21.07.2026).
Census maszynowy: `census.json` (obok). Kod: `tools/pending_global_resweep.py`.

---

## 0. Co to jest i co ROBI flip (z plik:linia)

Ziomek liczy propozycję JEDNORAZOWO przy `NEW_ORDER`. Gdy wisi kilka nieprzypisanych zleceń naraz,
ten sam „najlepszy" kurier (często stojący pod restauracją / bez GPS w centrum) bywa proponowany do
WSZYSTKICH, choć jadą w różne strony. `pending_global_resweep` co ~1 min bierze wszystkie wiszące
zlecenia i alokuje je GLOBALNIE (sekwencyjny greedy z wirtualnym doładowaniem worka), więc drugie
zlecenie „w inną stronę" dostaje u tego kuriera gorszy score i trafia do innego.

**Ścieżka wykonania flipa (`PENDING_RESWEEP_LIVE=True`):**
1. `run_once` (l.417) — no-op gdy master `ENABLE_PENDING_RESWEEP` OFF (jest ON). Zbiera `hanging` =
   zlecenia z `pending_proposals.json` które są **wciąż `status=="planned"`** i mają pickup+delivery coords
   (l.443-455). Cap `MAX_HANGING=8` (l.460).
2. `_live_armed = C.flag(FLAG_LIVE) and live_gate_open()` (l.472). **`live_gate_open()` (l.319) wymaga
   `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` — DZIŚ ON → brama otwarta, flip realnie by uzbroił live.**
3. `global_allocate` (l.137) liczy alokację. `_live_apply` (l.339) dla wierszy `would_repropose` **podmienia
   `entry.decision_record` w `pending_proposals.json`** na serializację NOWEGO kuriera (l.393-395).
4. **Zakres = TYLKO propozycje przed przypisaniem** (konsola/1-klik koordynatora). **NIE dotyka Telegrama**
   (l.342, wyciszony 26.06 = nietykalny). **NIE przenosi już przypisanych** zleceń (to robi osobny
   reassignment-forward, nie ten plik).
5. Bezpieczniki live: `LIVE_MAX_ACTIONS_PER_TICK=3` (l.336) — max 3 podmiany/tick. TOCTOU-guard w locku
   (l.388-392): podmiana tylko jeśli wpis nadal istnieje i nadal wskazuje `proposed_cid` — inaczej skip
   (koordynator już kliknął / inny pisarz zmienił). Lock = kanon `pending_proposals_store.locked_mutate` (fcntl).

**Interakcja z `ENABLE_CLAIM_LEDGER_INVARIANT_HARD` (od dziś ON, drop-feral):**
W `global_allocate` (l.240-273) każdy claim jest sprawdzany `_check_feral_claim` względem zaakceptowanych
claimów sweepu; feralny (podwójna rezerwacja kuriera bez spójnego wzrostu worka) jest **DROPowany** —
allocation→`DROP_FERAL_CLAIM`, `would=False` (l.528), NIE trafia do `_results_out` ani ścieżki live (l.258-259).
**Efekt: HARD może uczynić live TYLKO bezpieczniejszym** (odfiltrowuje podwójne rezerwacje zanim by je
zastosował). W korpusie: **0 breaches, 0 feral-drops** historycznie (pole obecne w 18 rekordach — HARD dopiero
dziś ON). Ryzyko kolizji flip↔HARD: brak.

---

## 1. Census korpusu (wolumen i stabilność)

| Metryka | Wartość |
|---|---|
| Rekordy / ticki / dni | 12 329 / 8 105 / 27 dni (24.06–21.07) |
| Ticki z ≥1 wiszącym | 8 105; z ≥2 wiszącymi: 2 766 (34%) |
| Wiersze `would_repropose` | **3 250 (26,4% wszystkich)** |
| Rozkład reasonów `would` | rozjazd_kierunkow 1 263 · proponowany_wypadl 1 166 · lepszy_kurier 821 |
| `changed` (new_cid≠proposed_cid) | 3 800; z tego `would` 3 250, `zmiana_marginalna` (poniżej marginu) 550 |
| delta_vs_now `would` (CZYSTE, bez sentineli 1e6) | p50 **84,7** · p80 241,4 · p90 468,7 · p95 826,2 pkt |
| Stabilność dzienna | mechanizm NIE no-op: `would` codziennie 20–35% wierszy; rozjazd obecny każdego dnia (07-19: 177 would / 70 rozjazd; 07-20: 67/16) |

**Uwaga o sentinelach:** 258 wierszy ma `|delta_vs_now|>1e6` (score −1e9 = twardy odrzut proponowanego
kuriera). To realny sygnał (`proponowany_wypadl`/`lepszy_kurier`), ale zaburza surowe percentyle — dlatego
wyżej podane wartości CZYSTE. Marginy realnych podmian są DUŻE (p50=84,7 ≫ margin 15) → to nie są zmiany na styk.

**Top odciążani ↔ dociążani** (ci sami kurierzy po obu stronach — floot się przetasowuje):
odciążani: 413 Mateusz Ostapczuk (563×), 179 Gabriel Ostapczuk (439×), 515 Szymon Parys (218×).
dociążani: 413 (353×), 179 (319×), 447 Dawid Kalinowski (294×).

---

## 2. Problem ownera zmierzony wprost: „dwa zlecenia w różne strony do tego samego kierowcy"

**Danych o współrzędnych w rekordzie NIE ma** (są `delivery_address` string + `new_deliv_spread_km` dla
worka nowego kuriera). Shadow_decisions.jsonl zrotował się do 1 079 linii (tylko ostatnie ~dni), więc join
historyczny po współrzędnych niemożliwy. Mierzę więc DWA sygnały:

**A. Proxy adresowy (bezpośredni):** grupuję po `(tick, proposed_cid)` — ile wiszących zleceń dostało tego
samego proponowanego kuriera i czy mają różne adresy docelowe.
- Grup pile-on (≥2 zlecenia na jednego proponowanego kuriera, ten sam tick): **2 033**.
- Z ≥2 RÓŻNYMI adresami docelowymi: **2 033 = 100,0%.** Koncentracja: 2 zlec. 1 428×, 3 zlec. 380×, 4+ zlec. 181× (do 7).
- Przykład (cid 413): jednocześnie proponowany na Sybiraków 16a + Bitwy Białostockiej 14 + Żurawia 14b — trzy różne kierunki.

**B. Metryka silnika (autorytatywna):** `g_maxpile_before/after` + `g_spread_improved` liczone z prawdziwego
re-scoringu (który zawiera R1 spread 8km i cosinus kierunku).
- Ticki z realnym pile-on (`maxpile_before≥2`): **1 989**.
- Z tego resweep ROZBIŁ pile-on (`spread_improved`): **1 350 = 67,9%.** Reszta (32%) — rozbicie nie poprawiłoby
  geometrii (kurier faktycznie najlepszy dla obu / brak alternatywy feasible).

**Wniosek:** problem ownera jest realny i częsty — ~1 350 sytuacji/27 dni (≈50/dzień), gdzie jeden kurier był
proponowany na 2+ zlecenia w różne strony, a resweep by je rozdzielił. To rzeczy dziś robione ręcznie przez koordynatora.

**⚠ Ważny trop U ŹRÓDŁA:** cid 179 i 413 to TOP over-proposed i tutaj, i w otwartym bugu no-GPS center-score
(`memory/ziomek-nogps-center-score-bug-2026-07-19`). Pile-on jest w dużej części SKUTKIEM tego, że kurierzy
bez GPS lądują w `BIALYSTOK_CENTER` (blisko wszystkiego) → wygrywają wszystko → resweep to potem rozbija.
**Resweep LIVE łatałby skutek błędu scoringu, którego fix należy do trwającego zadania no-GPS.** To nie
dyskwalifikuje flipa, ale każe go traktować jako komplementarny, nie zamiast fixu źródłowego.

---

## 3. Progi GO/NO-GO (analogicznie do karty `nogps_measure/GO_NO_GO.md`)

Ponieważ ścieżka live jest UZBROJONA dopiero po flipie, telemetrii „live_acted" jeszcze nie ma. Progi mierzą
się na 48h REALNEGO cienia po ewentualnym flipie `ENABLE_GLOBAL_ALLOC_WRITE`-równoległym pomiarze, ORAZ z
obecnego korpusu shadow (dla wolumenu/regresji). Metryka docelowa MUSI być lepsza, nie tylko „bez regresji".

| # | Bramka | Próg GO | Obecny pomiar (shadow) | Werdykt |
|---|---|---|---|---|
| G1 | **Wolumen** — mechanizm nie jest no-op | ≥20 `would`/dzień w ≥5 z 7 ostatnich dni | 21–177/dzień, 12/12 dni ≥15 | ✅ |
| G2 | **Metryka docelowa (de-pile)** — rozbicie pile-on w różne strony | ≥60% ticków `maxpile_before≥2` kończy `spread_improved` | **67,9%** (1350/1989) | ✅ |
| G3 | **Siła zmiany** — nie zmiany na styk | delta_vs_now p50 ≥ 2×margin (≥30) | p50 **84,7** | ✅ |
| G4 | **REGRESJA: KOORD-rate** — live nie może zwiększyć zleceń bez kuriera | odsetek `no_courier`/`brak_feasible_KOORD` w allocation ≤ baseline i NIE rośnie w 48h live-shadow | reason `brak_feasible_kuriera_KOORD` = 0 w korpusie (nigdy nie wystąpił) | ✅ (baseline czysty) |
| G5 | **REGRESJA: km zwycięzców** — nowy kurier nie może dowozić dalej | mediana `new_km_to_pickup` ≤ mediana km proponowanego pierwotnie; brak wzrostu p95 spread nowego worka >8km (R1) | do zmierzenia w 48h live-shadow (dołączyć `km` starego kuriera do logu — DZIŚ NIEobecny) | ⚠ **LUKA POMIAROWA** |
| G6 | **NOWY guard anty-ping-pong: churn przełożeń/h** | median target-flipów **na zlecenie** ≤1 w oknie życia; live-swapów **na kuriera** ≤3/h; udział zleceń z ≥2 różnymi celami ≤10% | shadow: **228/1330 zleceń (17%)** miało ≥2 różne cele; 398 flipów; 57/2445 ticków biłoby cap 3 | ⚠ **PONIŻEJ PROGU — wymaga twardego guardu** |

---

## 4. Guard anty-ping-pong (NOWY, wymagany przed flipem)

Shadow liczy `proposed_cid` jako PIERWOTNĄ propozycję (nie aktualizuje pending), więc 17% ping-ponga to
GÓRNA granica — pod live `proposed_cid` aktualizuje się po każdej podmianie, co tłumi oscylację A→B→A, ale
jej nie eliminuje (świat może znów faworyzować A). Ryzyko: koordynator widzi „skaczącą" propozycję.

**Rekomendowany guard (do wdrożenia protokołem #0 PRZED flipem, nie sam flip):**
- **Hystereza:** po podmianie zlecenia X, nie podmieniaj go ponownie przez `RESWEEP_LIVE_COOLDOWN_MIN` (proponuję 10 min),
  chyba że proponowany aktualnie wypadł z puli feasible (`prop_now_score is None`).
- **Podniesiony margin dla RE-podmiany:** druga+ podmiana tego samego zlecenia wymaga delty ≥2×margin (≥30), nie 15.
- **Licznik churnu w jsonl:** `live_swaps_per_courier_last_h`, `order_flip_count` — jako guardy shadow z progami G6.
- Cap `LIVE_MAX_ACTIONS_PER_TICK=3` istnieje (l.336) i rzadko bije (57/2445 ticków) — zostaje.

---

## 5. Luki pomiarowe do domknięcia w 48h live-shadow (przed APPLY)

1. **G5 km porównawcze:** log NIE zapisuje `km_to_pickup` PROPONOWANEGO (pierwotnego) kuriera — bez tego nie
   udowodnimy „nowy nie dowozi dalej". Dodać pole `proposed_km` do wiersza (zmiana obserwowalności, nie decyzji).
2. **G6 churn pod semantyką live:** obecny licznik ping-pong liczy na pierwotnym `proposed_cid`. Potrzebny
   pomiar z AKTUALIZOWANYM celem (symulacja live lub `ENABLE_GLOBAL_ALLOC_WRITE` już pisze `global_alloc.json` —
   z niego policzyć realny churn celu per zlecenie).
3. **Dowód POZYTYWNEGO wpływu (Przykazanie #0 etap 5):** nie tylko „spread_improved 68%", ale replay/okno 2 dni
   pokazujące, że rozbite zlecenia realnie skróciły czas dostawy / zmniejszyły R6, a nie tylko przetasowały worki.

---

## 6. REKOMENDACJA

**NO-GO na teraz — GO-KANDYDAT warunkowy.** Mechanizm jest wartościowy i nie-no-op (G1-G3 ✅: 26,4% wierszy to
realne podmiany, 67,9% pile-onów rozbitych, delta p50=84,7), a problem ownera jest zmierzony i częsty
(~50 sytuacji/dzień „różne kierunki na jednego kuriera", 100% grup pile-on ma różne adresy). Interakcja z
claim-ledger HARD jest bezpieczna (0 feral, HARD tylko chroni). ALE trzy rzeczy blokują flip TERAZ:

1. **Brak guardu anty-ping-pong (G6 poniżej progu: 17% zleceń oscyluje).** Wymaga hysterezy + podniesionego
   marginu re-podmiany — wdrożonych protokołem #0 PRZED flipem.
2. **Luka pomiarowa G5 (km zwycięzców) — nie da się dziś udowodnić braku regresji dowozu.** Dodać `proposed_km`
   do logu i zebrać 48h.
3. **Trop źródłowy:** pile-on jest w dużej mierze skutkiem otwartego buga no-GPS center-score (te same cid
   179/413). Kierunkowo lepiej najpierw domknąć fix źródłowy no-GPS (trwa), a resweep-LIVE wpiąć jako
   komplement — inaczej łatamy skutek, nie przyczynę (Z2/„u źródła, nie łatki").

**Ścieżka do GO:** (a) guard anty-ping-pong + `proposed_km` w logu → (b) 48h realnego cienia z G4/G5/G6
zmierzonymi na żywo → (c) dowód pozytywnego wpływu (okno 2 dni) → (d) ACK Adriana → flip za protokołem #0
(backup→py_compile→test→1 restart), rollback = `PENDING_RESWEEP_LIVE=False` (hot-reload, bez restartu).
Cap 3/tick i TOCTOU-guard już są. Flip = decyzja Adriana.

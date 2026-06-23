# SPEC — best_effort selekcja ślepa na carry-ordery (R6 new-pickup-only)

**Data:** 2026-06-23 · **Status:** DIAGNOZA + REPLAY (read-only) · **NIC nie flipnięte**
**Trigger:** case #482817 (Adrian) — Ziomek zaproponował Jakuba Olchowika (cid=370) na sushi
mimo że 370 wiózł 482800 do Niewodnicy; wpięcie sushi zepsułoby 482800 (+58 min / 93 min worek).

---

## 1. TL;DR

Przy 0 feasible (peak, nasycona flota) selekcja best_effort sortuje kluczem
`_best_effort_sort_key` (`dispatch_pipeline.py:557`):

```
(r6_pov, sla, bucket, -score, dur)        # r6_pov = len(r6_per_order_violations)
```

`r6_per_order_violations` liczy naruszenia R6 (>35 min termicznie) **TYLKO na NOWO
odbieranych zleceniach** (def. `route_simulator_v2:194` — „only for orders picked up during
this plan"). Carry-ordery (już w aucie, `picked_up`) **nie są liczone** → kandydat, który
rozwala niesione jedzenie, wychodzi `r6_pov=0` = „czysty" na PRIMARY i wygrywa, choć:

- jego **score** jest najgorszy w puli (bo `r6_soft_pen` wycenia carry-breach poprawnie),
- jego **objm_r6_breach_count** > 0 (carry-świadoma metryka też widzi szkodę).

Score i objm znają prawdę — ale klucz selekcji ich nie używa jako PRIMARY.

**To NIE jest błąd czasów.** Czasy zweryfikowane (case §2): GPS świeży, OSRM realny (nie
fallback), dystanse spójne z trajektorią, cel 482800 = realna Niewodnica. 93 min to prawdziwa
geometria. Problem = rozjazd metryk: ten sam worek liczony 93 min w score/objm, a 0 naruszeń w
kluczu selekcji.

---

## 2. Case #482817 (dowód że czasy są dobre, a wybór zły)

| sygnał | wartość | werdykt |
|---|---|---|
| pula przy 15:56:57 | 8 kurierów, **0 feasible** | peak saturacja → best_effort |
| GPS 370 | `gps`, wiek **1,4 min** | świeży |
| OSRM | `osrm_fallback_used=False` | realna trasa |
| 370 → Węglowa (odbiór) | 5,21 km / 19,7 min → ETA **16:16** vs czas_kuriera 16:18 | trafione |
| 482800 (Niewodnica) coords | [53.070, 23.075]; GPS 370 o 14:18 = 53.097 (jedzie tam) | cel realny |
| **plan 370** | `482794→482793→482817→482800`; **482800 bag = 93,4 min** | prawda geometryczna |
| **370 score** | **−1795** (najgorszy w puli), z czego `r6_soft_pen=−1489.6` | score WIE |
| **370 r6_pov** | **0** (sushi 30,86 min < 35) | klucz selekcji ŚLEPY |
| 370 objm_r6_breach | **3 naruszenia / max 58,4 min** | objm WIE |

Pula i klucz:

| cid | kurier | score | r6_pov | wybór |
|---|---|---|---|---|
| 393 | Michał Karpiuk | −845 | (sla) | score-first #1 |
| 531 | Piotr Kulaszewski | −892 | **2** | wybór człowieka |
| 370 | Kuba Olchowik | **−1795** | **0** | **best_effort wybrał** |

370 wygrywa na PRIMARY (`r6_pov 0 < 2`) zanim score (−1795) w ogóle zostanie porównany
(score jest 4. w kluczu). Propozycja wyszła z `auto_route=ALERT` (brama człowieka, nie
auto-assign) → **koordynator nadpisał na 531** (dokładnie to, co należało). Forward-shadow
przerzutów dalej sugeruje 531→370 (Δ+272) — bo ta sama ślepota siedzi w scoringu izolowanym.

Bliźniaczy przypadek tego samego dnia: **#482815 13:46 — znów 370 (−1793, objm 2/55 min) nad
393 (−337), gap 1456.**

---

## 3. Root cause (kod)

Ścieżka feasible MA już carry-inclusive selektor:
- `_objm_lexr6_shadow` (`dispatch_pipeline.py:940`, D2 2026-06-17) — PRIMARY =
  `objm_r6_breach_max_min` (carry-inclusive), zwalidowany replayem (−577 min twardych
  spóźnień/7d na 54 naprawionych). Flaga `ENABLE_OBJM_LEXR6_SELECT_SHADOW=ON`.
- `_objm_lexr6_select` (Faza 2, `:1038`, kanon `dispatch_v2.objm_lexr6`) — live-flip,
  flaga `ENABLE_OBJM_LEXR6_SELECT=OFF` (czeka na ACK).

**OBA wymagają `feasible` niepustego** (`:950 if not feasible: return`, `:5383 and feasible:`).
Ścieżka best_effort (0 feasible, `:6032`) **nigdy ich nie dotyka** — wisi na starym
`_best_effort_sort_key` z `r6_per_order_violations` (new-pickup-only). Migracja na objm
ominęła dokładnie tę ścieżkę — a to ona odpala w peaku, gdy carry najbardziej boli.

Ironia historyczna: PRIMARY `r6_per_order_violations` wprowadzono w P3-D3 (2026-05-11) by
naprawić INNĄ ślepotę (`plan.sla_violations=0` przepuszczało 43-min carry — case Jelenia).
Naprawiając ślepotę SLA wprowadzono ślepotę carry. `objm_r6_breach_*` łapie OBA.

---

## 4. Replay (read-only, bez re-runu silnika — exact z zalogowanego score)

Metoda: 222 decyzji best_effort (06-11→06-23, `shadow_decisions.jsonl*`). `best` pełny,
`alternatives` mają `score` (pule <16 = TOP_N → bez truncacji). Score już zawiera
`r6_soft_pen` (carry-inclusive), więc „score-first" = dolna granica carry-świadomej selekcji.

| metryka | wynik |
|---|---|
| best „wygląda czysto, jest brudny" (`r6_pov=0` ALE `objm_breach>0`) | **36/222 = 16,2%** |
| ↳ ignorowany carry-breach `objm_max` | mediana **32 min** / max **167 min** / >35min w **16** przyp. |
| score-first wybrałby INNEGO (cała divergencja klucz vs score) | 101/222 = 45,5% (zbyt szerokie — patrz §5) |
| **divergencja SPOWODOWANA ślepotą** (best `r6_pov=0`+`objm>0` ∧ lepszy alt w puli) | **26/222 = 11,7%** |

TOP przypadki (best „ślepo czysty" + dostępny lepszy alt):

```
ts                 oid      best  score  r6pov objmN objmMax  ->sf   sfscore  gap
2026-06-15T14:10   481048   393   -1824    0     4   167.4   ->471   -197    1627
2026-06-15T14:03   481046   393   -1660    0     4   162.6   ->471   -188    1472
2026-06-22T12:12   482603   393   -2065    0     2    61.2   ->515   -368    1697
2026-06-23T13:56   482817   370   -1795    0     3    58.4   ->393   -845     950
2026-06-23T13:46   482815   370   -1793    0     2    55.0   ->393   -337    1456
2026-06-21T13:31   482328   447   -1754    0     2    48.3   ->531   -380    1374
```

Dotknięci kurierzy: 393/531/533/413/529/447/370 — **nie artefakt jednego kuriera**.

### 4b. Exact objm-first replay z `obj_replay_capture.jsonl` (re-run silnika) — z BRAMKĄ WIERNOŚCI

Re-run `simulate_bag_route_v2`+`compute_plan_metrics` per kandydat (harness `obj_harness`,
coords z capture, zero geokodowania). **OGRANICZENIE WIERNOŚCI (kluczowe):** capture trzyma
wejścia z etapu *feasibility*; re-run robi ZAWSZE świeży solve (deterministic — 4/4 identyczne),
a realna selekcja czyta objm z etapu *scoring*, który dla **`sticky`/saved-plan** używa zapisanej
sekwencji (incremental insert). Capture NIE odtwarza sticky. Bramka: licz tylko decyzje, gdzie
re-run odtwarza zalogowany `best.objm` (±2 min).

| | wynik |
|---|---|
| best_effort total | 224 |
| **WIERNE** (fresh-best odtworzony exact) | **81 (36%)** |
| sticky/niewierne (re-run ≠ realny plan, **w tym #482817**) | 143 (64%) |

Na wiernych 81:
- **objm-first FLIP** (istnieje kandydat z niższym carry-breach): **33/81 = 40,7%**;
  redukcja worst-breach **mediana 7,1 / max 48,4 min**; 7 flipów ≥20 min.
- **0-harm na carry = GWARANTOWANE konstrukcyjnie** (objm-winner = min worst-breach w puli,
  best zawsze w puli → ≤ best). Replay kwantyfikuje ZYSK, nie „dowodzi" 0-harm.
- ⚠ **new-order „regresja" (winner new-bag>35 a best ≤35): 6/33 = 18,2%** — ale to
  REDYSTRYBUCJA, nie netto-szkoda: objm-first minimalizuje GLOBALNY worst-breach (zawsze ≤ best),
  nowy order staje się breachem PODmaksymalnym, nie najgorszym. Spójne z regułą „>35 dostawa
  wygrywa / carried-first".

TOP flipy: 482321 (−48 min), 482616 (−44), 482631 (best new-bag 101→18 **na obu**), 482433 (−28).

**Werdykt wierności:** offline replay daje kierunek na 36% (fresh, prostsze worki), ale 482817 i
większość peak-sticky są NIEodtwarzalne offline → **exact pełny pomiar wymaga live shadow (§6.1)**
albo dologowania objm per-alternatywa (§6.3).

### 4c. Drugi, NIEZALEŻNY lewar (uboczne odkrycie)

Re-run worka 370 (#482817) świeżym solverem: `seq=[482794,482793,482800,482817]`, **482800 = 34
min** (vs sticky 93 min). Sticky/incremental-insert (`insert_stop_optimal` „does NOT reorder
existing stops") sam wepchnął sushi przed 482800 → +59 min carry. Czyli część katastrofy 370 to
NIE selekcja, lecz **sticky nie reoptymalizuje gdy daleki carry zostaje uwięziony** (łączy się z
22.06 incremental-no-reopt / carried-first). Osobny temat — NIE mieszać z fixem selekcji.

---

## 5. Fix (proponowany — NIE wdrożony)

**Minimalny, spójny z D2:** w `_best_effort_sort_key` zamień PRIMARY z new-pickup-only na
carry-inclusive, mirror `objm_lexr6._lex_qual`:

```python
# było:  r6_pov = len(c.metrics.get("r6_per_order_violations") or [])
# będzie: r6q = c.metrics.get("objm_r6_breach_max_min") or 0.0   (carry-inclusive)
#         return (r6q, sla, bucket, -score, dur)   # lub objm_r6_breach_count jako primary
```

Zachowuje strukturę leksykograficzną („najmniej R6-breach najpierw") — NIE czyste score-first
(45,5% = zbyt szerokie, ryzyko regresji P3-D3: kandydat z R6-breach ale lepszym score
wygrywałby nad czystym). objm-first flipuje DOKŁADNIE ślepe carry-przypadki.

Dla #482817: 370 dostaje `objm_r6_breach_max=58.4` zamiast `r6_pov=0` → spada pod 531
(objm niższe) i 393. Wychodzi 531/393 — zgodnie z wyborem człowieka.

**Flaga:** `ENABLE_BEST_EFFORT_OBJM_R6_KEY` (default OFF, hot-reload), kill-switch jak
`ENABLE_BEST_EFFORT_POS_SOURCE_KEY`.

### Dlaczego niskie ryzyko
- reużywa metrykę `objm_r6_breach_*` JUŻ zwalidowaną na ścieżce feasible (D2 replay 17.06),
- łapie też oryginalny case Jelenia (P3-D3), więc to nadzbiór, nie regres,
- objm jest w `c.metrics` dla każdego kandydata z planem (czyta to już `_objm_lexr6_shadow`),
- nie rusza reguł Adriana: carry-first / >35 dostawa wygrywa / R-35MIN-MAX zostają.

---

## 6. Plan measure-first (PRZED jakimkolwiek live-flip)

⚠ Offline capture replay (§4b) = TYLKO 36% wiernych (sticky nieodtwarzalny, w tym 482817).
Faithful pełny pomiar = jedna z dwóch dróg na żywych planach (sticky-aware):

1. **(REKOMENDOWANE) Shadow na ścieżce best_effort** (log-only): w gałęzi `:6042` policz
   `_be_objm = min(with_plan, key=_lex_qual)` i zapisz `best.metrics["best_effort_objm_pick"]`
   (cid + flip + d_objm_breach + d_score + d_new_late + new_order_d) — mirror `_objm_lexr6_shadow`,
   ZERO mutacji `best`. Liczy objm na DOKŁADNIE tych (sticky) planach co realna selekcja → faithful
   flip-rate + 0-harm carry + new-order regresja na peaku w kilka dni.
3. **(alternatywa, 1-linijka)** Dologuj `objm_r6_breach_max_min` + `sum_bag_time_min` do
   serializacji KAŻDEJ alternatywy w `shadow_dispatcher` (dziś tylko score+reason) → po kilku
   dniach replay §4 staje się exact objm-first bez re-runu silnika, faithful do sticky.
2. **Offline replay (§4b)** — zrobiony, wartość kierunkowa na 36%; NIE wystarczający do flip.

**Gate flip:** faithful flip-rate (shadow §6.1) ∧ 0-harm na carry (gwarancja konstrukcyjna) ∧
new-order regresja akceptowalna (redystrybucja worst-breach, nie netto-szkoda — patrz §4b/§5).
Dopiero wtedy `ENABLE_BEST_EFFORT_OBJM_R6_KEY=true` + monitor.

## 7. Rollback
Flaga `=false` (hot-reload, bez restartu) → stary `_best_effort_sort_key`.

## 8. Otwarte
- §4 to dolna granica (score-first z widocznych alternatyw). Exact objm-first = §6.2.
- Drobny dług danych: 482800 adres „Kościuszki 23/1" vs coords Niewodnica — etykieta gubi
  miejscowość; coords realne (kurier tam jedzie), NIE wpływa na tę decyzję.

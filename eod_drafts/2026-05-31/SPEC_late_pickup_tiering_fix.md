# SPEC — fix late-pickup tiering (priorytet odbioru przebija jakość dowozu)

**Data:** 2026-05-31 | **Autor:** CC (diagnoza na żądanie Adriana) | **Status:** DESIGN, czeka ACK
**Powiązane:** [[feedback_two_hard_rules_defer_over_extend]], OBJ FRESH (30.05), `late-pickup-hard-gate-2026-05-31`

---

## 1. Problem (objaw)

Adrian zgłosił 6 propozycji (31.05), w których Ziomek dorzuca zlecenia / wybiera kuriera tak, by
**trafić w czas odbioru restauracji**, kosztem długich (33–34 min, na granicy 35) i rozjechanych
dowozów. Cytat: *„za bardzo chce przypisać po czasie restauracji… wciska na siłę, żeby był czas
restauracji. Lepiej przedłużyć 15–20 min i zawieźć w 20 min niż odebrać w czasie restauracji
i wozić 35 min."*

## 2. Root cause (logika)

`dispatch_pipeline.py:3441–3459` — FINAL reorder pass po `_demote_blind_empty`:

```python
if _has_lower:   # istnieje jakikolwiek tier-0
    feasible.sort(key=lambda c: (_lp_tier(c), _orig_order[id(c)]))
else:
    feasible.sort(key=lambda c: (_lp_tier(c), _new_eta_key(c), _orig_order[id(c)]))
```

`_lp_tier` jest **PIERWSZYM kluczem sortu**:
- tier 0 = nowy odbiór ≤5 min na czas
- tier 1 = nowy odbiór potrzebuje przedłużenia (>5 min)
- tier 2 = łamie committed `czas_kuriera` bag-ordera (>5 min)

Skutek: **każdy tier-0 bije każdy tier-1 niezależnie od score.** A w `score` (post-`feasible.sort(-score)`
przy 3409) siedzi cała jakość dowozu: R1 spread/korytarz, R6 czas w bagu, bundle dev. Tiering ją kasuje.

Ironia: gate miał *preferować przedłużenie odbioru* (tier 1 → `pickup_extension_redirect`).
W praktyce robi odwrotnie — **unika przedłużenia**, byle zdążyć na czas, wybierając gorszy dowóz.

Wzmacnia to **OBJ FRESH** (`ENABLE_OBJ_PICKUP_FRESHNESS=1`, 30.05): kara w funkcji celu TSP za
odbiór > `ready+8min` (coeff 20) → solver woli przyjechać wcześniej i **czekać pod restauracją**.

## 3. Dowód (zweryfikowany 31.05, niedziela → traffic mult = 1.0, OSRM dokładny)

**Twardy dowód nadpisania score** (`dispatch.log`):
```
NO_GPS_DEMOTE order=477330: ... new_top_cid=518     # po demote topem Michał Ro (+36.4, informed)
```
…ale serializowany best = cid **484 Andrei K (−5.3)**. Jedyny pass między demote a zapisem to tiering.
Andrei tier-0 (Tarasowa+Jodłowa, oba odbiory ~na czas, pickup_spread 1.06 km) przeskoczył
Michała Ro tier-1 mimo **−42 pkt** score. R1 korytarz ukarał (−58.55), ale bez efektu.

**Skala:** 42/173 (24%) dzisiejszych PROPOSE ma best ≠ najwyżej-punktowany nie-blind kandydat.

**OSRM bezpośrednio vs w-bundlu** (dowóz, niedziela, mult 1.0):

| oid | dostawa | bezpośrednio (solo) | w bundlu (plan) | narzut |
|---|---|---|---|---|
| 477330 | Goodboy→Jodłowa | 8.1 min (~12 z DWELL) | 32.0 | +20 |
| 477285 | Rany Julek→Kołłątaja | 6.7 min (~11) | 33.92 | +23 |
| 477298 | Pani Pierożek→Bełzy | 7.2 min (~11) | 32.88 | +22 |

Czyli food siedzi w aucie ~20+ min dłużej; dowóz potraja się i ląduje na granicy 35 min.

## 4. Fix — 3 opcje (rekomendacja: B)

Wszystkie zachowują **tier-2 jako twardy demote** (łamanie committed obietnicy = ostateczność, case 477237).
Różnią się traktowaniem tier-0 vs tier-1.

### Opcja A — minimalna (tier-2 last, reszta po score)
```python
feasible.sort(key=lambda c: (1 if _lp_tier(c) == 2 else 0, _orig_order[id(c)]))
```
`_orig_order` = kolejność po score (z sortu 3409 + demote). Tier 0/1 wracają do rankingu score;
zwycięzca tier-1 → `pickup_extension_redirect`.
**Ryzyko:** pickup-lateness ma ZEROWĄ wagę w score → może wybrać +25 min przedłużenia odbioru dla
drobnego zysku dowozu.

### Opcja B — gradient (REKOMENDOWANA)
Dodaj **miękką karę score proporcjonalną do `new_pickup_late_min`** (nowy term scoringowy,
np. `-LATE_PICKUP_SOFT_COEFF * max(0, late-5)`, cap), potem sortuj czysto po score.
Tier = już tylko etykieta do `pickup_extension_redirect`.
Spójne z LESSON-QA-10 (gradient nie threshold) i R-BUFFER-OK („5–15 min delay OK, nawet 40 jeśli
najlepszy"). Carry (R6) waży mocno, pickup-lateness lekko → dokładnie intencja Adriana.
Wymaga kalibracji `LATE_PICKUP_SOFT_COEFF` (replay 7–14 dni).

### Opcja C — bounded tiering
Tier-0 wygrywa TYLKO gdy żaden tier-1 nie bije go o > `MARGIN` pkt score:
```python
feasible.sort(key=lambda c: (1 if _lp_tier(c)==2 else 0,
                             0 if _lp_tier(c)==0 else 1,  # tier-0 przed tier-1
                             _orig_order[id(c)]))
# potem: jeśli istnieje tier-1 z score - best_tier0_score > MARGIN → promuj go
```
Kompromis, ale dwa progi do strojenia.

## 5. Counterfactual na 6 przypadkach (Opcja A/B, logika)

| oid | obecny best | po fix | uwaga |
|---|---|---|---|
| 477330 | Andrei −5.3 (11.7km spread) | **Michał Ro +36.4** (1 zlec., 7.8km) | tiering cofnięty |
| 477329 | Jakub −5.4 | **Andrei 0.7** (B+#5 → Paweł +12.2) | +fix #5 (pos_source) |
| 477298 | Grzegorz −17.1 (4 zlec.) | zależy od alt; Michał Rom −38.6 gorszy → możliwy KOORD | R6-soft też za słaby |
| 477285 | Aleksander 0.6 | bez zmian (był top-score) | tu winny R6-soft, nie tiering |
| 477271 | Grzegorz pre_shift 97 | bez zmian | osobny: pre_shift + wait penalty=0 |
| 477287 | Gabriel −2.1 | bez zmian | nie-tiering; veto return OFF |

→ Fix tieringu naprawia **477330, 477329** wprost. 477298 wymaga dodatkowo hardeningu R6-soft.
477285/477271/477287 to osobne przyczyny (patrz §7).

## 6. Walidacja (przed flipem)

1. **Per-candidate tiery do shadow logu** (NAJPIERW — bez tego nie da się audytować):
   serializuj `late_pickup_committed_breach / new_pickup_needs_extension / new_pickup_late_min /
   new_pickup_eta_iso` w `_serialize_candidate` (LOCATION A) + best (LOCATION B). Obecnie są tylko
   w `pickup_extension_redirect` zwycięzcy → łamie encoding-checklist (niewidoczny bug).
2. **sequential_replay** 7–14 dni: `python3 -m dispatch_v2.tools.sequential_replay --date … --from … --to …`
   pod flagą `ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST` (nowa, default OFF/shadow). Mierz:
   - ile propozycji zmienia zwycięzcę,
   - delta median deliv_spread / r6_max_bag_time / udział dowozów >32 min,
   - ile razy zwycięzca staje się tier-1 (→ ile `pickup_extension_redirect` Telegram pokaże).
3. Flip dopiero po ACK Adriana + 7-dniowy shadow czysty.

## 7. Fixy powiązane (osobne tickety)

- **#2 R-NO-RETURN veto:** `ENABLE_R_RETURN_TO_RESTAURANT_VETO=0` → flip `1` po replay (kara −100, sprawdzić FP).
- **#5 pos_source:** dodać `last_picked_up_pickup` do `INFORMED_POS_SOURCES` (`dispatch_pipeline.py:462`) —
  dziś spada do bucketu „other" w demote (Paweł SC 477329 +12.2 zdegradowany mimo top-score).
- **R6-soft za słaby:** strefa 30–35 (`bonus_r6_soft`) nie odrzuca; rozważyć próg hard 33 dla bagów ≥3
  albo bufor ETA. (477298 Bełzy 32.88, 477285 Kołłątaja 33.92.)
- **pre_shift z bagiem:** `_demote_blind_empty` nie rusza pre_shift gdy ma bag → syntetyczny score 97
  (Grzegorz 477271). Rozważyć demote pre_shift niezależnie od bag.
- **wait penalty V327 = 0:** `bonus_r9_wait_pen` zwraca 0 we wszystkich dzisiejszych (legacy −186).
  Sprawdzić czy V327 wait penalty w ogóle odpala (czekanie pod restauracją nie karane).

## 8. Rollback

Nowa flaga `ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST` (env, default OFF). Flip OFF = powrót do obecnego
zachowania bez restartu kodu (hot-reload jeśli przez `flags.json`, albo env + restart `dispatch-shadow`).
Tier-2 demote pozostaje niezmieniony w każdej opcji.

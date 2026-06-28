# STAGE 4 — czasówka-w-uwagach DECYZYJNIE (projekt; NIE budować bez ACK + okna danych)

**Autor:** sesja 20, 2026-06-28 · **Status:** DESIGN ONLY. Build dopiero po (a) oknie żywych danych z shadow (Stage 1a+2 LIVE od 28.06) + (b) decyzji Adriana na otwarte pytania (§3) + (c) ACK. Linie dryfują — grepuj symbole.
**Poprzednie:** Stage 1a (`8220220`) + Stage 2 (`15e39bb`) LIVE — ingest+persist+parser+oracle, observability-only, flaga `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW` ON. Spec: `CZASOWKA_UWAGI_PARSER_SPEC.md`.

---

## 1. Cel i ZASADA (czytaj zanim cokolwiek)
Stage 4 = sprawić, by `delivery_deadline_uwagi` (już zapisywany) **wpływał na wybór kuriera / plan**, tak by Ziomek dowoził pod twardy deklarowany czas dostawy z `uwagi`.

**ZASADA #1 — to DRUGIE, NIEZALEŻNE ograniczenie, NIE modyfikacja R6.**
- R6/SLA = **względne**: `delivered − pickup ≤ 35 (T1/2) / 40 (T3)`, tier-aware, ready-anchored (jedzenie stygnie od gotowości).
- uwagi-deadline = **absolutne**: `delivered ≤ 17:10` (zegar). Niezależne od tieru i od pickupu.
- Worek może SPEŁNIAĆ R6 a MIJAĆ deadline (bo pickup był późno) i odwrotnie. → deadline to **osobna oś**, nakładana NA R6, **nigdy go nie zastępuje ani nie luzuje** (P0: SOFT nie osłabia HARD).

**ZASADA #2 — always-propose nietykalne.** Żaden wariant nie tworzy „BRAK KANDYDATÓW". HARD-ścieżka MUSI mieć fallback `_best_effort_fastest_pickup_key` (auto_route=ALERT), jak R6.
**ZASADA #3 — R27 committed nietykalny.** Deadline NIE zmienia `czas_kuriera`/okna ±5. Ortogonalne.
**ZASADA #4 — tier-aware zostaje.** R6 dalej 35/40. Deadline-term komponuje się z R6, nie nadpisuje progów.

---

## 2. BRAMA WEJŚCIA (co MUSI być PRZED buildem)
1. **Okno danych:** shadow zbiera `delivery_deadline_uwagi` od 28.06; potrzeba ≥7–14 dni realnego ruchu (dziś n=51 effective, elastic n=9 — za mało na flip). Oracle pokazał: ~1/3 mija deadline >3min, p90 ~17-18min, ale mediana ~on-time → **materialność jeszcze NIE udowodniona**.
2. **Decyzje Adriana (§3)** — bez nich nie zaczynamy (Wątpliwość → PYTAJ).
3. **ACK** na ścieżkę (SOFT/HARD) + deploy.
4. **Replay ON↔OFF** udowadnia POZYTYWNY wpływ. **Próg (Adrian 28.06): ≥2% PEWNEGO progresu wystarcza — Ziomek ma być idealny, każdy pewny zysk się liczy.** ⚠ Niższy próg ⇒ CZYSTOŚĆ pomiaru + NETTO-rozliczenie regresji OBOWIĄZKOWE: przy 2% marginesie szum/kłamiący miernik przepycha zły flip (audyt O2: 12,4% „improved" maskowało 7 regresji). „Pewny" = bez regresji R6/SLA, deterministyczny (≥2 odpalenia), netto (nie 1 case), n duże tyle by 2% NIE było szumem.

---

## 3. OTWARTE DECYZJE — DLA ADRIANA (NIE zgaduję)
| # | Pytanie | Opcje | Rekomendacja sesji 20 |
|---|---|---|---|
| D1 | **HARD czy SOFT?** | (A) HARD-reject gdy predicted delivered > deadline (+ best_effort fallback) · (B) SOFT-kara w scoringu rosnąca z przekroczeniem · (C) hybryda: SOFT + HARD dopiero przy dużym przekroczeniu | **(B) SOFT najpierw.** Always-propose święte, dane skromne (mediana on-time), `delivered_at` ±3min szum → HARD groziłby masowym KOORD/best_effort („geometryczne optimum łamie czasówkę" — case Bartka). SOFT pozwala optymalizatorowi ważyć deadline fleet-level. Eskalacja do (C) po danych. |
| D2 | **Kształt kary (SOFT)** | per-min liniowa / gradient 3-bucket (QA-10) / kwadratowa | **Gradient 3-bucket** (QA-10 „gradient nie threshold"): np. ≤deadline 0; +1..+10min mała; >+10min duża. Progi z danych. |
| D3 | **Próg „breach" przy szumie ±3min** | breach = pred > deadline / > deadline+3 / > deadline+tol | **> deadline + tolerancja (~3-5min)** — `delivered_at` to klik w apce (audyt: 0/377 GPS). Kara liczona od tolerancji, nie od 0. |
| D4 | **Paczki?** | deadline dotyczy / nie dotyczy paczek | **Dotyczy** (deadline = obietnica KLIENTOWI, nie termika) — ale paczki bypass R6, więc deadline byłby ich JEDYNYM czasowym ograniczeniem. Potwierdź. |
| D5 | **Okno danych przed flipem** | 7 / 14 / 30 dni | **14 dni** (peak+offpeak, lekcja 2-dni za mało dla rzadkiego zjawiska ~1,2%). |

---

## 4. MAPA KOMPLETNOŚCI (klasa → miejsce → co zrobić; bliźniaki RAZEM)
Symbole zweryfikowane 28.06 (grepuj — dryf):

| # | Klasa | Plik:symbol (~linia) | Stage 4 — co | SOFT | HARD |
|---|---|---|---|---|---|
| 1 | **OrderSim plumbing** | `route_simulator_v2.OrderSim` (dataclass ~208) | dodać pole `delivery_deadline_uwagi: Optional[datetime] = None` (dziś `order_type` jest ad-hoc attr — zrób PORZĄDNIE polem) | ✓ | ✓ |
| 1b | **Budowa OrderSim z order-dict** | `dispatch_pipeline` `sim.order_type=d.get(...)` (~3016) + `new_order.order_type=order_event.get(...)` (~3417) + **WSZYSTKIE** miejsca `OrderSim(...)` / `_bag_dict_to_ordersim` (grep) | ustaw `delivery_deadline_uwagi` z order-dict (parse ISO→aware). **Bag-members TEŻ** (breach niesionych), nie tylko new_order | ✓ | ✓ |
| 2 | **Twin 1 — symulator** | `route_simulator_v2._count_sla_violations` (635) + caller (768) + `RoutePlanV2` (~217) | policz ABSOLUTNY breach: dla każdego oid z deadlinem `delivered_at[oid] > deadline+tol` → nowe pole planu `deadline_uwagi_breach` (count) + `deadline_uwagi_worst_min`. NIE ruszaj `sla_violations` (R6 osobno) | ✓ telemetria | ✓ + gate |
| 3 | **Twin 2 — feasibility** | `feasibility_v2`: `DEFAULT_SLA_MINUTES`(53), `metrics["sla_violations_count"]`(825), gate `plan.sla_violations>0`(1135), `metrics["sla_violations"]`(1182) | dołóż `metrics["deadline_uwagi_breach"]`. SOFT: TYLKO metryka (zero gate). HARD: osobny gate PO sla/R6 (kolejność: R6→sla→deadline), werdykt NO→best_effort fallback (always-propose) | ✓ metryka | ✓ gate+fallback |
| 4 | **Twin 3 — plan_recheck** | `plan_recheck._o2_key` (683), `simulate_bag_route_v2(...sla_minutes=35)` (699, 1641), gate `_committed_ok` (722) | jeśli deadline-aware RE-SEQ: dołóż term deadline do klucza selekcji. **⚠ SPRZĘŻENIE z `ENABLE_O2_READY_ANCHOR_SWEEP`** (l.679, O2 — audyt: NIE flipować 02.07) → term deadline za WŁASNĄ pod-flagą, default = zachowanie O2-OFF (byte-id gdy OFF). NIE wiązać z O2 | ✓ (opcj.) | ✓ (opcj.) |
| 5 | **Scoring (SOFT)** | `dispatch_pipeline._v327_eval_courier` (19 kar + `bonus_penalty_sum`) | nowy term `deadline_uwagi_penalty` (gradient D2), wliczony do score za flagą; telemetria zawsze | ✓ | — |
| 6 | **Serializer A+B (wzorzec #16!)** | `shadow_dispatcher._serialize_candidate` (LOCATION A ~271) + `_serialize_result.best` (LOCATION B ~567) | JAWNIE (nie prefiks — HARD-metryki znikają): `delivery_deadline_uwagi`, `deadline_uwagi_breach_min`, `deadline_uwagi_penalty`. Test `grep -c` w jsonl >0 | ✓ | ✓ |
| 7 | **Flaga decyzyjna** | `common.ETAP4_DECISION_FLAGS` + stała OFF + `flags.json` + `ZIOMEK_LOGIC_REFERENCE` | NOWA `ENABLE_CZASOWKA_UWAGI_DEADLINE_PENALTY` (SOFT) / `..._GATE` (HARD). Telemetria breach **compute-always**; flaga gate'uje TYLKO aplikację (kara/gate). Default OFF | ✓ | ✓ |
| 8 | **Testy** | `tests/test_czasowka_uwagi_*` | ON≠OFF (kara/gate zmienia wynik), parytet 3 bliźniaków (ten sam anchor/tol), breach-w-jsonl, **inwariant SOFT-nie-osłabia-HARD** (sla/R6 ON≤OFF), always-propose (0 BRAK KAND.), R27 niezmienione | ✓ | ✓ |

**Anchor breach:** delivered = `plan.predicted_delivered_at[oid]` (ten sam co R6/SLA), deadline = `OrderSim.delivery_deadline_uwagi`. Parytet kotwicy między 3 bliźniakami (#15 — nie proxy). Tolerancja D3.

---

## 5. WARIANT SOFT (rekomendowany) — przepływ
1. OrderSim niesie deadline (krok 1/1b).
2. `_count_sla_violations`/symulator liczy `deadline_uwagi_breach_min` per oid (compute-always) → RoutePlanV2.
3. feasibility wystawia metrykę (bez gate).
4. scoring: `deadline_uwagi_penalty = gradient(breach_min)` za flagą `..._PENALTY` → wchodzi do `bonus_penalty_sum` → przesuwa selekcję ku kurierowi, który zdąży na deadline (fleet-level, NIE twardy reject).
5. serializer A+B zapisuje breach+penalty → replay/oracle.
6. Werdykt/feasibility/always-propose **bez zmian** (zero reject z tytułu deadline). Najbezpieczniejsze.

## 6. WARIANT HARD (alternatywa, tylko po danych) — przepływ
Jak SOFT + dodatkowy **gate w feasibility PO R6/sla**: `if deadline_breach_min > próg: verdict NO (reason=DEADLINE_UWAGI)`. ALE: musi spaść do `_best_effort_fastest_pickup_key` (always-propose), auto_route=ALERT, NIGDY BRAK KAND. Ryzyko over-reject → tylko gdy replay pokaże, że HARD bije SOFT bez wzrostu best_effort/KOORD.

---

## 7. WALIDACJA (ETAP 5 — warunek flipu)
- **Shadow window 14 dni** (brama §2): breach compute-always serializowany.
- **Oracle** (`tools/czasowka_uwagi_oracle.py`, już jest): real `delivered_at` vs deadline, tol ±3min.
- **Replay ON↔OFF na korpusie:** czy kara/gate ZMIENIA wybór kuriera i czy wybrany częściej zdąża na deadline. Metryka docelowa: **% delivered ≤ deadline ON vs OFF** — wymagane MIERZALNIE lepsze, **≥2% PEWNY netto** (Adrian 28.06). ⚠ Przy n=51 2% ≈ 1 zlec = SZUM → wymagane okno na tyle duże, by 2% było statystycznie pewne (nie pojedynczy case).
- **Inwarianty-tripwire:** (a) `sla_violations`/R6 ON ≤ OFF (SOFT nie osłabia HARD); (b) liczba best_effort/KOORD ON ≤ OFF+ε (always-propose nie degraduje); (c) `czas_kuriera` ON == OFF (R27); (d) tier-aware 35/40 nietknięte.
- **Determinizm:** ≥2 odpalenia replay.

## 8. DEPLOY (ETAP 6) + ROLLBACK (ETAP 7)
- Flaga OFF default → shadow breach → pomiar → flip za ACK, poza peakiem.
- Restart: `dispatch-shadow` (scoring/feasibility). Twin-3 → też `dispatch-plan-recheck`. NIGDY telegram/peak bez OK.
- Rollback: flaga OFF (hot) / `.bak` / `git revert`.

## 9. SPRZĘŻENIA I PUŁAPKI (znać przed buildem)
- **O2 coupling (twin-3):** `_o2_key` dzieli ścieżkę z `ENABLE_O2_READY_ANCHOR_SWEEP` (O2, audyt: NIE flip 02.07). Term deadline = własna pod-flaga, default O2-OFF semantyka. NIE wiązać losów z O2.
- **`delivered_at` = klik ±3min** (audyt, 0/377 GPS) → tolerancja D3 obowiązkowa, inaczej szum udaje breach.
- **Paczki** (D4): bypass R6, więc deadline byłby ich jedynym czasowym gate'em — świadoma decyzja.
- **Bag-members** (#1b): breach liczyć dla NIESIONYCH też, nie tylko new_order (inaczej połowiczne — wzorzec #6).
- **Serializer #16:** HARD-metryki bez auto-prefiksu znikają → JAWNIE w A+B, inaczej replay-gate (ETAP5) cicho zepsuty.
- **Recall/precision parsera (Stage 2):** wciąż ~? FP (price „12,30 zł" w oknie słowa-klucza) — oracle monitoruje deadline<pickup; przy HARD podnieść precyzję parsera przed flipem.

---
**DoD Stage 4:** każdy wiersz §4 dotknięty/N-D+powód · flaga ON≠OFF · breach w jsonl (#16) · parytet 3 bliźniaków · inwarianty (SOFT≤HARD, always-propose, R27, tier) · pełna regresja vs baseline · replay POZYTYWNY (**≥2% PEWNY, netto, czysty pomiar** — Adrian 28.06) · okno na tyle duże by 2% nie było szumem · O2 niezwiązane · rollback. Częściowe = niezakończone.

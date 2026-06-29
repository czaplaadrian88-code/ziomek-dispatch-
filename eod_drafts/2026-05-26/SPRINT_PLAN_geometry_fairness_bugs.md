# Sprint Plan — Geometry/Fairness Bug Fixes

**Data sesji:** 2026-05-26
**Trigger:** Adrian przedłożył 9 kolejnych „złych propozycji" Ziomka, prosił o pełną diagnozę i plan naprawczy. Decyzja: dziś (26.05) implementujemy WSZYSTKO według rekomendacji, później kilka dni walidacji A/B.

**Status:** PLANOWANY — gotowy do implementacji w nowej sesji.
**Owner Adrian decyzja:** „zapisz, dzisiaj wszystko zmienimy, za kilka dni test przed/po".

---

## 0. TL;DR — co zrobimy

| # | Bug | Faza 1 (dziś) | Faza 2 (1-2 dni) | Faza 3 (replay) | Faza 4 (live) | Faza 5 (long) |
|---|-----|---|---|---|---|---|
| **E** | best_effort obchodzi R6 35min hard | **HOTFIX** ~30 min | — | — | — | — |
| **B** | brak kary detour-pickup | Shadow ON | Calibrate | Replay 7d | Flip live | — |
| **A** | brak Σ bag_time + max bag_time + FIFO w celu solver'a | Shadow ON | Calibrate | Replay 7d | Flip live | — |
| **C** | commit divergence render fikcji (Toriko→GK 2min) | Marker w trasie + ALERT | — | — | — | — |
| **F** | klastry geograficzne (Wybickiego pierwsze) | Shadow metric | — | — | — | Sprint osobny |
| **D** | OSRM × traffic 2× zaniża peak (TomTom) | — | — | — | — | GATE B re-werdykt 22.05 |

**Krytyczna kolejność:** BUG E pierwszy (zatrzymuje 4 case'y natychmiast), reszta równolegle shadow → replay → flip.

---

## 1. Bug Inventory (6 niezależnych luk)

### BUG A — brak Σ bag_time + max + FIFO w funkcji celu solver'a

**Reguła Adriana (cytat z dyskusji):** „lepiej żeby OBA jechały po 15 min, niż jedno 25 a drugie 8, czym dalej czasu 35min tym lepiej, najlepiej żeby suma czasów wszystkich dowozów w bagu była jak najmniejsza".

Plus: „jeśli jest podobnie, najpierw to co zostało wcześniej odebrane" (FIFO tie-break).

**Co aktualnie robi solver (`tsp_solver.py` + `route_simulator_v2._simulate_sequence`):**
- Minimalizuje `total_min` (suma czasów jazdy)
- R6 35min jako HARD constraint (`feasibility_v2` wycina kandydatów)
- `wait_courier` penalty PUSHA pickup na ostatnią chwilę

**Czego brakuje:**
- `Σ bag_time[oid]` minimalizacja jako component score
- `max_bag_time` jako kara za nierównomierność
- FIFO weight: starszy (pickup_at wcześniej) ma większą wagę kary

**Miejsce w kodzie:**
- `dispatch_pipeline.py:~2049` — bonus aggregation, dodać `bonus_bag_time_sum` + `bonus_bag_time_max` + `bonus_fifo_violation`
- `route_simulator_v2.py:_simulate_sequence` (linia 506-598) — już zwraca `delivered_at` + `pickup_at`, wystarczy do obliczeń

**Manifestuje się w case'ach:** #2 (Andersa przed 1000-lecia), E (Bacieczki bag time 90 min), F (3/4 orderów łamie R6).

---

### BUG B — brak kary za detour pickup-not-on-route

**Reguła Adriana:** „dowóz w żaden sposób nie jest po drodze" (Case C).

**Co aktualnie robi solver:**
- `r5_pickup_detour_total_km` jest **zbierane** w `dispatch_pipeline.py:2049+` jako metryka obserwacyjna
- ALE **nie ma negatywnej wagi w bonus aggregation** → solver nie widzi że detour kosztuje
- `r1_avg_pairwise_cosine` + `bonus_r1_corridor` mierzą kierunkowość DROPÓW (nie pickupów)

**Czego brakuje:**
- Negative weight per km detour (np. -8/km, zbliżone do R4)
- Free threshold `<500m` = naturalnie po drodze, bez kary

**Miejsce w kodzie:**
- `dispatch_pipeline.py:~2049` — dodać `bonus_r5_pickup_detour_penalty` = `-R5_DETOUR_PENALTY_PER_KM * max(0, detour_km - R5_DETOUR_FREE_THRESHOLD_KM)`

**Manifestuje się w case'ach:** C (Karczma+Skłodowskiej +1.4 km +4.2 min w bok), A (Pan Schabowy odwrotnie do Transportowej +2 km).

---

### BUG C — commit divergence render fikcji

**Reguła Adriana:** „nie ma szans żeby jechał tam 2 min" (Case #3).

**Co aktualnie robi system:**
- Solver OR-Tools respektuje `[ck-5, ck+5]` per pickup independently (`route_simulator_v2.py:961-977`)
- Może wcisnąć Grill Kebab na `ck+5=13:13` mimo że drive Toriko→GK = 6 min realnie
- Renderer (`telegram_approver._resolve_pickup_at`) prioritetuje **commit** (`czas_kuriera_warsaw`) nad plan ETA → pokazuje 13:08 bez tyldy
- `V3274_RENDER_DIVERGENCE_WARN_MIN=5.0` loguje warning ale **nie blokuje propozycji ani nie pokazuje operatorowi**

**Co trzeba:**
- Renderer dodaje marker `⚠plan~HH:MM` gdy commit vs plan_eta differ > 3 min
- ALERT (nie ACK) gdy divergence > 3 min — reuse mechanizm F4 AUTO-ROUTE WEAK-PICK ALERT (`e300224`, 24.05)

**Miejsce w kodzie:**
- `telegram_approver.py:1002-1009` (renderer)
- `feasibility_v2`/`dispatch_pipeline` — dorzucić reason `plan_commit_divergence` do `_detect_edge_routing`

**Manifestuje się:** Case #3 (Tor→GK 2 min commit fizycznie niemożliwe).

---

### BUG D — V326_OSRM_TRAFFIC_TABLE zaniża peak ~2×

**Empirycznie zmierzone 26.05 ~16:05 Wt:**

| Segment | OSRM ff | OSRM×1.3 mult (wt 16-17) | TomTom real | Stosunek |
|---------|---------|--------------------------|-------------|----------|
| Tor→GK | 3.1 | 4.1 | 6.5 | 2.1× ff |
| GK→RJ | 3.7 | 4.8 | 9.2 | 2.5× ff |
| Skłodowskiej→1000-lecia | 7.7 | 10.0 | 19.0 | 2.5× ff |
| Rukola→Andersa | 4.8 | 6.2 | 11.3 | 2.4× ff |

**Konkluzja:** w peaku 16-17 wt TomTom = ~2-2.5× OSRM free-flow, vs aktualny mult ×1.3.

**Co znaczy:**
- Wszystkie predykcje peak są zaniżone
- Bag times w plan są systematycznie krótsze niż realne → R6 violations realnie częstsze niż w plan
- Częściowo tłumaczy Case #1 (Mieszka I 24 min OSRM = realnie 50+ min?)

**Co robić:**
- **Odłożyć** do GATE B re-werdyktu 22.05 (już at-job zaplanowany per `sprint_timeline.md`)
- Sample TomTom collected w trakcie tej sesji może zasilić re-kalibrację `V326_OSRM_TRAFFIC_TABLE`

---

### BUG E — best_effort obchodzi R6 hard cap (NAJPILNIEJSZY)

**Reguła Adriana:** „przecież to psuje na 100% dowóz, już lepiej dać 10 min później i wrócić po to" (Case E).

**Co aktualnie:**
- `feasibility_v2` wycina kandydatów gdy bag_time > 35 min (HARD R6)
- Gdy wszyscy odrzuceni → fallback `strategy="best_effort"` z `verdict=PROPOSE`
- Telegram pokazuje `⚠️ Best effort — brak feasible kandydata` + `🔴 ALERT — wymaga Twojej decyzji`
- Operator akceptuje sugerując że to sensowny wybór → R6 violations dla istniejących orderów

**Co trzeba:**
- Gdy `best_effort=True` AND `max(bag_times) > BAG_TIME_HARD_MAX_MIN` (35) AND żaden kandydat NIE poprawia tej liczby (`max_bag_time > best_alternative_max`) → `verdict=KOORD` (przepisz do koordynatora)
- Alternatywnie: zostaw ALERT ale dodaj sekcję „R6 BREACH dla orderów: [lista]" żeby koordynator widział co psuje

**Empiryczne sygnały (4 case'y dziś):**
- Case D: 2 orderów łamie R6 (max 45 min)
- Case E: 2 orderów łamie R6 (max **90 min**)
- Case F: 3 orderów łamie R6 (max 56 min)
- Case G: 1 order łamie R6 (max 43 min)

**Miejsce w kodzie:**
- `dispatch_pipeline.py` — gdzie `best_effort` jest decydowane (sprawdzić w nowej sesji)
- `BAG_TIME_HARD_MAX_MIN = 35` (`common.py:318`)

**Skutek po fix:** zamiast 9/14 R6 violations → propozycja przepisana do KOORD, koordynator manualnie rozdziela (np. „10 min później").

---

### BUG F — brak agregacji klastrowej (osiedla)

**Reguła Adriana:** „kraszewskiego i wąska są blisko siebie na jednym osiedlu, szybkie do doręczenia, a później miałby najdalej na jaroszówce" (Case D).

**Co aktualnie:**
- Solver minimalizuje single-hop drive (suma drive_min)
- Nie ma pojęcia „klaster osiedlowy" → nie wybiera „zrób wszystkie blisko, daleko na końcu"
- `districts_data.py` mapuje ulice na osiedla ale **NIE używane jako sygnał dla TSP sequencing**

**Co trzeba (długoterminowo):**
- Dodać metrykę `same_district_drops_grouped_score` — bonus za sekwencję która grupuje dropy z tego samego osiedla
- Lub: kara za „daleki drop pomiędzy dwoma bliskimi"

**Status:** sprint osobny, długoterminowy. Faza 1: tylko shadow metryka żeby zbierać korpus.

---

## 2. Case Inventory (9 case'ów ground truth)

### Case #1 — Chicago Pizza · Mateusz O · Rany Julek → Mieszka I 17/39 ~24 min
**Bug:** D + osobny problem geokodu Mieszka I 17/39 (out of city?)
**Status:** odłożony, osobny tor (geocoding validator + bbox tightening)

### Case #2 — Rukola Sienkiewicza · Michał K. · Andersa 14/3
**Bug:** A (FIFO bag time + Σ minimization)
**System:** Rukola→Andersa→1000-lecia (OSRM 9.7, TomTom **17.2**)
**Adrian:** Rukola→1000-lecia→Andersa (OSRM 11.5, TomTom **15.7**)
**Wniosek:** TomTom potwierdza Adrian wygrywa (15.7 < 17.2). OSRM ff nie widzi peaku.

### Case #3 — Grill Kebab · Mateusz O · pre-shift
**Bug:** C (commit divergence)
**System trasa:** Toriko 13:06 → GK 13:08 → RJ 13:11 (commit times, ALE plan ETA inny)
**Geometria:** Toriko ↔ RJ = 0.3 km / 0.7 min. GK 1.5 km od obu.
**Adrian:** Toriko 13:06 → RJ 13:11 → GK ~13:15 — OK do akceptacji.

### Case A — Pan Schabowy · Mateusz O · Transportowa
**Bug:** B (detour-not-on-route) + A (Σ bag_time)
**System:** RJ→Schabowy→Transportowa (18.0 min OSRM ff, **24.85 min TomTom**, 12.5 km)
**Adrian:** RJ→Transportowa→Schabowy (16.2 min OSRM ff, 10.46 km, -16% km)
**Plus:** Sweet Fit pickup 13:40 → drop Transportowa 14:47 = **67 min bag time** (R6 BREACH)

### Case C — Karczma Maciejówka · Michał K. · Skłodowskiej SOR
**Bug:** B (detour-not-on-route)
**Base:** Kaczorowskiego→1000-lecia = 6.7 min / 4.07 km (TomTom **14.7**)
**Z detour:** +Karczma+Skłodowskiej = 10.9 min / 5.45 km (TomTom **24.2**, +65%)
**Wniosek:** detour kosztuje +1.4 km + 4.2 min OSRM (+9.5 min TomTom)

### Case D — Jakub OL · Sushi RJ NOWY · Chrobrego 12/36
**Bug:** A + B + E + F (najgłębszy bagaż)
**System trasa:** Rukola→Wybickiego (NAJDALEJ)→[pickup Sushi NOWY]→Wąska→Kraszewskiego→Chrobrego
- OSRM ff 25.8 / OSRM+traffic 30.9 / TomTom 33.3 / 13.65 km
- 2 z 4 orderów łamie R6: _500 stopni 45 min, Rukola Sien 41 min
- best_effort flag
**Adrian:** Rukola→klaster(Wąska→Kraszew→Chrobrego)→[pickup Sushi]→Wybickiego
- OSRM ff 23.8 / TomTom 31.3 / 12.41 km (-1.24 km, -2 min)

### Case E — Dariusz M · Rukola Kaczorowskiego · Jana Pawła 61B
**Bug:** A + E (NAJGORSZY R6 violation)
**System trasa:** Stroma→Mama Thai→Jana Pawła 59a→Rukola Kacz NOWY→Jana Pawła 61B→Bacieczki
- OSRM ff 39.4 / TomTom **48.2** / 22.59 km
- **Sushi RJ pickup 16:32 → drop Bacieczki 18:02 = 90 min bag time (R6 BREACH ABSURDALNY)**
- Chinatown pickup 16:30 → drop Stroma 17:17 = 47 min R6 BREACH
- best_effort flag
**Adrian:** skip Rukoli, dokończ bag → Stroma→Mama Thai→Jana Pawła 59a→Jana Pawła 61B→Bacieczki
- OSRM ff 23.7 / TomTom 28.6 / 13.11 km (**-15.7 min OSRM / -19.6 min TomTom**)

### Case F — Dariusz M · Szklanki Talerze · Borsucza 10/33
**Bug:** A + E + F (Borsucza Dojlidy SE + Skidelska Białostoczek NW)
**System trasa:** Bacieczki→Jana Pawła 61B→[pickup Sushi+Szklanki]→Borsucza→Skidelska
- OSRM ff 34.2 / TomTom 41.2 / 19.91 km
- 3 z 4 orderów łamie R6: Mama Thai 55 min, Rukola Kacz 56 min, Sushi RJ 41 min
- best_effort flag

### Case G — Mateusz O · Street Mama Thai · Wiadukt 7
**Bug:** A + E
**System trasa:** RanyJulek→Saturna→[pickup Rukola+SMT]→Transportowa→Wiadukt
- OSRM ff 37.1 / TomTom **47.7** / 24.08 km
- Rany Julek pickup 18:26 → drop Saturna 19:09 = **43 min R6 BREACH**

---

## 3. Plan Implementacji — 5 Faz

### Faza 1: BUG E HOTFIX (dziś, ~30-60 min)

**Cel:** zatrzymać natychmiast wszystkie 4 nowe case'y (D, E, F, G).

**Krok 1.1 — znaleźć miejsce w kodzie**
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
grep -n "best_effort" dispatch_pipeline.py feasibility_v2.py shadow_dispatcher.py | head -30
```

**Krok 1.2 — implementacja (Aider deepseek-coder, ~5 min)**

```python
# dispatch_pipeline.py — w miejscu gdzie best_effort verdict jest decyzyjnie ustawiany
# Po obliczeniu plan.predicted_delivered_at i plan.pickup_at:

if best_effort_flag:
    # Policz bag_time per order w plan
    bag_times = {}
    for oid in plan.pickup_at:
        pu = plan.pickup_at[oid]
        do = plan.predicted_delivered_at.get(oid)
        if pu and do:
            bag_times[oid] = (do - pu).total_seconds() / 60.0
    max_bt = max(bag_times.values()) if bag_times else 0
    breach_count = sum(1 for bt in bag_times.values() if bt > BAG_TIME_HARD_MAX_MIN)

    # Flaga gating
    if (getattr(C, "ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT", True)
        and max_bt > BAG_TIME_HARD_MAX_MIN
        and breach_count >= 1):
        verdict = "KOORD"
        reason = f"best_effort_r6_breach_{breach_count}orders_max{int(max_bt)}min"
        # Surface w decision dict żeby render mógł dodać label „przepisane: R6 ochrona"
        decision["best_effort_r6_redirect"] = {
            "breach_count": breach_count,
            "max_bag_time_min": round(max_bt, 1),
            "orders_in_breach": [oid for oid, bt in bag_times.items() if bt > BAG_TIME_HARD_MAX_MIN],
        }
```

**Krok 1.3 — flaga + stała**
```python
# common.py
ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT = True  # default ON od początku
# BAG_TIME_HARD_MAX_MIN = 35 (już istnieje, common.py:318)
```

**Krok 1.4 — telegram_approver render**
Gdy `decision.best_effort_r6_redirect` jest dict → pokaż w wiadomości:
```
🟦 KOORD — przepisane do koordynatora.
   Powód: best_effort by spowodowało R6 breach dla {breach_count} orderów (max {max_bag_time} min).
   Ordery w breach: {oids}
   Sugestia: przesuń odbiór o ~10 min lub przepisz innemu kurierowi.
```

**Krok 1.5 — testy**
```python
# tests/test_best_effort_r6_redirect.py
# 3 testy:
# 1. best_effort + 1 R6 breach → verdict=KOORD (nie PROPOSE)
# 2. best_effort + zero R6 breach → verdict=PROPOSE (legacy, propozycja idzie)
# 3. flag OFF → legacy behavior (verdict=PROPOSE z R6 breach)
```

**Krok 1.6 — backup + deploy**
```bash
cp dispatch_pipeline.py dispatch_pipeline.py.bak-pre-best-effort-r6-2026-05-26
cp common.py common.py.bak-pre-best-effort-r6-2026-05-26
# str_replace edits
python3 -c "from dispatch_v2 import dispatch_pipeline; from dispatch_v2 import common"  # py_compile
pytest tests/test_best_effort_r6_redirect.py -v
git add common.py dispatch_pipeline.py tests/test_best_effort_r6_redirect.py
git commit -m "BUG E hotfix: best_effort + R6 breach → verdict=KOORD"
git tag bug-e-best-effort-r6-koord-2026-05-26
sudo systemctl restart dispatch-shadow
# dispatch-telegram WYMAGA Adrian ACK
```

**Krok 1.7 — verify**
- Smoke shadow 5-10 min, sprawdź czy żadne PROPOSE z `best_effort=True` AND `max_bag_time>35`.
- Adrian ACK przed restartem `dispatch-telegram`.

---

### Faza 2: BUG A + B Shadow Implementation (1-2 dni)

**Cel:** dodać metryki + scoring components do `shadow_decisions.jsonl`, flagi default OFF.

**Krok 2.1 — nowe metryki w bonus aggregation (`dispatch_pipeline.py:~2049`)**

```python
# === BUG A: Σ bag_time + max + FIFO ===
if getattr(C, "ENABLE_BAG_TIME_FAIRNESS_SCORING", False) and plan is not None:
    bag_times = {}
    pickup_order = []  # (pickup_at, oid) for FIFO
    for oid in plan.pickup_at:
        pu = plan.pickup_at[oid]
        do = plan.predicted_delivered_at.get(oid)
        if pu and do:
            bag_times[oid] = (do - pu).total_seconds() / 60.0
            pickup_order.append((pu, oid))
    pickup_order.sort()  # rosnąco po pickup_at

    sum_bag_time = sum(bag_times.values())
    max_bag_time = max(bag_times.values()) if bag_times else 0

    # FIFO violations: ile par (i,j) gdzie i picked-up wcześniej ale dropped później
    fifo_violations = 0
    for i, (pu_i, oid_i) in enumerate(pickup_order):
        for pu_j, oid_j in pickup_order[i+1:]:
            do_i = plan.predicted_delivered_at.get(oid_i)
            do_j = plan.predicted_delivered_at.get(oid_j)
            if do_i and do_j and do_i > do_j:
                fifo_violations += 1

    bonus_bag_time_sum = -C.BAG_TIME_SUM_PENALTY_PER_MIN * sum_bag_time
    bonus_bag_time_max = -C.BAG_TIME_MAX_PENALTY_PER_MIN * max_bag_time
    bonus_fifo_violation = -C.BAG_TIME_FIFO_TIE_PENALTY * fifo_violations

    # Sygnały do serializacji (LOC A + LOC B — checklist 5 miejsc)
    candidate["sum_bag_time_min"] = round(sum_bag_time, 2)
    candidate["max_bag_time_min"] = round(max_bag_time, 2)
    candidate["fifo_violations"] = fifo_violations
    candidate["bonus_bag_time_sum"] = round(bonus_bag_time_sum, 2)
    candidate["bonus_bag_time_max"] = round(bonus_bag_time_max, 2)
    candidate["bonus_fifo_violation"] = round(bonus_fifo_violation, 2)

# === BUG B: detour pickup-not-on-route ===
if getattr(C, "ENABLE_R5_PICKUP_DETOUR_PENALTY", False):
    detour_km = candidate.get("r5_pickup_detour_total_km", 0)  # już zbierane
    excess_km = max(0, detour_km - C.R5_DETOUR_FREE_THRESHOLD_KM)
    bonus_r5_detour = -C.R5_DETOUR_PENALTY_PER_KM * excess_km
    candidate["bonus_r5_pickup_detour_penalty"] = round(bonus_r5_detour, 2)
```

**Krok 2.2 — stałe w `common.py`**

```python
# === BUG A flagi + stałe ===
ENABLE_BAG_TIME_FAIRNESS_SCORING = False  # shadow-first, OFF
BAG_TIME_SUM_PENALTY_PER_MIN     = 1.0   # punkt startowy, calibrate po replay
BAG_TIME_MAX_PENALTY_PER_MIN     = 0.7   # extra dla najgorszego orderu
BAG_TIME_FIFO_TIE_PENALTY        = 5.0   # ile pkt za FIFO violation

# === BUG B flagi + stałe ===
ENABLE_R5_PICKUP_DETOUR_PENALTY  = False  # shadow-first, OFF
R5_DETOUR_PENALTY_PER_KM         = 8.0   # 1 km detour = -8 pkt (≈ R4 clip)
R5_DETOUR_FREE_THRESHOLD_KM      = 0.5   # <500m = naturalnie po drodze, bez kary

# === BUG F shadow metric (long-term) ===
ENABLE_CLUSTER_DROP_GROUPING_METRIC = False  # only metric, no scoring weight yet
```

**Krok 2.3 — serializer LOC A + B**
- Dodaj klucze do `_AUTO_PROP_PREFIXES` w `shadow_dispatcher.py` (jeśli istnieje prefix mechanism), albo explicit w `_serialize_candidate` + `_serialize_result`.
- Lekcja #80 (encoding checklist 5 miejsc): kod + tests + shadow serializer LOC A+B + learning_analyzer readers (do późniejszej fazy).

**Krok 2.4 — testy**

```python
# tests/test_bag_time_fairness_2026_05_26.py
# 1. Case #2 (Andersa) — z flag OFF: bonus=0
# 2. Case #2 z flag ON: bonus_bag_time_sum + bonus_fifo_violation < 0
# 3. Plan z 3 orderami pickup [13:00, 13:10, 13:20] drop [14:00, 13:50, 13:40]:
#    fifo_violations = 3 (każda para łamana)
# 4. Plan idealny FIFO: fifo_violations = 0

# tests/test_r5_pickup_detour_penalty_2026_05_26.py
# 1. detour 0.3 km → bonus=0 (<free threshold)
# 2. detour 1.5 km → bonus = -8*(1.5-0.5) = -8
# 3. detour 5 km → bonus = -8*4.5 = -36
```

**Krok 2.5 — deploy shadow**
```bash
git add common.py dispatch_pipeline.py shadow_dispatcher.py tests/test_bag_time_*.py tests/test_r5_*.py
git commit -m "BUG A+B shadow: bag_time fairness + r5 detour penalty (flags OFF)"
git tag bug-ab-shadow-impl-2026-05-26
sudo systemctl restart dispatch-shadow
# Verify czy metryki lądują w shadow_decisions.jsonl (5 min smoke)
```

---

### Faza 3: BUG C marker + ALERT (dziś lub jutro, ~20 min)

**Cel:** kiedy commit czas_kuriera odjeżdża od plan_eta > 3 min, surface to operatorowi.

**Krok 3.1 — renderer `telegram_approver.py:_route_lines_v2`**

```python
# linia ~1002-1009: gdy stop ma source=commit, sprawdź plan_eta dla diff
for dt, kind, oid, addr, source in stops:
    hhmm = dt.astimezone(WARSAW).strftime("%H:%M")
    icon = "🍕" if kind == "odbiór" else "📍"
    ta_marker = " ← TA" if oid == cur_oid else ""

    if source == "commit":
        # Sprawdź czy plan_eta odjeżdża
        plan_eta_iso = (pickup_at or {}).get(str(oid))
        if plan_eta_iso:
            plan_dt = _parse_iso(plan_eta_iso)
            if plan_dt:
                diff_min = (plan_dt - dt).total_seconds() / 60.0
                if abs(diff_min) > COMMIT_RENDER_DIVERGENCE_TILDE_MIN:
                    plan_hhmm = plan_dt.astimezone(WARSAW).strftime("%H:%M")
                    time_str = f"{hhmm}⚠plan~{plan_hhmm}"
                else:
                    time_str = hhmm
            else:
                time_str = hhmm
        else:
            time_str = hhmm
    else:
        time_str = f"~{hhmm}"
```

**Krok 3.2 — F4 AUTO-ROUTE rozszerzenie**
- `_detect_edge_routing` (`feasibility_v2` lub `dispatch_pipeline`) dorzucić reason `plan_commit_divergence` gdy `max_divergence_min > 3.0`
- ALERT zamiast ACK gdy ten reason fires

**Krok 3.3 — stałe**
```python
ENABLE_COMMIT_NEIGHBOR_CHECK     = True
COMMIT_NEIGHBOR_MIN_BUFFER_MIN   = 2.0
COMMIT_RENDER_DIVERGENCE_TILDE_MIN = 3.0
```

---

### Faza 4: Replay Calibration (po 3-7 dniach shadow)

**Cel:** skalibrować wagi `BAG_TIME_SUM_PENALTY_PER_MIN`, `BAG_TIME_MAX_PENALTY_PER_MIN`, `R5_DETOUR_PENALTY_PER_KM` na bazie real corpus.

**Tools:**
- `tools/sequential_replay.py --rolling` (per memory `ziomek_replay_harness.md`)

**Procedura:**
1. Zbierz 7-14 dni shadow data
2. Replay baseline (flagi OFF) — kontrola
3. Replay treatment (flagi ON ze startowymi wagami) — eksperyment
4. Compare:
   - Ile decyzji się zmieniło (% delta)
   - W jaką stronę (lepiej/gorzej wg metryk):
     - Σ bag_time per decision (oczekiwany spadek)
     - Max bag_time (oczekiwany spadek)
     - FIFO violations (oczekiwany spadek)
     - R6 breach count (oczekiwany spadek)
     - Total SLA breach (NIE oczekiwany wzrost — regresja gdyby wystąpiła)
     - best_effort decisions (oczekiwany wzrost — bo bardziej rygorystyczne)
5. Iteruj wagi:
   - Jeśli zmian za mało → zwiększ wagi 2× i replay
   - Jeśli regresja w SLA/capacity → zmniejsz lub dodaj guard

**Stop criteria:**
- Σ bag_time / decision spada o ≥ 8%
- Max bag_time / decision spada o ≥ 5%
- FIFO violations spada o ≥ 40%
- BRAK regresji w SLA breach (% identyczny ±2%)
- BRAK regresji w capacity (nie więcej dropniętych przez best_effort niż +5%)

---

### Faza 5: Live Flip (po Replay OK)

**Per-flag flip strategy (zgodnie z `feedback_rules.md`):**

```bash
# 1. Najpierw BUG B (najprostszy, najbezpieczniejszy)
# Hot-reload via flags.json:
python3 -c "import json; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_R5_PICKUP_DETOUR_PENALTY']=True; json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
# Observe 2 dni

# 2. Potem BUG A (głębszy)
# To samo, ale ENABLE_BAG_TIME_FAIRNESS_SCORING=True
# Observe 2 dni

# 3. Jeśli regresja w którymkolwiek — flag OFF natychmiast (hot-reload)
```

**Per `feedback_rules.md` 2026-05-24:** deploy od razu live (pre-autonomy), weryfikacja Z HISTORII po kilku dniach. **At-job 28-30.05** dla werdyktu keep/flag-off.

---

### Faza 6: BUG F (sprint długoterminowy)

**Cel:** klastry geograficzne w funkcji celu.

**Etap 0 (shadow metric, w Fazie 2):**
- Dodaj do każdej decyzji `cluster_grouping_score` = liczba razy gdy sekwencja przeskakuje między osiedlami w bagu
- Logging only, brak wpływu na scoring

**Etap 1 (po Fazie 5, sprint osobny 1-2 tygodnie):**
- Wzbogacić `districts_data.py` o pole `district_centroid` (lat, lon)
- W `route_simulator_v2._simulate_sequence` policzyć "cluster jumps" = ile razy sekwencja zmienia centroid_district
- Dodać bonus `bonus_cluster_grouping` = -CLUSTER_JUMP_PENALTY * jumps
- Stała startowa: `CLUSTER_JUMP_PENALTY = 5.0`

**To wymaga osobnego planowania w odrębnym sprincie — odłożone.**

---

## 4. Calibration Values (Punkty Startowe)

| Stała | Wartość startowa | Uzasadnienie | Replay calibration |
|-------|------------------|--------------|---------------------|
| `BAG_TIME_SUM_PENALTY_PER_MIN` | 1.0 | min Σ bag_time = punkt linearny | może wymagać ×2-3 dla Case #2 flip |
| `BAG_TIME_MAX_PENALTY_PER_MIN` | 0.7 | extra dla najgorszego (kara nierównomierności) | calibrate przez „lepiej oba 15 niż 25+8" |
| `BAG_TIME_FIFO_TIE_PENALTY` | 5.0 | gdy plany różnią się <2 min Σ, FIFO violator traci | startowe, replay-driven |
| `R5_DETOUR_PENALTY_PER_KM` | 8.0 | ≈ `bonus_r1_corridor` clip (-35), 4 km detour ≈ klif R1 | calibrate by aktualne Case C nie pass'owało |
| `R5_DETOUR_FREE_THRESHOLD_KM` | 0.5 | <500m = naturalnie po drodze | fixed (krajobraz miasta) |
| `COMMIT_RENDER_DIVERGENCE_TILDE_MIN` | 3.0 | < `V3274_FROZEN_PICKUP_WINDOW_MIN`(5), pokazuje rosnące napięcie | tweak based na false positives |

---

## 5. Pliki do dotknięcia

### Faza 1 (BUG E)
- `dispatch_pipeline.py` (decyzja verdict best_effort)
- `common.py` (flag + stała)
- `telegram_approver.py` (render label dla R6-redirect)
- `tests/test_best_effort_r6_redirect.py` (NEW)

### Faza 2 (BUG A+B)
- `common.py` (flagi + 7 stałych)
- `dispatch_pipeline.py` (bonus aggregation ~2049, +30 linii)
- `shadow_dispatcher.py` (serializer LOC A+B)
- `tests/test_bag_time_fairness_2026_05_26.py` (NEW)
- `tests/test_r5_pickup_detour_penalty_2026_05_26.py` (NEW)

### Faza 3 (BUG C)
- `telegram_approver.py` (renderer divergence marker)
- `feasibility_v2.py` / `dispatch_pipeline.py` (`_detect_edge_routing` reason)
- `tests/test_commit_divergence_render_2026_05_26.py` (NEW)

### Faza 4 (replay)
- `tools/sequential_replay.py` (sprawdzić czy obsługuje nowe sygnały, dorzucić jeśli nie)
- `eod_drafts/2026-05-XX/replay_bug_ab_analysis.py` (NEW, analysis script)

---

## 6. Validation Plan (A/B przed/po) — KONKRETNE DEADLINY

**🔴 CHECKPOINT #1 — niedziela 2026-05-31 wieczór 20:00 Warsaw (5 dni od diagnozy)**
- **Cel:** quick check BUG E (4 dni live od 27.05) + status shadow A+B+C
- **Kryteria do sprawdzenia:**
  1. `journalctl -u dispatch-shadow --since "2026-05-27 12:00"` → ile decyzji z `best_effort_r6_redirect` (powinno być > 0)
  2. `shadow_decisions.jsonl` → ile decyzji z metrykami `sum_bag_time_min`, `max_bag_time_min`, `fifo_violations`, `r5_pickup_detour_total_km` (powinno być > 100 dla wiarygodnej kalibracji)
  3. Adrian Q&A: czy widzi mniej „wpadek" typu Case D/E/F/G w propozycjach Telegramu (jakościowy sygnał)
  4. Brak regresji systemowych (`grep -i error journalctl` po deploy)
- **Decyzja:** 
  - JEŚLI BUG E OK + dość danych shadow → flip flag B live od 02.06 (BUG B najprostszy, najbezpieczniejszy)
  - JEŚLI mało danych shadow → kontynuować zbieranie do CHECKPOINT #2

**🔴 CHECKPOINT #2 — niedziela 2026-06-07 wieczór 20:00 Warsaw (12 dni od diagnozy)**
- **Cel:** pełna walidacja A/B przed/po dla BUG E + BUG B
- **Period split:**
  - Baseline collection: 28-30.05 (shadow only, flagi A+B+C OFF, metryki zbierane)
  - BUG E live: od 27.05 (hotfix natychmiast)
  - BUG B live flip: ~02.06 (po CHECKPOINT #1)
  - Treatment collection BUG B: 02-06.06 (4 dni live obserwacja)
- **Analiza:** 07.06 wieczór
- **Decyzja:**
  - JEŚLI BUG B OK → flip flag A (głębszy) live od 08.06
  - JEŚLI regresja → flag B OFF (hot-reload), debug, replay
- **Final A/B report** — porównanie:
  - Tydzień 19-25.05 (przed wszystko) vs 28.05-04.06 (po BUG E hotfix) vs 02-07.06 (po BUG B live)
  - Metryki w tabeli niżej

**🔴 CHECKPOINT #3 (opcjonalny) — niedziela 2026-06-14 wieczór 20:00 Warsaw (19 dni)**
- BUG A live (od 08.06) — pełna walidacja Σ bag_time fairness
- Sprint F (klastry osiedlowe) — design start

---

**Original period (referencyjne):**
- Baseline collection: 28-30.05 (przed flagi ON, shadow-only)
- Treatment collection: 31.05-02.06 (flagi ON live)
- Analysis: 03.06

**Metryki do porównania:**

| Metryka | Definicja | Cel | Próg regresji |
|---------|-----------|-----|---------------|
| Σ bag_time / decision | Suma bag_time orderów w bagu, mean | Spadek ≥ 8% | Wzrost > 3% |
| Max bag_time / decision | Najgorszy bag_time per plan | Spadek ≥ 5% | Wzrost > 2% |
| R6 breach rate | % propozycji z ≥1 R6 violation | Spadek ≥ 30% | Wzrost > 5% |
| best_effort PROPOSE | % decyzji `best_effort=True` ALE NIE KOORD | Spadek ≥ 50% (BUG E) | Wzrost > 0% |
| KOORD redirect | % decyzji verdict=KOORD | Wzrost umiarkowany | Wzrost > +15pp = za rygorystyczne |
| SLA breach | % orderów dostarczonych po SLA | Bez zmian (±2%) | Wzrost > 3% |
| FIFO violations / decision | Średnia liczba FIFO inversions w plan | Spadek ≥ 40% | Wzrost > 5% |
| Operator override rate | % propozycji odrzuconych przez koordynatora | Spadek | Wzrost > +3pp = system gorszy w ocenie operatora |

**Source danych:**
- `shadow_decisions.jsonl` (z nowymi metrykami)
- `events.db` (acceptance/override)
- Q&A Adriana (jakościowe — czy widzi mniej „wpadek" jak te 9 dziś)

**Stop kryteria (rollback):**
- Jeśli `SLA breach` lub `Operator override rate` rośnie powyżej progu → flag OFF natychmiast (hot-reload)
- Jeśli `KOORD redirect` rośnie > +15pp → BUG E gating za rygorystyczne, podnieść próg lub dodać whitelist (np. firmowe konta)

---

## 7. Rollback Plan (per bug)

### BUG E
```bash
# Soft (hot-reload, 5s):
python3 -c "import json; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT']=False; json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
# Hard (revert):
cd /root/.openclaw/workspace/scripts/dispatch_v2
git revert <commit-hash-bug-e> --no-edit
sudo systemctl restart dispatch-shadow
# dispatch-telegram wymaga Adrian ACK
```

### BUG A (jeśli regresja po flag ON)
```bash
python3 -c "import json; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_BAG_TIME_FAIRNESS_SCORING']=False; json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
```

### BUG B
```bash
python3 -c "import json; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_R5_PICKUP_DETOUR_PENALTY']=False; json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
```

---

## 8. Zdyscyplinowany Workflow (per dispatch_v2 CLAUDE.md)

Każdy step:
1. Draft → ACK Adriana
2. `cp .bak`
3. `str_replace` (Aider deepseek-coder dla > 30 LOC, SELF dla < 30 LOC)
4. `py_compile` + import check
5. `pytest tests/test_X.py -v`
6. `git commit` + `git tag`
7. `sudo systemctl restart dispatch-shadow`
8. Verify in `journalctl -u dispatch-shadow --since "5 minutes ago" | grep -i "error\|warn"`
9. **STOP for ACK** przed kolejnym etapem
10. **NIGDY** restart `dispatch-telegram` bez explicit ACK Adriana w czacie

---

## 9. Co już zostało zmierzone (referenc dla nowej sesji)

**Wszystkie OSRM 3-backend dane są w `eod_drafts/2026-05-26/measurements.md`** (osobny plik — patrz niżej).

Kluczowe coords użyte:

```
RESTAURACJE:
Toriko            53.13386, 23.15211
Rany Julek        53.13420, 23.14882
Sushi Rany Julek  53.13338, 23.14882  (różny cid, ten sam adres Lipowa 12)
Grill Kebab       53.13244, 23.16538
Pan Schabowy      53.15290, 23.08790  (Bacieczki NW)
Karczma Maciejówka 53.12463, 23.15171
Rukola Sienkiewicza 53.13729, 23.16820
Rukola Kaczorowskiego 53.12538, 23.15029
Sweet Fit & Eat   53.12824, 23.15240
Pani Pierożek     53.12451, 23.15095
500 stopni        53.13432, 23.15380
Chinatown Bistro  53.12188, 23.14617
Mama Thai Bistro  53.12188, 23.14617
Street Mama Thai  53.15396, 23.18629
Szklanki Talerze  53.13280, 23.15750

DROPY (z geocode_cache):
Wybickiego 12     53.15962, 23.19186  (Jaroszówka NE - daleko)
Wąska 4           53.14111, 23.16952  (Bojary)
Kraszewskiego 17c 53.13728, 23.17432  (Sienkiewicza E)
Chrobrego 12      53.13022, 23.18486  (Piasta I)
Jana Pawła 59a    53.13954, 23.10234  (Wysoki Stoczek W)
Jana Pawła 61B    53.14029, 23.09973
Stroma 23         53.12929, 23.10321
Bacieczki 223     53.13073, 23.08575  (Bacieczki NW)
Borsucza 10       53.10888, 23.19596  (Dojlidy SE)
Skidelska 16Ac    53.15533, 23.14969  (Białostoczek N)
Saturna 63        53.15753, 23.10157  (Bacieczki NW)
Transportowa 2A   53.11373, 23.12856  (S)
Wiadukt 7         53.09806, 23.13285  (S)
Andersa 14        53.15300, 23.17640  (z Andersa 28 proxy)
Tysiąclecia 56    53.15570, 23.15420  (Wysoki Stoczek N)
Kaczorowskiego 7  53.12538, 23.15029  (centrum SE)
Kombatantów 7     53.15009, 23.15959
Pułkowa 5         53.14334, 23.18290
Kręta 10          53.11278, 23.14439
Kopernika 3a      53.12053, 23.14383

TomTom API key:  /root/.openclaw/workspace/.env → TOMTOM_API_KEY
OSRM local:      http://localhost:5001
```

---

## 10. Reguła Adriana (cytat do utrwalenia w `feedback_rules.md`)

**REGUŁA-BAG-TIME-FAIRNESS (Adrian 2026-05-26):**

> „Suma czasów wszystkich dowozów w bagu powinna być jak najmniejsza. Lepiej żeby OBA jechały po 15 min, niż jedno 25 a drugie 8. Czym dalej czasu 35 min tym lepiej. Jeśli jest podobnie, najpierw to co zostało wcześniej odebrane, mimo że teraz będzie 200m bliżej, to później trzeba będzie nadrabiać."

**Implikacja dla solver'a:** funkcja celu MUSI uwzględniać `Σ bag_time` + `max bag_time` + FIFO tie-break, nie tylko `total_drive_min`.

**Implikacja dla detour:** „dowóz w żaden sposób nie jest po drodze" = jeśli detour > 1 km z istniejącej trasy, NIE wstawiaj nowego pickup'a do tego kuriera, lepiej przepisać innemu/koordynatorowi.

**Implikacja dla best_effort:** „już lepiej dać 10 min później" = gdy wszystkie opcje łamią R6, NIE proponuj „najmniejsze zło", przepisz do KOORD.

---

## 11. Lekcje do dopisania po sesji (lessons.md)

- **#145** — Renderer commit-priority maskuje plan-divergence (BUG C). Operator widzi fikcję; solver wie ale nie surfacuje. Reguła: visual + machine truth, nie tylko commit.
- **#146** — best_effort fallback nie powinien być transparentny dla operatora. Gdy każdy kandydat łamie R6, decyzją systemu jest KOORD, nie ALERT.
- **#147** — Funkcja celu solver'a musi odzwierciedlać operacyjną prawdę: `min Σ bag_time + min max bag_time + FIFO tie-break`, nie tylko `min total_drive_min`. Bag fairness > geometric efficiency w wielu case'ach (Case #2 TomTom potwierdza).
- **#148** — Klastry geograficzne (osiedla) są domeną którą system NIE rozumie. `districts_data.py` jest, ale TSP go ignoruje. Long-term: cluster_jump_penalty w cel.

---

## 12. Co dalej w NOWEJ SESJI

1. **READ FIRST** ten plik (`SPRINT_PLAN_geometry_fairness_bugs.md`)
2. **READ** `measurements.md` (osobny w tym folderze) dla wszystkich liczb OSRM/TomTom
3. **READ** `memory/sprint_timeline.md` `## CURRENT HANDOFF` (zaktualizowany 26.05)
4. **READ** `memory/tech_debt_backlog.md` (bugy A-F dodane na samej górze)
5. **START** od Fazy 1 (BUG E hotfix) — najpilniejszy + najmniejszy LOC
6. **ACK GATE** po każdej fazie

**Nie pomijaj kroku „cp .bak" przed edycją + py_compile + import check.** Workflow per dispatch_v2 CLAUDE.md.

**Nie restartuj `dispatch-telegram` bez explicit ACK Adriana.**

---

**Status końcowy sesji 2026-05-26:** DIAGNOZA + PLAN ZAPISANY. Implementacja jutro/dziś w nowej sesji z czystą głową.

**Last update:** 2026-05-26 (sesja diagnostyczna 9 case'ów)

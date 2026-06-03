# SPRINT PLAN — Test źródeł ruchu dla Ziomka (TomTom / HERE / Google) — A vs B

**Data utworzenia:** 2026-06-03
**Cel biznesowy:** dać Ziomkowi najlepsze możliwe dane o czasie jazdy w korku → zbić niedoszacowanie ETA (+11 min) i breache R6 (14%).
**Charakter:** w 100% SHADOW / offline measurement. ZERO zmiany w zachowaniu live dispatcha.

---

## 1. Pytania badawcze (co chcemy rozstrzygnąć)

1. Czy KTÓREKOLWIEK żywe źródło ruchu bije OSRM+stały-mnożnik w peaku? O ile (bcRMSE)?
2. Czy **overlay (A)** łapie większość zysku **pełnego routingu (B)**? → jeśli tak = ogromna oszczędność (A ≈ $0).
3. **TomTom vs HERE** — czyje dane są trafniejsze w Białymstoku (head-to-head)?
4. Jak daleko TomTom/HERE są od **Google oracle** (górna granica jakości)?

---

## 2. Ramiona testu (6 mierzonych + 2 kontrole)

| Ramię | Definicja | Skąd ETA | Koszt |
|---|---|---|---|
| **OSRM** (kontrola) | stan obecny | OSRM free-flow × obecny V326 mnożnik | $0 |
| **GPS-prawda** (kontrola) | ground truth | `pure_drive` z `gps_history` (tier-1) / delivery-derived (tier-2) | $0 |
| **OSRM-recalib** | darmowa krzywa | OSRM free-flow × **nowa krzywa godzinowa** (median-based) | **$0** |
| **TomTom-A** | overlay | OSRM free-flow × żywy współczynnik strefy (TomTom Flow) | ~$0 |
| **TomTom-B** | routing | TomTom Routing `traffic=true` (czas-w-korku gotowy) | ~$43/mc prod |
| **HERE-A** | overlay | OSRM free-flow × żywy współczynnik strefy (HERE Flow) | ~$0 |
| **HERE-B** | routing | HERE Routing v8 z ruchem | $0-449/mc prod |
| **Google-oracle** | próbka | Google Routes `TRAFFIC_AWARE`, TYLKO sampling (budżet) | $0 (kredyt) |

**OSRM-recalib = darmowa poprzeczka.** To NIE jest „kolejne API" — to OSRM free-flow przemnożony
przez krzywą godzinową z `hourly_multiplier_curve.md` (median-based, wyliczona z GATE B).
Liczona OFFLINE z już logowanego `osrm_freeflow_min` → zero API, zero ryzyka. Kluczowa rola:
**płatny ruch (A/B) musi pobić OSRM-recalib, nie surowy OSRM** — inaczej nie ma sensu płacić.

Uwaga: scenariusza C (świeży cache 10 min) NIE testujemy osobno — A/B przy forward-live i tak mierzą świeży ruch (≤35 min od delivery). C to wyłącznie kwestia TTL produkcyjnego, którą rozstrzygamy PO werdykcie A vs B.

---

## 3. Architektura pomiaru — reuse GATE B (forward-live)

Bazujemy na żywym harnessie: `eod_drafts/2026-05-14/tomtom_poc/` (crony aktywne).
Metodyka bez zmian: dla każdego świeżo zamkniętego tropu (≤35 min od delivery) mierzymy
WSZYSTKIE ramiona równolegle → bias-correction per bucket → **bcRMSE / win-rate / Pearson**.

```
zamknięty trop (oid) ──> measure_realworld_multi.py
     │  pickup_ll, delivery_ll, ts
     ├─ OSRM(free-flow)                → baza (kontrola + baza dla A/recalib)
     ├─ hourly_curve[hour] × freeflow  → OSRM-recalib   ($0, offline)
     ├─ TomTom Routing(traffic)        → TomTom-B
     ├─ HERE Routing(traffic)          → HERE-B
     ├─ zone_factor(midpoint, ts)      → ×OSRM = TomTom-A / HERE-A
     ├─ [sampling] Google Routes       → Google-oracle
     └─> rw_results_multi.jsonl  (per-arm duration_min + latency)

nocą: build_ground_truth.py → trips_realworld.jsonl  (tier-1 GPS gold + tier-2)
po 6-7 dniach: analyze_multi.py ⨝ po oid → ranking per bucket → verdict_notify (Telegram)
```

---

## 4. Komponenty do zbudowania

Nowy workdir: `eod_drafts/2026-06-03/traffic_test/` (kopia harnessu GATE B + rozszerzenia).
Klucze: `/root/.openclaw/workspace/.env` (wzór `_load_api_key`): `TOMTOM_API_KEY` (jest),
`HERE_API_KEY` (nowy, free bez karty), `GOOGLE_MAPS_API_KEY` (osobny billing, $200 kredytu).

### Faza 0 — Setup (Dzień 0, ~0.5 dnia)
- [ ] Założyć klucze HERE (freemium 250k/mc) + Google (osobny projekt billingowy, włączyć Routes API).
- [ ] `zones.py` — wygenerować ~12 stref Białegostoku **data-driven**: k-means (k=12) na koordach
      pickup+delivery z `candidate_decisions_*.jsonl` (ostatnie 14 dni). Zapis `zones.json`
      = `[{id, centroid_ll, n_trips}]`. Centroid = punkt do query Flow API.
- [ ] `smoke.py` — 1 call każdego API (TomTom route+flow, HERE route+flow, Google route),
      walidacja auth + format odpowiedzi + latency. Fail-loud.

### Faza 1 — Adaptery (Dzień 0-1)
Rozszerzyć wzór `measure_delta.py`. Każdy adapter → `{duration_min, distance_km, latency_ms, source, ok}`:
- [ ] `_osrm_call` — JEST (kontrola, baza dla A).
- [ ] `_tomtom_route_call` — JEST jako `_tomtom_call` (TomTom-B).
- [ ] `_here_route_call` — częściowo JEST (PoC `eod_drafts/2026-05-08/here_poc/measure_delta.py`); dopiąć (HERE-B).
- [ ] `_google_route_call` — NOWY, `TRAFFIC_AWARE`, budget-gated (Google-oracle).
- [ ] `_tomtom_flow_call(centroid_ll)` / `_here_flow_call(centroid_ll)` — NOWE; zwracają
      `current_speed` + `free_flow_speed` → `ratio = free_flow / current` (≥1.0 = wolniej).
- [ ] `overlay_eta(osrm_freeflow_min, zone_ratio)` = `osrm_freeflow_min * zone_ratio` (A-arm).
- [ ] `recalib_eta(osrm_freeflow_min, pu_epoch)` = `osrm_freeflow_min × HOURLY_CURVE[dzień][hour_warsaw(pu)]`
      — **OSRM-recalib, ZERO API**, liczone offline z logu. Tabela = `hourly_multiplier_curve.md`
      (weekday) + weekend = obecna `V326_OSRM_TRAFFIC_TABLE`. Godzina bez danych w krzywej → fallback obecny V326.
      Liczone retroaktywnie dla WSZYSTKICH dotychczas zebranych tropów (3 451 w rw_results) → werdykt od razu, nie po 7 dniach.
- [ ] Każdy adapter: timeout 3s, try/except fail-soft (`ok=False`, NIE wywala harnessu), 429 backoff.

### Faza 2 — Flow poller (Dzień 1) — infrastruktura dla ramion A
- [ ] `flow_poller.py` — co 5 min, dla każdej strefy: TomTom Flow + HERE Flow na centroid →
      append `flow_log.jsonl` `{ts, zone_id, provider, ratio, free_flow_kmh, current_kmh}`.
- [ ] Cron: `*/5 7-21 * * *` (UTC = 09:00-23:55 Warsaw; **CRON_TZ NIE honorowany — liczyć w UTC**).
- [ ] `zone_factor(ll, ts, provider)` — helper: znajdź strefę punktu (najbliższy centroid) →
      najświeższy `ratio` z `flow_log` dla (zone, provider) ≤ ts. Fallback ratio=stały-mnożnik gdy brak.
- [ ] Koszt poller: 12 stref × 2 dostawców × 12/h × 15h ≈ **4 320/dobę**. TomTom free 2 500/dobę
      → niewielki overage; na 7-dniowy test = kilka $ albo zejść do co 10 min (→ ~2 160/dobę, w free).

### Faza 3 — Harness pomiarowy (Dzień 1-2, potem leci 5-7 dni)
- [ ] `measure_realworld_multi.py` (fork `measure_realworld.py`): per świeży trop woła WSZYSTKIE
      ramiona, dla A czyta `zone_factor(midpoint(pickup,delivery), ts)`. Idempotent (skip zmierzone oid).
      → `rw_results_multi.jsonl`.
- [ ] **Google budget-gate:** licznik dzienny w `google_budget.json`; sampling priorytetowo PEAK,
      cap **500/dobę**, hard-stop globalny **3 000** (≈$30 z $200 kredytu → realnie $0). Po capie skip.
- [ ] Cron: `*/10 7-22 * * *` (UTC, jak GATE B).

### Faza 4 — Ground truth (ciągłe)
- [ ] Reuse `build_ground_truth.py` → `trips_realworld.jsonl` (tier-1 GPS + tier-2 derived). Cron `30 3 * * *`.
      (Działa już dla GATE B — wystarczy wskazać ten sam plik.)

### Faza 5 — Analiza + werdykt (Dzień 6-7)
- [ ] `analyze_multi.py` (fork `analyze_realworld.py`): join `rw_results_multi` ⨝ `trips_realworld`
      po oid → per-ramię bias-correction per bucket (peak/shoulder/offpeak) → bcRMSE / win-rate /
      Pearson. **Tabela rankingowa per bucket.** Cross-check na tier-1 (GPS gold). MIN_SAMPLE=25/bucket.
- [ ] `verdict_notify.py` (fork `gate_b_verdict_notify.py`) — odpala analyze + Telegram (`send_admin_alert`)
      + zapis `verdict_2026-06-1X.txt`.
- [ ] **at-job** (UTC): werdykt ~Dzień 8 rano. (`at`, NIE `/schedule` — dane lokalne na serwerze.)

---

## 5. Kryterium werdyktu / decyzji

Główna metryka = **bcRMSE w buckecie PEAK** (tam boli). Decyzja **2-stopniowa** (recalib jest poprzeczką):

**Krok 0 — darmowa rekalibracja (niezależna od reszty):**
- OSRM-recalib vs OSRM (obecny): czy krzywa godzinowa bije obecną tabelę V326? (oczekiwane TAK —
  diagnoza pokazała −1 do −2,4 min medianowego niedoszacowania 12–16/18–19).
- Jeśli tak → **promuj krzywę do V326 niezależnie od werdyktu płatnych** (zero kosztu, zero ryzyka).

**Krok 1 — czy płatny ruch ma sens:**
- Płatne ramię (A/B) musi pobić **OSRM-recalib** (nie surowy OSRM!) o **≥0.75 min I ≥10%** ORAZ win-rate >55%.
- Inaczej: OSRM-recalib zostaje, płatnego API NIE wdrażamy (Z3 — prościej, bez zależności).

**Krok 2 — przy remisie płatnych:** preferuj tańsze/prostsze: A > B (koszt), TomTom > HERE, unikaj Google w prod.
Osobno raportuj „ile A traci do B" (jeśli <0.3 min w peaku → idź w A, ≈$0).

Decyzja produkcyjna (po werdykcie):
- Krzywa godzinowa → podmiana bloku `"weekday"` w `common.py:V326_OSRM_TRAFFIC_TABLE` (mechanizm już LIVE).
- A wygrywa/remis z B → wepnij overlay w `osrm_client._apply_traffic_multiplier` (żywy ratio zamiast tabeli), flag-gated, shadow→live.
- B wyraźnie lepsze → rozważ scenariusz C (TTL 60→10 min) tylko dla `route()`, macierze zostają na OSRM.

---

## 6. Koszt testu (7 dni) + guardrails

**Koszt pomiaru** (per trop ~450/dobę): TomTom-B + HERE-B = ~2 płatne calle/trop = ~900/dobę każdy →
w darmowych progach (TomTom 2 500/dobę, HERE 250k/mc). Flow poller ~2-4k/dobę. Google ≤500/dobę capped.
**→ realny koszt całego testu ≈ $0-10.**

Guardrails:
- [ ] WSZYSTKO shadow — harness nie dotyka `dispatch_pipeline` ani Telegrama live (poza werdyktem).
- [ ] Każdy adapter fail-soft + circuit-breaker (jeden padnięty dostawca NIE psuje pomiaru reszty).
- [ ] Google hard-cap (dzienny + globalny) — najważniejszy guard kosztowy.
- [ ] 429 → exponential backoff; loguj `latency_ms` (porównanie SLA przy okazji).
- [ ] MIN_SAMPLE=25/bucket — poniżej werdykt = „niemiarodajny", crony lecą dalej.

---

## 7. Harmonogram (daty konkretne)

| Dzień | Data | Co |
|---|---|---|
| 0 | 03-04.06 | klucze HERE/Google, `zones.py`, `smoke.py`, adaptery |
| 1 | 04-05.06 | flow_poller LIVE, measure_realworld_multi LIVE, crony |
| 1-7 | 05-11.06 | zbieranie danych (forward-live) |
| 8 | ~12.06 | at-job → werdykt na Telegram + `verdict_2026-06-12.txt` |

(Jeśli peak <25 w dniu werdyktu → za mało danych, odpalić analyze ręcznie kilka dni później.)

---

## 8. Sprzątanie po teście
- [ ] Usunąć crony testowe (`crontab -e`): flow_poller, measure_realworld_multi.
- [ ] `atq` — sprawdzić/usunąć at-job werdyktu.
- [ ] GATE B (oryginalny tomtom_poc) — zdecydować czy zostaje czy wygaszamy (zastąpiony multi).
- [ ] Wpis do `sprint_timeline.md` (2-4 linie) + lekcja jeśli jest.

---

## Byproduct (niezależny od werdyktu)
- Latency per dostawca (SLA) — czy któryś nadaje się do hot-path produkcyjnego.
- Dwelle z tier-1 GPS (kalibracja DWELL) — kontynuacja bocznego wątku GATE B.
- `zones.json` — reużywalna siatka stref do innych feature'ów (np. fleet load per rejon).

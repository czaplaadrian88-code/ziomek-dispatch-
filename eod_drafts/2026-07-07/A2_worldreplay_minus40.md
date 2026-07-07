# A2 — Diagnoza world-replay: różnica DOKŁADNIE −40 (485986/485987/485988)

**Data:** 2026-07-07 · **Tryb:** READ-ONLY (żaden kod/flaga/usługa nietknięte) · **Werdykt gate:** `world_replay_gate_verdict.txt` z 02:00 UTC (okno since=2026-07-06T02:00).

## TL;DR (1 zdanie)
Te −40 to **kara load-governora `bonus_loadgov_shadow_delta = LOADGOV_BAG_PENALTY = −40.0`** (`core/candidates.py:1559`, aplikowana `:1790` za żywą flagą `ENABLE_FLEET_LOAD_GOVERNOR=True`); rozjazd to **LUKA NAGRYWANIA, NIE bug determinizmu** — dotknięte rekordy są `schema=wr0` (sprzed deployu wr1 ~01:00 Warsaw 07.07), więc replay nie ma nagranej krotki `loadgov`, a in-proc EWMA jest nieodtwarzalna z dysku → replay nie nakłada −40.

---

## 1. KTÓRY składnik = te 40 (nazwa + plik:linia)

| Element | Lokalizacja | Wartość |
|---|---|---|
| Definicja kary | `core/candidates.py:1559` `bonus_loadgov_shadow_delta = float(getattr(C, "LOADGOV_BAG_PENALTY", -40.0))` | **−40.0** |
| Warunek zapłonu | `core/candidates.py:1556-1558` | `loadgov_ewma is not None` **AND** `loadgov_ewma > LOADGOV_TIGHTEN_AT (2.7)` **AND** `len(bag_raw) >= LOADGOV_BAG_MIN (3)` |
| Aplikacja do score | `core/candidates.py:1790` `final_score = final_score + bonus_loadgov_shadow_delta` | za `C.decision_flag("ENABLE_FLEET_LOAD_GOVERNOR")` |
| Stała flaga | `flags.json` (żywy, hot-reload) | `ENABLE_FLEET_LOAD_GOVERNOR = True` (default w `common.py:2566` = OFF, ale flags.json wygrywa — kara **LIVE**) |
| Stałe | `common.py:2559-2563` | `LOADGOV_TIGHTEN_AT=2.7`, `LOADGOV_BAG_MIN=3`, `LOADGOV_BAG_PENALTY=-40.0` |
| Mapa flaga→delta | `dispatch_pipeline.py:2407` `("ENABLE_FLEET_LOAD_GOVERNOR", "bonus_loadgov_shadow_delta")  # -40, LIVE` | potwierdza „LIVE" |

**Kandydaci ODRZUCENI:**
- `bonus_r5_detour = -40.0` (`core/candidates.py:1239`) — to bucket (0/−5/−15/−40 wg detour km). W shadow logu dla dotkniętych orderów `r5_detour` = 0 lub −5 (NIE −40). Odrzucony.
- `PBREACH_GOV_COEFF = 40.0` (`core/candidates.py:1575`) — ciągły `−40 × p_breach`, nie daje równego 40; to czysta telemetria (nie dodawana do score). Odrzucony.

## 2. Dowód że te −40 to loadgov (shadow_decisions.jsonl, best candidate)

| order | best_score (zapis) | loadgov_delta_best | loadgov_ewma_best | r5_detour_best | −40? |
|---|---|---|---|---|---|
| 485986 | −375.752 | **−40.0** | 2.998 | −5.0 | ✅ |
| 485987 | −486.798 | **−40.0** | 3.012 | 0.0 | ✅ |
| 485988 | −223.270 | **−40.0** | 3.006 | −5.0 | ✅ |
| 485978 | −366.653 | **−40.0** | 2.74 | 0.0 | ✅ |
| 485982 | −453.530 | **−40.0** | 2.962 | −5.0 | ✅ |
| 485981 | −444.257 | **−40.0** | 2.962 | 0.0 | ✅ |
| 485965 | −117.68 | 0.0 | **1.865** (<2.7) | 0.0 | ❌ (diff ~20 = szum OSRM) |
| 485966 | −142.99 | 0.0 | **1.897** (<2.7) | 0.0 | ❌ (diff ~29 = szum OSRM) |

Wniosek: peak wieczorny 18:0x–18:30 → `loadgov_ewma` przekroczyło 2,7 → kara −40 nałożona LIVE dla worków ≥3. Wcześniejsze ordery (17:2x, ewma ~1,9) kary nie miały — ich rozjazdy (~20–29) to niezależny szum OSRM/geometrii, dlatego NIE są równe 40. To potwierdza, że „stałe 40" pochodzi z jednego binarnego składnika (loadgov), a nie z zaokrągleń.

## 3. Werdykt: LUKA NAGRYWANIA vs BUG determinizmu → **LUKA NAGRYWANIA**

**Mechanizm gapu (nagrywanie):**
- Silnik liczy `loadgov` (`dispatch_pipeline.py:3854`) i nagrywa krotkę hookiem `note_decision_input("loadgov", ...)` (`dispatch_pipeline.py:3861`). Komentarz `:3856-3858`: *„zależy od orders_state.json + in-proc EWMA — nieodtwarzalne w świeżym procesie replayu"*.
- **Ale dotknięte rekordy to `schema=wr0`** — nagrane 2026-07-06 wieczór, **PRZED** deployem wr1 (~00:42–01:26 Warsaw 07.07). Empirycznie: plik `world_record-20260706.jsonl` = **97/97 rekordów wr0**, `live_inputs` puste, `loadgov=null` dla WSZYSTKICH 3 case'ów (i 485978/981/982/927/965/966).

**Mechanizm gapu (replay):**
- `tools/world_replay.py:_serve_live_inputs` (`:227-229`) czyta `live_inputs.loadgov`; brak → zwraca `loadgov=None`.
- `tools/world_replay.py:265-266`: patch `dp._loadgov_compute` na nagraną krotkę **tylko gdy `_loadgov_rec is not None`**. Dla wr0 → None → **patcha brak** → oryginalny `_loadgov_compute` liczy w świeżym procesie replayu (godziny później), gdzie in-proc EWMA nie istnieje → `loadgov_ewma` cold/None/inne → warunek `>2.7` fałszywy → **replay nie nakłada −40** → replay o 40 wyżej. Kierunek zgadza się co do joty (zapis 40 NIŻSZY = z karą; replay bez kary).

To NIE jest niedeterminizm silnika: przy tym samym wejściu `decide` daje ten sam wynik. Rozjazd wynika WYŁĄCZNIE z braku nagranego wejścia `loadgov` w rekordzie wr0.

**Czy wr1 to zamyka?** ✅ **TAK, dla rekordów od 07.07.** Plik `world_record-20260707.jsonl` = **218/218 rekordów wr1**, `loadgov` obecny w **218/218** (100%), wszystkie poprawne 4-krotki `[now, ewma, orders, couriers]`; `ewma>2,7` w 117 (peak). Przykłady: `486006 → [3.0, 2.76, 6, 2]`, `486047 → [3.125, 2.778, 25, 8]`. Dla wr1 replay serwuje nagraną krotkę → kara −40 się odtworzy → rozjazd znika. Rekordów wr0 z 07.06 nie da się domknąć wstecznie (in-proc EWMA bezpowrotnie utracona) — to oczekiwane i akceptowalne.

## 4. Case 485927 — INNY mechanizm (nie loadgov)

485927 (15:39, `schema=wr0`) to **rozjazd puli feasibility, NIE −40**: replay `feasible=5 best=484 score=80.33` vs zapis `feasible=6 best=179 score=−54.94`. Żywa pula miała o 1 kuriera feasible więcej (179) i on wygrał niskim score. To odrębna luka nagrywania (wr0 nie nagrywa też `k07` prefetchu czas_kuriera — od wr1 nagrywany, `dispatch_pipeline.py:4047`), rozstrzygalna dopiero na rekordach wr1. Notatka „jedyny realny do diagnozy przy v1" = wszystkie pozostałe krytyczne to znany gap loadgov-40; 485927 to jedyny, którego −40 NIE tłumaczy, więc jedyny wart ponownej weryfikacji na świeżych wr1. **Hipoteza: kolejny gap wr0 (pula/k07), nie bug** — potwierdzenie na wr1.

## 5. Czy wymaga naprawy? (NIE naprawiam teraz)

- **Silnik / nagrywanie: BEZ zmian.** Gap jest JUŻ zamknięty przez wr1 (deploy 07.07). Kara −40 działa poprawnie i celowo (governor floty w peaku).
- **Rezydualny szum w bramce (opcjonalne, do decyzji Adriana):** nocna bramka (02:00 07.07) leciała po oknie zdominowanym przez rekordy wr0 → wygenerowała 12 `ROZNICA-KRYTYCZNA`, z których ≥6 to czysty artefakt gapu loadgov na wr0 (nie realne rozjazdy). Werdykt ma już bucket „pominięte now=null: 9". Analogiczny **schema-aware bucket „pominięte/downgrade schema=wr0"** (albo: diff wyłącznie na loadgov przy wr0 → miękki zamiast krytyczny) usunąłby fałszywe alarmy krytyczne, gdy okno bramki obejmuje jeszcze rekordy sprzed wr1. To refinement RAPORTOWANIA bramki (`tools/world_replay_gate.py`), NIE fix silnika. Po ~1 dniu, gdy okno bramki będzie w całości wr1, problem zniknie sam.

---

### Artefakty/dowody (do wglądu)
- Rekordy: `dispatch_state/world_record/world_record-2026070{6,7}.jsonl` (wr0: 97/97 loadgov=null; wr1: 218/218 loadgov present).
- Log żywych decyzji: `logs/shadow_decisions.jsonl` (best.`bonus_loadgov_shadow_delta`=−40, `loadgov_load_ewma` 2,74–3,01 dla 6 case'ów).
- Kod: `core/candidates.py:1556-1559,1790`; `common.py:2559-2566`; `dispatch_pipeline.py:2407,3854-3864,4047`; `tools/world_replay.py:227-229,265-266`; `world_record.py:60-97,220-284`.

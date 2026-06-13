# BUG-C re-check progu COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN (analiza, P3)

**Data:** 2026-06-13 | **Sesja:** auton/geo-districts (GEO-front audytu) | **Status flagi:** `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE=OFF` (NIE flipowane — tylko analiza)

## Pytanie
Czy próg 10 min (`COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN=10.0`) to dobra wartość gate'a, który przy rozjeździe `plan_eta(pickup) − commit(czas_kuriera_warsaw) > próg` (jednostronnie, plan PÓŹNIEJ niż commit → ryzyko zimnej potrawy) eskaluje propozycję do KOORD zamiast PROPOSE/AUTO z markerem `⚠plan~HH:MM`.

## Dane
Źródło: `logs/commit_divergence_resolutions.jsonl` (65 rekordów z okna 2026-05-27 → 2026-06-01, 6 dni, kiedy gate był ON; flaga później wróciła do OFF). Każdy rekord = jedno odpalenie gate'a + jak człowiek rozwiązał zlecenie w panelu (`human_resolved.courier_id`, `delta_min` = czas od decyzji Ziomka do przypisania ręcznego).

Mechanika gate'a (`dispatch_pipeline.py:5108-5177`): liczy `max` po wszystkich bag-pickupach z `plan.pickup_at[oid] − commit_iso[oid]`, jednostronnie dodatnio; gdy `> próg` → `PipelineResult(verdict=KOORD, reason=commit_divergence_gate ...)`, early-return przed serializacją.

## Rozkład rozjazdu (wszystkie odpalenia, z definicji >10 min)
- n=65, min=10.0, **median=16.0**, mean=16.0, p75=19.1, p90=21.5, max=26.6 min
- buckety: 10-12 min → 18 | 12-15 → 13 | 15-20 → 20 | >20 → 14

## Czy eskalacja była uzasadniona? (główny test)
Porównanie: kandydat, którego gate ZAWETOWAŁ (`ziomek_proposed.courier_id`), vs kogo realnie przypisał koordynator (`human_resolved.courier_id`).

| Wynik | n | % | Interpretacja |
|---|---|---|---|
| Człowiek przypisał **INNEGO** kuriera | 54 | **83%** | Gate słusznie eskalował — zawetowany kandydat faktycznie NIE był wyborem człowieka |
| Człowiek przypisał **TEGO SAMEGO** (zawetowanego) | 11 | 17% | Gate „przesadził" — człowiek i tak wybrał tego co Ziomek (over-escalation) |

**Szybkość reakcji człowieka:** median `delta_min`=1.6 min, 71% rozwiązanych ≤3 min → koordynator miał gotową odpowiedź, eskalacja trafiła w realne wąskie gardło (nie generowała szumu wymagającego długiego namysłu).

## Czy próg powinien być inny? (per-bucket actionability)
| Bucket | n | człowiek-INNY (gate trafny) | człowiek-TEN SAM (over-escalation) | median resolve |
|---|---|---|---|---|
| **10-12 min** | 18 | 16 | **2 (11%)** | 1.66 min |
| 12-15 min | 13 | 11 | 2 (15%) | 1.49 min |
| 15-20 min | 20 | 15 | 5 (25%) | 1.70 min |
| >20 min | 14 | 12 | 2 (14%) | 1.33 min |

Kluczowe: **band najbliższy progowi (10-12 min) ma NAJNIŻSZĄ stopę over-escalation (11%)** — nawet na samej granicy 89% eskalacji było trafnych. To znaczy, że obniżenie progu poniżej 10 nie jest sprzeczne z danymi (gate dalej byłby trafny), ale danych <10 min nie ma (gate odpalał tylko >10). Podniesienie progu (np. do 15) wyrzuciłoby 31 trafnych eskalacji z band 10-15 (27 z 31 = 87% trafnych), żeby uniknąć 4 over-escalation — zła wymiana.

## WERDYKT
**Próg 10 min jest dobrze skalibrowany. NIE zmieniać.**
- 83% eskalacji trafnych globalnie; band graniczny 10-12 min trafny w 89%.
- Median rozjazdu 16 min potwierdza, że gate łapie REALNE rozjazdy (nie szum przy 10-11 min).
- Koszt over-escalation niski: 17% przypadków gdzie człowiek i tak wybrał zawetowanego — ale to KOORD (człowiek dostaje pełną decyzję z markerem), więc „koszt" = jedno kliknięcie zamiast auto-PROPOSE, nie błędna dostawa.
- Podniesienie progu = utrata trafnych eskalacji w najgęstszym paśmie (10-15 min = 31/65 = 48% wolumenu). Obniżenie = brak danych potwierdzających + ryzyko szumu przy 8-9 min (nie zmierzone).

## ⚠ Zastrzeżenia (uczciwość danych)
1. **Próbka mała i z jednego okna** (65 rekordów, 6 dni, koniec maja). Rozjazdy zależą od sezonu/restauracji — rekalibrację warto powtórzyć po kolejnym oknie ON.
2. **„Over-escalation" liczone konserwatywnie** przez równość cid zawetowany vs przypisany — nie uwzględnia, że człowiek mógł przypisać tego samego kuriera, ale dla INNEGO rozłożenia worka (wtedy rozjazd realnie zniknął — gate i tak miał rację). Czyli realna trafność może być >83%.
3. **Flaga jest OFF** — to analiza historyczna z okna gdy była ON. Bieżący `shadow_decisions.jsonl` (551 rekordów) ma `commit_divergence_redirect=null` we wszystkich (gate nie liczy przy OFF), więc nie da się odświeżyć rozkładu bez ponownego włączenia gate'a (decyzja Adriana — poza zakresem tej sesji).

## Rekomendacja operacyjna
Zostawić próg 10 min i flagę w obecnym stanie (OFF). Jeśli Adrian zdecyduje włączyć gate ponownie (eskalacja zimna-potrawa), 10 min wejdzie bez zmian. Przy ewentualnym re-tune: zebrać ≥150 rekordów z 2+ okien przed jakąkolwiek zmianą wartości.

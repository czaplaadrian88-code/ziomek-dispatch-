# C12 — checkpoint_tz (ENABLE_CHECKPOINT_TS_WARSAW_PARSE) — LANE C RUNTIME-ORACLE

**Agent:** C12-checkpoint-tz · **lane C** (oracle, C9/C11) · **klasa L (TZ)** · READ-ONLY · 2026-06-30 ~17:35 UTC
**Werdykt: VALIDATED** (claim potwierdzony DRUGĄ, niezależną metodą). **Kolektor SŁUSZNIE disabled.**
Numery linii re-grepowane świeżo dziś. Zero edycji/restartów/flipów. Output recompute → scratchpad/ten plik (NIE dispatch_state).

---

## 0. CLAIM badany
Rejestr/seed: „checkpoint-TZ VALIDATED, flaga LIVE ON, kolektor disabled po at-182 CLEAN (interp_on 958, bag_dropped=ghost cid393/1)". Klasa L. Flaga `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` = **flags.json:221 `true`** (common.py:3002 env-default `"0"`=OFF → flags.json hot-reload nadpisuje na ON; `_f4_flag` = `bool(flag(name, globals().get(name,False)))` → **efektywnie ON**).

**Istota fixu (courier_resolver.py:42-67 `_parse_checkpoint_ts`):** `picked_up_at`/`delivered_at` w `orders_state` to **NAIWNE stringi czasu Warszawy** z panelu Rutcom (format `"2026-06-29 13:06:31"`, bez `T`/offsetu).
- **OFF (legacy):** `datetime.fromisoformat(...).replace(tzinfo=UTC)` → naiwny string STEMPLOWANY jako UTC (BŁĄD: ~2h do przodu w UTC → świeże zdarzenia wyglądają jak PRZYSZŁOŚĆ).
- **ON (fix):** `parse_panel_timestamp` (common.py:441) → naive→Warszawa→`astimezone(UTC)` (poprawne).

---

## 1. ORACLE — prawda DRUGĄ, NIEZALEŻNĄ metodą

### 1a. Recompute na ŻYWYM `orders_state.json` (588 checkpointów, dziś 17:31 UTC)
Czytam realne `picked_up_at`/`delivered_at`, liczę wiek OBIEMA gałęziami:
```
checkpoints total:588   naive(no-T):585   aware(T/offset):3
NEGATIVE age (future-dated) under OFF: 94/588   under ON: 0/588
age_ON − age_OFF dla naive: median = +120.0 min  (= dokładnie offset Warszawy UTC+2)  n=585
```
**Dowód twardy:** OFF zaniża wiek KAŻDEGO naiwnego checkpointu o **dokładnie 120 min** (offset Warszawy) → **94/588** świeżych checkpointów wygląda jak PRZYSZŁOŚĆ (age<0). ON: **0/588** ujemnych — wszystkie wieki sensowne. 585/588 (99,5%) to naiwne stringi = powierzchnia błędu realna i dominująca.

### 1b. Code-trace 4 BRAMEK (każda gałąź zgodna z kierunkiem danych jsonl)
4 miejsca w `courier_resolver` czytają surowe pole stanu i idą przez TEN SAM helper (twin-complete WEWNĄTRZ modułu):
| linia | gałąź | bramka | OFF (age fałsz.) | ON (age realny) |
|---|---|---|---|---|
| 668-684 | `_compute_interp_pos` | `if elapsed_min < 0: return None` (672) | świeży pickup→PRZYSZŁOŚĆ→neg→**interp MARTWY** | dodatni→**interp ODPALA** |
| 642-646 | `_bag_not_stale` | `age_min < _threshold` (tylko górna granica) | neg<thr → ZAWSZE „świeży" → zombie ZOSTAJE | realny → >thr → DROP (ghost) |
| 613-627 | ZOMBIE-01 guard | `(now−ts)>_threshold` | przyszłość→neg→NIE >thr → zombie zostaje | realny → >thr → odfiltruj |
| 1045-1062 | `last_delivered`/`last_picked_up_recent` | `if age<0 or age>=30: continue` (1057) | dostawa SPRZED ~120-150min wygląda jak „0-30min świeża"→**fantom pozycji** | realny wiek 120-150→ODRZUĆ→**no_gps** |

### 1c. Re-agregacja ZAMROŻONEGO jsonl (386 tików, 26.06 12:40 → 27.06 21:06)
```
interp_off=0   interp_on=958   (median 2/tik, max 10, zer:142)
rescued_from_synth=1   pos_source_changed=1405   pos_moved_gt50m=1548
bag_dropped_couriers=5   bag_dropped_orders=5
TOP transitions src_off→src_on:
  953  last_picked_up_pickup → last_picked_up_interp   (INTENCJONALNY win: interp ożywa)
  245  last_delivered        → no_gps                  } REAL→SYNTH = 335
   90  last_picked_up_recent → no_gps                  } = KOREKTY fantomów
  147  last_delivered        → last_delivered
   95  last_picked_up_recent → last_delivered
REAL→SYNTH (ON traci pozycję) = 335   |   SYNTH→REAL (ratunek) = 1
BAG DROPS: 5 / 386 tików, WSZYSTKIE cid 393, WSZYSTKIE gps→gps, bag 4→3 / 5→4, peak 12:40-12:58
```

### 1d. Determinizm (≥2 odpalenia, czysta funkcja)
2 przebiegi parse na stałych fixturach = BAJT-IDENTYCZNE. Naive(space) → ON o 120min WCZEŚNIEJ w UTC (Warszawa→UTC). **Aware(`T`/offset) → delta 0** = fix jest BEZPIECZNYM NO-OP na już-poprawnych ts (3/588 takich, bez kolateralu).

---

## 2. INTERPRETACJA — czy „REAL→SYNTH 335" i „bag_dropped" to KOREKTY czy REGRESJA?

**335 `*→no_gps` = KOREKTY, nie regresja.** Bramka 1045:1057 `if age<0 or age>=30: continue` ma OBIE granice. OFF odejmuje 120min → dostawa naprawdę sprzed ~120-150min wygląda jak „0-30min świeża" → silnik używał **STAREJ pozycji dostawy jako bieżącej** (kurier dawno odjechał — realny operacyjny błąd: fałszywe „blisko restauracji X"). ON widzi realny wiek → odrzuca → uczciwe `no_gps`. **ŻADNEJ utraty realnie-świeżej pozycji:** ON nigdy nie produkuje age<0 (0/588), a bramka ma guard `age<0`. Pod R-NO-GPS-EQUAL-TREATMENT (flaga ON) `no_gps` = traktowany RÓWNO (bez kary) → korekta nie krzywdzi kuriera, prostuje ranking.

**bag_dropped=5 = REALNY GHOST, nie legit.** 5 dropów / 386 tików, WSZYSTKIE jeden kurier cid 393, WSZYSTKIE `gps→gps` (źródło pozycji NIETKNIĘTE — to nie artefakt pozycji), bag 4→3/5→4 = JEDEN konkretny order. Mechanizm: `_bag_not_stale`/ZOMBIE-01 pod ON liczą realny wiek `picked_up_at` → order odebrany >próg temu, nigdy nie domknięty = ghost (truje carry/R6/C2 — komentarz 603-612). Zgodne z MEMORY „bag_dropped=ghost cid393/1 peak". Skala znikoma + skupiona w 1 kurierze/1 orderze = łapanie patologii, NIE systematyczne gubienie legit-orderów.

**Twin-completeness (engine-wide):** zero un-fixed bliźniaków. `grep fromisoformat` na picked_up_at/delivered_at w żywym silniku = PUSTE (tylko test-opis + eod_drafts backfill tool). Inni konsumenci pól = już poprawni: `dispatch_pipeline.py:3117` i `plan_recheck.py:322` używają `parse_panel_timestamp`. Bug był WYŁĄCZNIE w 4 surowych odczytach stanu w `courier_resolver` — wszystkie 4 (616/642/668/1045) teraz przez `_parse_checkpoint_ts`. (CARRIED_AGE_TZ_FIX w plan_recheck = OSOBNY siblng, inna flaga/pole — poza zakresem tego przyrządu.)

---

## 3. INWARIANTY-TRIPWIRE (lane C)
- **„fresh nie gorszy od frozen" (ON≥OFF w sensie POPRAWNOŚCI):** ON nigdy nie daje age<0 (0/588 ✓); ON nigdy nie odrzuca realnie-świeżego (guard `age<0`). ✓
- **Ten sam zbiór:** kids = off∪on = 54 kurierów w obu. ✓
- **Zero fikcyjnych pozycji:** ON tylko UPGRADE→interp (liczony z realnego pickup+OSRM route) lub uczciwe `no_gps`; NIGDY nie fabrykuje punktu. 958 interp + 1 ratunek = legit. ✓
- **Bezpieczny no-op:** aware(`T`) ts → delta 0. ✓

---

## 4. WERDYKT
**VALIDATED.** Fix poprawny u źródła, kompletny (4/4 bramki + brak engine-twin), deterministyczny, bez kolateralu na poprawnych ts. Wpływ POZYTYWNY i dwojaki: (1) ożywia predykcję pozycji no-GPS (interp 0→958), (2) **usuwa fantomy stałej pozycji** (335 korekt: stop używania dostaw sprzed ~2h jako bieżących). Kontrola bezpieczeństwa (bag-zombie) = 1 realny ghost, nie legit-loss.

**Kolektor SŁUSZNIE disabled:** flaga jest już LIVE ON → OFF nie jest już żywą ścieżką; ponowne odpalenie tylko re-dowodzi ten sam deterministyczny +120min. Cel (dowód PRZED flipem) spełniony; disable po at-182 CLEAN poprawny.

**CAVEAT (proxy vs ground):** `picked_up_at`/`delivered_at` = **button-truth** (klik panelu, nie fizyczny GPS — FUNDAMENT-CAVEAT). ALE werdykt NIE zależy od fizycznej dokładności kliku — zależy tylko od tego, że string jest naiwną Warszawą (udowodnione: 585/588 no-T, offset DOKŁADNIE +120min). Sama poprawność interpretacji TZ = **ground-truth deterministyczny**; jakość pozycji downstream = proxy-certified.

---

## 5. ZNALEZISKA INSTRUMENTU (klasa F/L/H/M — jakość przyrządu, NIE błąd silnika; werdykt zostaje VALIDATED)

**F-01 (P3, semantyka metryki vs wartość) — `checkpoint_tz_shadow.py:128-129,181,188-189.**
GO-kryterium summarize: „interp_on≫0 **+ rescued≫0** + przesunięcia realne". `rescued_from_synth` (synth→real) = **wrong-direction** dla tego fixu — realnie =1 (martwy). Dominująca wartość fixu (335 real→synth KOREKT) NIE jest w ogóle pokazana w headline summarize() — czytający dostałby „rescued:1" = pozornie SŁABY sygnał mimo mocnego fixu. Werdykt at-182 słusznie oparł się na interp_on=958, ale self-summary przyrządu MIS-FRAME'uje własny najmocniejszy dowód. still_open (kod niezmieniony; moot bo disabled). dedup→ K1/E-instrument-misframe.

**L/H-01 (P3, stale-read trap) — `dispatch_state/checkpoint_tz_shadow.jsonl` mtime 27.06 21:06.**
Zamrożony (kolektor disabled), leży w `dispatch_state/` obok ŻYWYCH jsonl, BEZ markera „stale/retired". Ślepy `ls`/konsument freshness pomyli z bieżącym. A4 §8 już to oznaczył (klasa L). dedup→ rodzina „stale-instrument-output-no-TTL" (A4 H: drive_speed_overshoot.txt, bug4_reseq.txt, objm peak .txt).

**M-01 (P3, cicha utrata zapisu, 4. instancja) — `checkpoint_tz_shadow.py:144-145.**
`except Exception: _log.warning("append fail")` połyka błąd zapisu jsonl (jak A4 §8 M-cluster: bundle_calib_shadow.py:524, reassignment_forward_shadow.py:414, carried_first_guard.py). Moot (disabled), ale ten sam wzorzec. dedup→ A4 M-cluster `_append_jsonl`-swallow.

---

## 6. TABELA POKRYCIA

| Obszar | Zbadane? | Jak |
|---|---|---|
| `tools/checkpoint_tz_shadow.py` | ✅ full | Read 1-216 + run-once/summarize logika + GO-kryterium |
| `checkpoint_tz_shadow.jsonl` (386 tików) | ✅ | re-agregacja sum + per-tick interp dist + transition matrix + bag-drop detail |
| `_parse_checkpoint_ts` (cr.py:42-67) | ✅ | OFF-branch vs ON-branch, flaga via `_f4_flag` |
| 4 call-sites (616/642/668/1045) | ✅ | code-trace każdej bramki vs kierunek danych |
| `parse_panel_timestamp` (common:441) | ✅ | naive→Warsaw→UTC potwierdzony |
| flaga efektywna (flags:221 / common:3002 / _f4_flag) | ✅ | flags.json ON nadpisuje env-OFF |
| live `orders_state` 588 checkpointów | ✅ | second-method age OFF/ON, +120min, 94 vs 0 future-dated |
| engine-wide twin-leak | ✅ | grep fromisoformat na pola = czysty; dispatch_pipeline:3117 + plan_recheck:322 poprawne |
| determinizm | ✅ | 2-run pure-function byte-identical + no-op na aware |
| **GAP: odpalenie kolektora na żywo** | ❌ | pisze do dispatch_state (DoD zakaz). Zastąpione frozen-jsonl + second-method (silniejsze: deterministyczny dowód, nie próbka) |
| **GAP: okno DST-boundary** | ❌ | jsonl 26-27.06 = lato UTC+2 stale; zima UTC+1 nietestowane (ale `astimezone` DST-correct z konstrukcji) |
| **GAP: downstream proposal/SLA impact** | ❌ | przyrząd mierzy MECHANIZM (interp+fantom-korekta), nie wynik dispatchu; „pozytywny wpływ" = correctness-mandate (nie trzymaj znanego-złego UTC), nie metryka outcome |
| **GAP: cid393 exact 26.06 12:40 ghost-age** | ❌ | stan z 26.06 12:40 nieodtwarzalny; ghost-status z mechanizmu+MEMORY, nie z re-odczytu age tego ordera (button-truth proxy) |
| **GAP: CARRIED_AGE_TZ_FIX sibling** | ❌ | osobna flaga/instrument (carried_age_tzfix_review) — poza zakresem C12 |

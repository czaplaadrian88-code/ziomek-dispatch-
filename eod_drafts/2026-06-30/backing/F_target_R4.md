# FAZA F — STAN DOCELOWY: rodzina **R4 „Semantyka"** (klasy F · L)

> **⚠️ DRAFT — propozycja kontraktów docelowych + zbieżny plan konsolidacji. ZERO wykonania.**
> Audyt READ-ONLY (sesja tmux 2). Ten dokument definiuje DOKĄD zmierzać, NIE wykonuje. Każda zmiana kodu = osobny ACK + protokół ETAP 0→7. PoC = wydzielony mini-sprint po akceptacji targetu.

**Data:** 2026-06-30 · **HEAD silnik:** `8024705` · **Tryb:** READ-ONLY
**Wejście:** `E_dedup_3_semantics_lifecycle.md` (podklaster R4 SEMANTYKA: S1·S2·S3·L1·L2·L3·L4) · `B08_F_field_semantics` · `B14_L_vocab_tz` · `B21_F_eta_pickup` · `F_target_R1.md` (granice floor/route/flag) · `F_target_R2.md` (granice split-layer/VETO) · `ZIOMEK_COHERENCE_AUDIT_DESIGN.md` §4 (kontrakty 1-8).
**Wszystkie `plik:linia` zweryfikowane ŚWIEŻYM grepem DZIŚ** (HEAD `8024705`; linie dryfują — re-grep przed cytatem jako pewnik). Zweryfikowane punkty kotwiczne na końcu (§5).

---

## 0. TEZA RODZINY R4 — „mapa kłamie o terytorium"

R4 to rodzina **dryfu semantyki**: **NAZWA albo PREZENTACJA wartości rozjeżdża się z jej ZNACZENIEM albo DECYZJĄ**. To NIE „reguła w N kopiach" (R1), NIE „reguła w złej warstwie" (R2), NIE „przyrząd kłamie o wyniku" (R3) — to pytanie **czy to, co pole/token NAZYWA lub POKAZUJE == to, czym ono DECYZYJNIE JEST**. Dwie osie tego samego defektu:

- **Oś F — `display ≠ decision`** (kontrakt §4.6 wprost): wartość PREZENTOWANA rozjeżdża się z wartością DECYZYJNĄ. Manifest: jedno pole = dwie role (decyzja+display), albo pola-bliźniacze pisane asymetrycznie, albo derywat-display zawracający DO decyzji. Kłamstwo: „co widzę" ≠ „co policzyłem".
- **Oś L — `name ≠ behavior`** (semantyczny analog §4.6 + §4.2 + §4.1): NAZWA/etykieta rozjeżdża się z faktycznym zachowaniem. Manifest: HARD-nazwane-SOFT-egzekwowane, jeden token 4 znaczenia, „warsaw"-nazwane-płynące-UTC, `elastic` vs `elastyk`, niejawne jednostki. Kłamstwo: „jak się nazywa" ≠ „co robi".

**Dlaczego rodzina jest głównie P2/P3-latentna (NIE bagatela):** dziś maskowana — negacją (`!="czasowka"` przykrywa `elastic`/`elastyk`), flagą-ON (regeocode-sync), normalizacją-u-granicy (naive→UTC no-op), single-role-użyciem. **Dług = sama DWUZNACZNOŚĆ uzbraja minę** dla KAŻDEJ przyszłej zmiany dotykającej pola/tokenu: pierwszy positive-matcher na `=="elastic"`, pierwszy flip flagi, pierwsza wartość omijająca granicę TZ, pierwszy nowy konsument. Stan docelowy = usunąć dwuznaczność U ŹRÓDŁA, żeby mina nie miała się jak uzbroić. To jest dokładnie „buduj na lata, nie łataj krawędzi" zastosowane do SŁOWNICTWA i SEMANTYKI PÓL.

**8 rootów rodziny R4** (podklaster R4 z `E_dedup_3` + persisted-root `name-vs-behavior-hard-misnomers` z finalnej listy):

| Root | Oś | Sev | Klasy | Werdykt | Objaw semantyczny (1 zdanie) |
|---|---|---|---|---|---|
| **S1 `eta-pickup-one-field-two-roles`** | F | P2 | F·(J·M most) | CONFIRMED, źródło | `eta_pickup_utc` = JEDNO pole o dwóch rolach: twarda zmienna decyzyjna (kara+HARD-reject+committed) ∧ baza napisu — brak separacji display/decision, „zmiana napisu" = regres feasibility. |
| **S2 `coupled-location-fields-async-write`** | F | P2 | F·(M most) | CONFIRMED, źródło | `delivery_coords`(pin) ∧ `delivery_address`(tekst) = ta sama lokalizacja w 2 formach; writer pisze JEDNO bez drugiego = split-brain HARD-geom↔SOFT-district; + pin REUŻYTY jako pozycja kuriera. |
| **S3 `uwagi-field-boundary-loss`** | F | P2 | F | CONFIRMED, źródło | `uwagi`+derywaty persystowane w głównej ścieżce, DROPOWANE w fallbacku corrupt-ts (wzorzec #1 zastosowany do POLA). |
| **NB `name-vs-behavior-hard-misnomers`** | L | P2 | L·I·B | PLAUSIBLE, źródło | Reguły HARD-nazwane są SOFT/metric-only (`PICKUP_SPAN_HARD`=próg kary nie bramka; R-RETURN „VETO" nigdy nie przerywa; `LATE_PICKUP_HARD_GATE`). Nazwa kłamie o warstwie. |
| **L1 `naive-datetime-tz-convention-split`** | L | P2 | L·(I most) | CONFIRMED(nawrót), źródło | DWIE przeciwne konwencje naive-datetime: parse=Warsaw / math-HARD-bramka=UTC → Warsaw-naive omijający granicę = **+2h w bramce**. 1 nawrót dowiedziony (checkpoint_tz). |
| **L2 `tier-token-overload`** | L | P2 | L | CONFIRMED, źródło | Token `tier` = 4 rozłączne znaczenia (klasa/eskalacja/solver-dim/GPS) bez glosariusza; „tier-3" eskalacja ≠ „tier=slow" klasa w sąsiednim kodzie. |
| **L3 `shift-start-midnight-anchor`** | L | P2 | L·(R1-B most) | CONFIRMED, źródło | `_shift_start_dt` bez obsługi północy (zawsze DZIŚ), gdy bliźniak `_shift_end_dt` MA `24:00→+1` — date-anchoring asymetryczny. |
| **L4 `lexical-naming-units-rot`** | L | P3 | L·N | CONFIRMED, źródło | Rozsyp: enum `elastic`/`elastyk` PL/EN; jednostka w prefiksie (`eta_pickup_min`≠`_utc`); `WARSAW` 6 nazw; dual-60min. |

**Kontrakt §4 wiodący dla CAŁEJ rodziny = §4.6 „brak-dryfu-semantyki (display≠decision)"** — i jego semantyczny analog dla nazw (name≠behavior). Kontrakty wspierające: **§4.1** (jedno źródło → JEDNA kanoniczna nazwa/reprezentacja per pojęcie), **§4.2** (kontrakt warstw → nazwa MUSI deklarować i pasować do warstwy egzekucji), **§4.8** (koherencja → ZERO dwóch przeciwnych konwencji dla jednego prymitywu — TZ), **§4.3** (parytet bliźniaków → 2 parsery TZ / start↔end zgodne), **§4.7** (kompletność cyklu życia → pole przeżywa KAŻDĄ ścieżkę persist).

**✅ JEDYNY POZYTYW (szablon docelowy):** `state_machine.py:61-95` `_sanity_*` — runtime-asercja **„ISO `strftime('%H:%M')` MUSI == raw `czas_kuriera_hhmm`"** (sygnał korupcji parsera). To **JEDYNY w całym systemie runtime-strażnik spójności pary semantycznej** (decision-ISO ↔ display-HH:MM). Cała rodzina R4 ma JEDEN taki strażnik — stan docelowy = **zreplikować ten wzorzec na każdą parę** (decision↔display, name↔layer, naive↔aware).

---

## 1. KONTRAKTY SEMANTYCZNE R4 (cross-cutting — produkt §4.6 + §4.1 + §4.2)

Stan docelowy zaczyna się od **REJESTRU SEMANTYKI** (analog macierzy reguła→warstwa z R2): dla KAŻDEGO przeciążonego pola/tokenu deklaracja kanoniczna = `{nazwa, rola, jednostka, TZ, warstwa}`. Inwarianty runtime egzekwują 4 zasady.

### 1.1 Cztery zasady docelowe

| Zasada | Treść | Kontrakt §4 | Root(y) |
|---|---|---|---|
| **SEM-1 display≠decision rozdzielone** | Wartość decyzyjna (surowa, jedyne wejście bramek/kar/committed) ODDZIELONA od derywatu-display (floored/formatted). Derywat NIGDY nie zawraca do decyzji. | §4.6 | S1, S2 |
| **SEM-2 jedna nazwa per pojęcie** | Każde pojęcie = JEDNA kanoniczna nazwa; każda nazwa = JEDNO znaczenie. Jednostka WYMUSZONA sufiksem (`_min`/`_utc`/`_hhmm`/`_timestamp`); enum w JEDNYM języku. | §4.1 | L2, L4 |
| **SEM-3 nazwa==warstwa==zachowanie** | Nazwa deklaruje warstwę egzekucji; HARD-nazwane ⇒ HARD-egzekwowane (L5) ALBO przemianowane na realną warstwę. | §4.2 | NB |
| **SEM-4 jedna konwencja per prymityw** | Jeden prymityw (naive-datetime) = JEDNA konwencja interpretacji od granicy; ZERO dwóch przeciwnych kotwic. | §4.8 §4.3 | L1, L3 |

### 1.2 Inwarianty runtime docelowe (szablon = `_sanity` z `state_machine:61-95`)

- **INV-SEM-1 (display-nie-karmi-decyzji, S1):** żadna wartość `*_display`/`*_hhmm` nie jest wejściem do `extension_penalty`/`final_score`/`feasibility_verdict`/`time_arg`. Test: graf przepływu `eta_pickup_hhmm` → 0 krawędzi do warstwy decyzyjnej; `eta_pickup_decision` = jedyne wejście `:5174`/`:5199`/`:5610`/committed.
- **INV-SEM-2 (para-spójna, S2):** writer dotykający `delivery_coords` ALBO `delivery_address` zapisuje PARĘ atomowo (+`city`) albo jawnie deklaruje N-D z logiem. Test: każdy upsert z 1 z 2 pól bez drugiego = naruszenie (poza guarded-terminal `:822-826`); detektor `address_mismatch.py:225` przechodzi z 0 rozjazdów po edycji.
- **INV-SEM-3 (nazwa==warstwa, NB):** symbol z `HARD`/`GATE`/`VETO` w nazwie egzekwuje w L5 (`check_feasibility_v2` reject) ALBO nie nosi tej nazwy. Test: grep symboli `*_HARD_*`/`*VETO*` → każdy ma reject-ścieżkę w L5 lub jest przemianowany.
- **INV-SEM-4 (no-naive-w-math, L1):** żaden `datetime` wchodzący do warstwy math/HARD-bramki (`feasibility_v2`/`route_simulator_v2`) nie jest naive. Test: `assert dt.tzinfo is not None` na wejściu math-layer (zamiast defensywnego `if None: →UTC` który MASKUJE minę); jeden typ „aware-UTC od granicy".
- **INV-SEM-5 (pole-przeżywa-persist, S3):** derywaty pola liczone w JEDNYM miejscu, które KAŻDA ścieżka persist wywołuje (happy ∧ fallback). Test: `state_machine` corrupt-ts fallback niesie te same klucze co happy (`uwagi`+`uwagi_pickup_parsed`+`delivery_deadline_uwagi`).

---

## 2. STAN DOCELOWY PER ROOT (twardy kontrakt + inwariant)

### — OŚ F (display ≠ decision) —

### S1 — `eta-pickup-one-field-two-roles` (P2, CONFIRMED, źródło) — klasa F

**Defekt (zweryfikowany świeżo):**
- `eta_pickup_utc` jest **twardą zmienną decyzyjną na 2 warstwach** + bazą display: (a) `dispatch_pipeline.py:5174` `extension_penalty(_eta, _pra)` → `:5610` `if v324a_extension_hard_reject and verdict=="MAYBE": verdict="NO"` (HARD-reject feasibility, ekstensja >60min via `V324_HARD_REJECT_EXTENSION_OVER_MIN`); (b) `:5199` `final_score += v324a_extension_penalty` (kara scoringu); (c) cross-repo `Ops13Console.tsx:835` `time_arg = best.eta_pickup_hhmm` → `assign.py:42 --time` → committed `czas_kuriera` (R27-frozen). „Display-only" = OBALONE grepem.
- **Dwie komputacje rozjeżdżalne (F1-C):** main-loop lokalna `eta_pickup_utc` (`:4057`=`plan.pickup_at−DWELL` / `:4061`=`now+drive` / `:4077`=soon_free) karmi `extension`/score; **post-loop NADPIS** `c.metrics["eta_pickup_utc"]` dla no_gps (`:5862`) / pre_shift (`:5877`=`shift_eta`) karmi display+`time_arg`+serializer. Dla pre_shift/no_gps **wartość scorująca ≠ serializowana/committed**. Komentarz autora `:5163` zna problem („clamp aktywny w post-loop override").
- **Display-floor overlay rozsiany (F-3):** „pokaż odbiór ≥ plan/committed/ready" re-implementowany w **6 powierzchniach × ≥7 flag** bez wspólnego importu (telegram ×2 / apka FROZEN_PICKUP_ETA ×3 / konsola PIN_AGREED+CLAMP / engine PRE_SHIFT_DEPARTURE_CLAMP / shadow PICKUP_DEBIAS) → display floored, decision-value surowy.
- **Prowenancja zgubiona:** `eta_source` (real-route vs fikcja BIALYSTOK_CENTER) liczony w 6 site, **0 w ledgerze** (oracle C18: 0/4-dni) → z `shadow_decisions` nie wiadomo czy zwycięska ETA = fikcja.

**KONTRAKT DOCELOWY (SEM-1):**
> `eta_pickup` rozdzielone na `eta_pickup_decision` (surowy, JEDYNE wejście `extension_penalty`+`>60min-reject`+`target_pickup_at`/`time_arg`) i `eta_pickup_display` (floored/formatted derywat, NIGDY z powrotem do decyzji). JEDNA komputacja (nie main-loop ∥ post-loop nadpis). HARD-reject >60min przeniesiony do `check_feasibility_v2` (warstwa HARD). `eta_source` serializowany.

**Forma docelowa (additive, wzorzec #8 — NOWE pole obok, nie mutacja in-place):**
1. **Pole `eta_pickup_decision`** = surowy wynik komputacji (jedna, nie dwie); konsumenci decyzyjni (`:5174`/`:5199`/`:5610`/committed) czytają TYLKO je. Post-loop nadpis no_gps/pre_shift staje się jawną komputacją `eta_pickup_decision` (nie nadpisem metryki po fakcie) — usuwa skew F1-C.
2. **Pole `eta_pickup_display`** = derywat (floor-overlay + `_eta_hhmm_warsaw`). Floor-overlay = **JEDEN wspólny helper** importowany przez 6 powierzchni (NIE 6×7 flag) — cross-ref **R1-B** (`earliest-pickup-floor-no-chokepoint`: floor=mechanizm jednego-źródła; tu tylko jego derywat-display).
3. **HARD-reject >60min → L5:** `check_feasibility_v2` zwraca `NO` na ekstensję >60min (warstwa HARD), nie verdict-override w L6 — cross-ref **R2** (`hard-feasibility-split-layer`, INV-LAYER-1).
4. **Serializuj `eta_source`** (real-route/plan/fiction) — cross-ref **R3** (`serializer-allowlist-metrics-vanish` + `objm-shadow-canary`): pole znika dziś przez allowlist.

**INWARIANT:** INV-SEM-1. *Metryka entropii:* `semantic-role-overload`(eta_pickup: 2→1 rola/pole); `display-feeds-decision`(hhmm→committed: 1→0); `compute-skew`(eta_pickup 2 komputacje→1).

**ZALEŻNOŚĆ:** floor-overlay-unify GATE na **R1-B** (jeden floor-source); serializacja `eta_source` GATE na **R3** (allowlist). Sam rozdział decision/display = niezależny (additive).

**Luka weryfikacji (read-only):** materialność skew F1-C (czy pre_shift plan-based `:4057` ≡ post-loop `:5877` dla realnego kandydata) NIE policzona runtime — Faza C oracle (replay 1 pre_shift case, diff scoring-eta vs serialized-eta). Deklaruję strukturalnie.

---

### S2 — `coupled-location-fields-async-write` (P2, CONFIRMED, źródło) — klasa F

**Defekt (zweryfikowany świeżo):**
- `delivery_coords`(pin) i `delivery_address`(tekst) = ta sama lokalizacja, 2 formy; rozdział konsumentów = oś split-brain: tekst→`drop_zone_from_address` (SOFT district/wave/bundling: `same_restaurant_grouper.py:84`, `dispatch_pipeline.py:940`, `insertion_anchor.py:127`) vs pin→geometria HARD/SOFT (`feasibility_v2.py:499` R1, `:518` R5, `plan_manager` kanon).
- **Writer asymetryczny:** `gastro_edit.py:154` pisze `delivery_coords` ZAWSZE, `:157-158` `delivery_address` TYLKO gdy `ENABLE_REGEOCODE_SYNC_TEXT`. Flaga **flags.json=true LIVE** (fix 484269 „Można"≠„Mroźna" 4,26km), ALE **const-default OFF** + **POZA ETAP4/fingerprint** (grep `common.py` REGEOCODE = ∅ potwierdzony) → usunięcie klucza z flags.json = **cichy rewert asymetrii** (pin bez tekstu wraca). `state_machine.py:822-826` COURIER_DELIVERED: tekst-zawsze/pin-warunkowo (guarded-terminal, OK).
- **Cross-field reuse (F2-C):** `courier_resolver.py:740-742` `cs.pos = tuple(order["delivery_coords"]); pos_source="last_picked_up_delivery"` — pole „gdzie dowieźć" REUŻYTE jako **pozycja kuriera** dla km/ETA/feasibility KOLEJNEGO ordera. Zatruty pin (regeocode-asym / sentinel `(0,0)`) propaguje w pozycję.

**KONTRAKT DOCELOWY (SEM-1/§4.6 + §4.3):**
> JEDEN writer-kontrakt: „pisz parę `(coords, address, city)` atomowo, albo JAWNIE zadeklaruj N-D z logiem". `ENABLE_REGEOCODE_SYNC_TEXT` → ETAP4+fingerprint → potem retire (zawsze-sync, asymetria niemożliwa). Pozycja-z-delivery przez `_valid`-guard.

**Forma docelowa:**
1. **Atomowy pair-writer** `(coords, address, city)` — jeden helper, każdy writer go woła; brak ścieżki piszącej 1 z 2. Detektor `address_mismatch.py:225` (`ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW`=true, review at-198 01.07) = oracle PRZED retire flagi.
2. **`ENABLE_REGEOCODE_SYNC_TEXT` → ETAP4 + fingerprint** (domknięcie leaku D — cross-ref **R1-D** `flag-state-3-layer`), potem **retire na zawsze-sync** (usunięcie flagi, nie zostawianie const-OFF miny).
3. **Pozycja-z-delivery przez `_valid(coords)`** (`coords_in_bialystok_bbox`) — cross-ref **R5/F1** (`coord-sentinel-no-ingest-chokepoint`, K5): zatrucie `(0,0)` w pozycję = TAM root, tu tylko konsument.

**INWARIANT:** INV-SEM-2. *Metryka entropii:* `coupled-field-async-write`(coords-bez-address: writerzy-asym 1→0); `flag-leak`(REGEOCODE poza fingerprint: 1→0); `semantic-role-overload`(delivery_coords destination+position: most do R5).

**ZALEŻNOŚĆ:** retire flagi GATE na oracle at-198 (01.07) + ETAP4-migracja (R1-D). Pair-writer = niezależny.

**Luka weryfikacji:** materialność split-brain (ile orderów ma HARD-pin≠SOFT-tekst po edycji) = shadow `address_mismatch` mierzy, review 01.07.

---

### S3 — `uwagi-field-boundary-loss` (P2, CONFIRMED, źródło) — klasa F

**Defekt (zweryfikowany świeżo):**
- `uwagi` (free-text) niesie 2 osadzone payloady decyzyjne: pickup-adres firmowego konta → coords; deadline czasówki. Persystowane w GŁÓWNEJ ścieżce NEW_ORDER (`state_machine.py:533-538`: `uwagi`+`uwagi_pickup_parsed`+`delivery_deadline_uwagi` — Lekcja #80 naprawiona), ale **DROPOWANE w fallbacku `CorruptedTimestampError`** (`:515`/`:629` raise; ścieżka fallback bez tych kluczy) — **wzorzec #1 (fix w 1 z N ścieżek) zastosowany do POLA**.
- + parse pickup-z-uwagi TYLKO przy NEW_ORDER (`panel_watcher.py:1210`) → **#18 temporalna luka** (edycja uwagi nie re-parsuje).

**KONTRAKT DOCELOWY (SEM-5/§4.7):**
> Derywaty (`uwagi_pickup_parsed`/`delivery_deadline_uwagi`) liczone w JEDNYM miejscu, które KAŻDA ścieżka persist (happy ∧ fallback) wywołuje. Sweep utrwalonego stanu zamiast hooka-przy-tworzeniu (#18).

**Forma docelowa:**
1. **Jeden derive-point** wołany z happy(`:533`) ∧ fallback(corrupt-ts) — fallback niesie te same klucze.
2. **Re-parse przy edycji uwagi** (nie tylko NEW_ORDER) — sweep utrwalonego stanu (wzór address_mismatch-sweep).

**INWARIANT:** INV-SEM-5. *Metryka entropii:* `field-boundary-loss`(uwagi+derywaty w fallbacku: 1→0); `temporal-reachability`(parse tylko-at-create: 1→0).

**Impact dziś NISKI** (merge-upsert `state_machine:815` zachowuje istniejące; `delivery_deadline_uwagi` = shadow bez konsumenta decyzyjnego). **Latentny P2** gdy `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW` dostanie konsumenta decyzyjnego — wtedy fallback-drop = realna utrata deadline. Domknięcie PROAKTYWNE (tanie, zgodne z „domykaj zepsute, nie pytaj o wartość").

---

### — OŚ L (name ≠ behavior) —

### NB — `name-vs-behavior-hard-misnomers` (P2, PLAUSIBLE, źródło) — klasy L·I·B

**Defekt (zweryfikowany świeżo):**
- **`PICKUP_SPAN_HARD_*` = NIE bramka:** `feasibility_v2.py:628` komentarz wprost „**PICKUP_SPAN_HARD_* to próg kary, nie bramka feasibility**"; `:638-639` używa `PICKUP_SPAN_HARD_BUNDLE3_MIN`/`_BUNDLE2_MIN` jako progu SOFT-kary. Nazwa `HARD` ⇒ czytelnik/audyt zakłada reject; realnie SOFT.
- **R-RETURN „VETO" = nigdy nie przerywa:** `feasibility_v2.py:904-905` „instrumentacja **NIGDY nie przerywa feasibility**"; `ENABLE_R_RETURN_TO_RESTAURANT_VETO` (`:905-906`) = metric-only w L5, realny zakaz dopiero w L9 `plan_recheck` (`ENABLE_NO_RETURN_TO_DEPARTED_PICKUP`). Nazwa „VETO" myli.
- **`LATE_PICKUP_HARD_GATE`:** `common.py:2822 ENABLE_LATE_PICKUP_HARD_GATE` (ON od 2026-05-31) — per MEMORY = „propozycja przedłużenia DO RESTAURACJI, NIE ukryta kara" (`common.py:261`); nazwa `HARD_GATE` sugeruje twardy reject.
- **`DEPRECATE_LEGACY_HARD_GATES`** (`feasibility_v2.py:1123`) = „stała = False, **nigdy nie flipnięta**" — nazwa obiecuje deprecation który się nie wydarzył (cross-ref **R6c-1** dead-code: TAM usunięcie).

**KONTRAKT DOCELOWY (SEM-3/§4.2):**
> Symbol z `HARD`/`GATE`/`VETO` w nazwie ALBO egzekwuje w warstwie HARD (L5 `check_feasibility_v2` reject), ALBO jest PRZEMIANOWANY na realną warstwę/rolę (`*_PENALTY`/`*_SOFT_*`/`*_METRIC`). Nazwa = warstwa = zachowanie.

**Forma docelowa (DWIE drogi — decyzja koherencji, ACK):**
1. **Przemianować** (preferowane gdy zachowanie SOFT jest świadome/poprawne): `PICKUP_SPAN_HARD_*`→`PICKUP_SPAN_PENALTY_*`; R-RETURN „VETO"→`R_RETURN_METRIC`/`_SHADOW`; `LATE_PICKUP_HARD_GATE`→nazwa oddająca „propozycja-przedłużenia". Czysta semantyka, ZERO zmiany zachowania, golden ON==OFF.
2. **Przenieść do HARD** (gdy zachowanie POWINNO być HARD): re-layer do L5 — to **NIE robota R4** (to R2 `hard-feasibility-split-layer` C-adj-2). R4 owns NAZWĘ; re-layering rozstrzyga **R2/R7 + Adrian** (czy „VETO" ma realnie wetować).

**INWARIANT:** INV-SEM-3. *Metryka entropii:* `name-layer-mismatch`(HARD-nazwane-SOFT: 3-4→0).

**ZALEŻNOŚĆ:** rozstrzygnięcie rename-vs-relayer per-symbol = koherencja, **cross-ref R7** + ACK Adriana (czy świadoma inwersja „VETO=instrumentacja" zostaje, czy ma realnie wetować). PLAUSIBLE bo wymaga decyzji intencji, nie tylko grepu.

---

### L1 — `naive-datetime-tz-convention-split` (P2, CONFIRMED-nawrót, źródło) — klasa L ★ headline-L

**Defekt (zweryfikowany świeżo):**
- DWIE przeciwne konwencje naive-datetime: **Camp B (parse/boundary = Warsaw, POPRAWNY)** — `common.py:467`, `panel_client.py:566`, `state_machine.py:779` (`picked_up_at`→Warsaw); **Camp A (math/HARD-bramka = UTC, defensywny)** — `feasibility_v2.py:127`/`:442`/**`:749`** (shift-start HARD pre-shift gate `replace(tzinfo=timezone.utc) if None`), `route_simulator_v2.py` ~20 site, `plan_recheck._parse_dt:284`.
- **Samodokumentowana mina:** `plan_recheck.py:288` docstring „NIE używać dla naiwnych Warsaw… **interpretacja jako UTC = błąd +2h**"; `:1071` „zachowanie sprzed fixa (błąd +2h)". DWA parsery `picked_up_at` przeciwne: `state_machine:779`(Warsaw, OK) vs `plan_recheck._parse_dt`(UTC, +2h).
- **DOWÓD NAWROTU (oracle C12 VALIDATED):** checkpoint_tz — 4 miejsca `courier_resolver` parsowały GPS-checkpoint Warsaw-naive jako UTC; `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` LIVE; oracle: `age_ON − age_OFF = +120.0min` (588 checkpointów). To LN naprawione w 1 module — pozostałe powierzchnie otwarte (klasa wraca w nowym module).
- **Cross-repo (LN-x):** `fleet_state.py:100 _iso`(naive→Warsaw) vs `:219 _parse_ts`(naive→UTC) w TYM SAMYM pliku, na polach `delivered_at`/`picked_up_at` opatrzonych „(Warsaw, naive)".

**KONTRAKT DOCELOWY (SEM-4/§4.8 + §4.3):**
> JEDEN typ/kontrakt: „wszystkie czasy aware-UTC OD GRANICY" + `assert dt.tzinfo is not None` w math-layer (zamiast defensywnego `if None: →UTC` który MASKUJE minę). Dwa parsery tego samego pola → JEDEN. Replikować inwariant `state_machine:61-95` (ISO≡HH:MM) na pary TZ.

**Forma docelowa:**
1. **Typ-na-granicy:** parse-layer zwraca aware-UTC ZAWSZE; math-layer dostaje aware. Defensywne `if tzinfo is None: →UTC` (`feasibility_v2:749` etc.) zamienione na `assert tzinfo is not None` (fail-loud = INV-SEM-4) — bo dziś no-op tylko PRZYPADKIEM (granica normalizuje), a maskuje +2h gdy wartość omija granicę.
2. **`_iso`/`_parse_ts` ujednolicić** (`fleet_state.py`) — jeden helper, jedna kotwica; pola „Warsaw naive" jawnie konwertowane na granicy konsoli.
3. **Nazewniczo (most L4):** `czas_kuriera_warsaw` płynący jako UTC = przemianować lub udokumentować inwariantem.

**INWARIANT:** INV-SEM-4. *Metryka entropii:* `tz-convention-count`(naive-datetime: 2→1); `twin-divergence`(2 parsery picked_up_at → 1); `dead-guard`(defensywny no-op maskujący → fail-loud assert).

**Luka weryfikacji:** czy DZIŚ realna wartość Warsaw-naive omija granicę (poza checkpoint) = NIE zmierzone (read-only); Faza C oracle (`ziomek_time_route_monitor` HH:MM konsola vs engine na `picked_up_at`, rozjazd ±120min). Latentne + 1 CONFIRMED nawrót.

---

### L2 — `tier-token-overload` (P2, CONFIRMED, źródło) — klasa L

**Defekt (zweryfikowany świeżo):**
- Token `tier` = **4 rozłączne znaczenia** bez glosariusza: (1) KLASA kuriera (`feasibility_v2.py:355 _tcap = 4 if tier=="gold" else 3`; `:1090 courier_tier=="gold"`), (2) POZIOM ESKALACJI (`dispatch_pipeline.py:737/739 _esc_tier∈{2,3}`, serializowany `:743 best_effort_objm_esc_tier`), (3) WYMIAR SOLVERA (`route_simulator_v2.py:1260` tier-1/tier-2), (4) TIER GPS (`common.py:600/611`).
- **Kolizja krytyczna:** „tier-3 cap=40" (`common.py:2657` komentarz „RZADKIE dni niedoboru, tier-3") = oś-2 **eskalacja**; `feasibility_v2:355 tier=="gold"`/„slow" = oś-1 **klasa**. **DWA różne „tier-3" w sąsiednim kodzie.** `_esc_tier=3` serializowany OBOK courier-class `tier` → konsument joinujący po „tier" myli eskalację z klasą. MEMORY/CLAUDE.md ostrzega „tier=DWIE rzeczy" — realnie CZTERY.

**KONTRAKT DOCELOWY (SEM-2/§4.1):**
> `tier` → 4 rozłączne nazwy: `courier_class` (gold/std/slow) · `escalation_level` (1/2/3) · `solver_dim` (tier-1/tier-2) · `gps_tier`. Glosariusz single-source. Jeden token = jedno znaczenie.

**Forma docelowa:** mechaniczny rename per-oś (golden ON==OFF, zero zmiany zachowania); serializowane pole `best_effort_objm_esc_tier` → `escalation_level` (rozłączne od `courier_class`).

**INWARIANT:** INV-SEM-2/SEM-3 analog (nazwa=znaczenie). *Metryka entropii:* `vocab-overload`(tier: 4 znaczenia/token → 1); `serializer-ambiguity`(esc_tier obok class-tier → rozłączne).

**Najwyższy zwrot z L (czysto mechaniczny, disarm miny join-by-tier), niskie ryzyko.**

---

### L3 — `shift-start-midnight-anchor` (P2, CONFIRMED, źródło) — klasa L

**Defekt (zweryfikowany świeżo):**
- `courier_resolver.py:1252 _shift_start_dt` + `:1240 _minutes_to_pre_shift` używają `now.replace(hour,minute)` = ZAWSZE dzisiejsza doba, BEZ północy. **Asymetria vs bliźniak `_shift_end_dt:1269`** który MA `:1278 if end_str=="24:00": +timedelta(days=1)` + komentarz o wczoraj.
- Zmiana nocna (start 22:00/23:00) odczytana po północy (now=00:30) → DZIŚ 23:00 = ~22h w przyszłości → fałszywy `pre_shift` → błędny clamp/`PRE_SHIFT_TOO_EARLY` (`feasibility_v2:751-756`). Realne (grafik pt/sb do 24:00, GRF-02).

**KONTRAKT DOCELOWY (SEM-4/§4.3):**
> `_shift_start_dt` symetryczny do `_shift_end_dt` (obsługa przełomu północy). Bliźniaki start/end = JEDNA konwencja date-anchoring.

**Forma docelowa:** dodać obsługę północy do `_shift_start_dt`/`_minutes_to_pre_shift` (mirror `:1278`). Golden: zmiana 22:00-now-00:30 → start=wczoraj 22:00.

**INWARIANT:** INV-SEM-4 (date-anchoring spójny). *Metryka entropii:* `twin-divergence`(start↔end anchoring: 1→0).

**ZALEŻNOŚĆ:** DISTINCT od floor (cross-ref **R1-B** `earliest-pickup-floor`: to NIE „brak floor", to błędne HH:MM→datetime); ale FIX wspiera floor (poprawny `shift_start` = poprawny floor). Ruszać świadomie razem z R1-B faza floor (L0).

---

### L4 — `lexical-naming-units-rot` (P3, CONFIRMED, źródło) — klasy L·N

**Defekt (zweryfikowany świeżo):**
- **enum `order_type` PL/EN:** prod pisze `"elastic"` (`panel_client.py:692`); positive-matcher `tools/czasowka_uwagi_oracle.py:153 =="elastic"`; fixtury+`test_czasowka_dispatchable_fleet_fix` używają `"elastyk"` (PL). Maskowane live negacją (`common.py:3500 !="czasowka"`), ale każdy positive-matcher = cichy miscount.
- **jednostka w prefiksie:** `eta_pickup_min`(min-od-teraz) vs `_utc`(absolut) vs `_hhmm`(display) — ten sam prefiks; `czas_odbioru`(int min) vs `_timestamp`(datetime); `pickup_at`-rodzina ≥7 nazw bez sufiksu-TZ.
- **`WARSAW` const = 6 nazw** (`WARSAW` 101× / `_WARSAW_TZ` 11× / `WARSAW_TZ` 9× / `_WARSAW` 8× / `WAW` 6× / `_WAW` 2×) — to samo pojęcie, audit-friction.
- **dual-60min:** `auto_koord.py:32 CZASOWKA_THRESHOLD_MIN=60` vs `common.py:430 EARLY_BIRD_THRESHOLD_MIN=60` (ta sama liczba, różne reguły; early-bird przekwalifikowane lekcja #196).

**KONTRAKT DOCELOWY (SEM-2/§4.1):**
> Enum `order_type` w JEDNYM języku (kanon EN lub PL, spójny prod∧fixtury∧oracle). WYMUSZONA konwencja sufiksu jednostki (`_min`/`_utc`/`_hhmm`/`_timestamp`). `WARSAW` = 1 nazwa. dual-60 = 2 jawnie-rozłączne nazwy lub 1 stała.

**Forma docelowa (mechaniczny cleanup, niskie ryzyko, wysoka redukcja audit-friction):**
1. `order_type` enum ujednolicić (jeden język) — domknąć positive-matcher `oracle:153` (most do N-class miscount).
2. Sufiks jednostki jako konwencja egzekwowana (linter/test nazewniczy); `pickup_at`-rodzina ≥7 nazw → sufiksy TZ.
3. `WARSAW` → 1 import (kosmetyka, ułatwia grep/audyt).

**INWARIANT:** SEM-2. *Metryka entropii:* `vocab-copy-count`(WARSAW 6→1, order_type 2-spelling→1, 60min 2-use→jawne); `implicit-unit`(prefix-ambiguous → sufiks-enforced).

**P3 — robić PRZY okazji dotykania danego modułu** (nie osobny sprint), ALE `order_type` enum przed jakimkolwiek nowym positive-matcherem (disarm miny).

---

## 3. ZBIEŻNY PLAN KONSOLIDACJI (zależnościowo; każdy krok REDUKUJE entropię; bramka „ZERO NOWYCH KOPII/NAZW")

> Zasada anty-entropii: konsoliduj-nie-dodawaj; **bramka „zero nowych kopii/nazw/ról"** na każdym kroku; każdy krok ściśle redukuje ≥1 metrykę semantyczną. Każdy krok dotykający kodu = osobny ACK + ETAP 0→7 (audyt READ-ONLY). Kolejność: tanie-mechaniczne-disarm NAJPIERW (vocab), konwencje, potem F-rozdziały (decyzyjne, ACK).

```
   S0 (rejestr semantyki + szkielet INV-SEM 1-5 jako testy-czerwone)
   [doc, read-only]
        │
        ├─ TANIE/MECHANICZNE (golden ON==OFF, disarm min) ─────────────┐
        │   S1v L2 tier→4 nazwy · S2v L4 enum+sufiks+WARSAW            │
        │                                                              ├─→ S6 (INV-SEM suite zielony,
        ├─ KONWENCJE (twin-symmetry, fail-loud) ───────────────────────┤      runtime-tripwiry = strażnicy
        │   S3c L1 assert-no-naive+1-parser · S4c L3 midnight-symmetry  │      nawrotu PRZED każdym F-flipem)
        │                                                              │
        ├─ NAZWA==WARSTWA (koherencja, ACK) ───────────────────────────┤
        │   S5n NB rename-vs-relayer (R2/R7 + Adrian)                   │
        │                                                              │
        └─ F-ROZDZIAŁY (decyzyjne, ACK, additive) ─────────────────────┘
            F1 S1 eta_pickup decision/display · F2 S2 pair-writer · F3 S3 uwagi derive-point
            (GATE: S1 floor-overlay→R1-B; S1 eta_source→R3; S2 retire-flag→R1-D+oracle 01.07)
```

| # | Krok | Root | Co redukuje (entropia) | Zależy od | Bramka „zero nowych" | Ryzyko/ACK |
|---|---|---|---|---|---|---|
| **S0** | Zbuduj REJESTR SEMANTYKI (`{nazwa,rola,jednostka,TZ,warstwa}` per przeciążone pole/token) + szkielet INV-SEM-1..5 jako testy-czerwone | wszystkie | czyni `semantic-role-overload`/`name-layer-mismatch`/`tz-convention-count` MIERZALNYMI | — | doc-only | read-only, brak ACK |
| **S1v** | `tier` → `{courier_class, escalation_level, solver_dim, gps_tier}` + glosariusz; serializ. `esc_tier`→`escalation_level` | L2 | `vocab-overload`(tier 4→1/oś); `serializer-ambiguity` | S0 | brak nowej osi „tier" | niskie; golden ON==OFF; protokół |
| **S2v** | enum `order_type` 1-język + sufiks-jednostki konwencja + `WARSAW` 6→1 + dual-60 jawne | L4 | `vocab-copy-count`; `implicit-unit`; disarm positive-matcher | S0 | brak nowej nazwy/spellingu | niskie; protokół (order_type przed nowym matcherem) |
| **S3c** | L1: `assert tzinfo is not None` w math-layer (zamiast `if None:→UTC`) + ujednolić `_iso`/`_parse_ts` + 1 parser `picked_up_at` | L1 | `tz-convention-count` 2→1; `twin-divergence`(2 parsery→1); `dead-guard`→fail-loud | S0 | nie dodawaj 3. parsera | **ACK** (assert w HARD-bramce; off-peak); replay (czy assert nie wybucha na realnym) |
| **S4c** | L3: `_shift_start_dt` midnight-symmetry (mirror `_shift_end_dt:1278`) | L3 | `twin-divergence`(start↔end → 0) | S0 | przeniesienie, nie 2. helper | niskie; razem z R1-B floor L0 |
| **S5n** | NB: rename HARD-misnomerów na realną warstwę (`PICKUP_SPAN_PENALTY`, R-RETURN-METRIC, …) LUB re-layer (R2) | NB | `name-layer-mismatch` 3-4→0 | S0 + **R2/R7 decyzja** | golden ON==OFF (rename) | **ACK** (rename-vs-relayer = koherencja, intencja „VETO") |
| **S6** | INV-SEM suite zielony → runtime-tripwiry (strażniki nawrotu, wzór `_sanity`/`carried_first_guard`) | wszystkie | `semantic-runtime-invariant` 1→5 | S0 + S1v-S5n | strażnik mierzy, nie zmienia | niskie (shadow-mierz); **GATE dla F-rozdziałów** |
| **F1** | **S1 rdzeń:** `eta_pickup_decision` (surowy, 1 komputacja) ⊥ `eta_pickup_display` (derywat); HARD-reject>60→L5; serializ. `eta_source` | S1 | `semantic-role-overload`(2→1); `display-feeds-decision`(1→0); `compute-skew`(2→1) | S6 + **R1-B**(floor-overlay) + **R3**(eta_source) + **R2**(reject→L5) | additive (NOWE pole, nie mutacja) | **ACK** (dotyka selekcji+committed LIVE); replay ON↔OFF |
| **F2** | **S2:** atomowy pair-writer `(coords,address,city)`; retire `ENABLE_REGEOCODE_SYNC_TEXT`; pozycja-z-delivery `_valid`-guard | S2 | `coupled-field-async-write`(1→0); `flag-leak`(1→0) | S6 + **R1-D**(ETAP4) + oracle at-198(01.07) + **R5/F1**(`_valid`) | jeden writer, nie N | **ACK**; retire-flag po oracle |
| **F3** | **S3:** jeden derive-point uwagi (happy∧fallback) + re-parse-przy-edycji (sweep) | S3 | `field-boundary-loss`(1→0); `temporal-reachability`(1→0) | S6 | jeden derive-point, nie 2 ścieżki | niskie (proaktywne, impact dziś niski); protokół |

**Sekwencja krytyczna:** `S0 → {S1v, S2v, S3c, S4c, S5n} → S6 (suite+tripwiry) → {F1, F2, F3}`. **🔒 Bramka nieprzekraczalna:** F-rozdziały (decyzyjne) NIE przed S6 — runtime-tripwir MUSI mierzyć nawrót PRZED dotknięciem decyzji (lekcja: „nie deklaruj — udowodnij ON≠OFF + brak regresji"). Vocab/konwencje (S1v-S4c) idą wcześnie bo tanie i disarm-mine.

---

## 4. WKŁAD W DASHBOARD ENTROPII (§4 — liczby DZIŚ → cel)

| Metryka entropii | Root | DZIŚ (R4) | Cel | Krok |
|---|---|---|---|---|
| `semantic-role-overload` (pole/token z N rolami) | S1·S2·L2 | eta_pickup 2 · delivery_coords 2 · tier 4 · uwagi 2 | 1/pole (lub guarded) | S1v·F1·F2·F3 |
| `display-feeds-decision` (display zawraca do decyzji) | S1 | 1 (hhmm→time_arg→committed) | **0** | F1 |
| `compute-skew` (pole liczone 2× rozjeżdżalnie) | S1 | 1 (eta_pickup main∥post-loop) | **0** | F1 |
| `coupled-field-async-write` (writer 1-z-pary) | S2 | 1 (regeocode coords-bez-tekstu) | **0** | F2 |
| `flag-leak` (flaga decyzyjna poza fingerprint) | S2 | 1 (REGEOCODE_SYNC_TEXT) | **0** | F2 (most R1-D) |
| `field-boundary-loss` (derywat dropowany w persist) | S3 | 1 (uwagi+3 w fallbacku) | **0** | F3 |
| `temporal-reachability` (parse tylko-at-create) | S3 | 1 (uwagi #18) | **0** | F3 |
| `name-layer-mismatch` (HARD-nazwane-SOFT) | NB | 3-4 (SPAN_HARD/VETO/LATE_GATE/[DEPRECATE]) | **0** | S5n |
| `tz-convention-count` (per naive-datetime) | L1 | 2 (Warsaw/UTC) | **1** | S3c |
| `twin-divergence` (2 parsery TZ / start↔end) | L1·L3 | 2 (picked_up_at) + 1 (start≠end) | **0** | S3c·S4c |
| `dead-guard` (defensywny no-op maskujący minę) | L1 | 1 (`if None:→UTC` math-layer) | **0** (fail-loud assert) | S3c |
| `vocab-copy-count` (nazwy per pojęcie) | L2·L4 | tier 4 · WARSAW 6 · order_type 2-spell · 60min 2 | 1/pojęcie | S1v·S2v |
| `implicit-unit` (prefiks-ambiguous / brak sufiksu TZ) | L4 | eta_pickup_min∥_utc · pickup_at ≥7 bez sufiksu | sufiks-enforced | S2v |
| `semantic-runtime-invariant` (strażniki spójności pary) | wszystkie | **1** (`_sanity` ISO≡HH:MM) | **5** (INV-SEM-1..5) | S6 |

**Każdy krok S0-F3 ściśle redukuje ≥1 wiersz i nie pogarsza żadnego** (warunek zbieżności). Po domknięciu R4: każde pole ma jedną rolę (lub guarded), każdy token jedną nazwę, każda nazwa pasuje do warstwy, jedna konwencja TZ, a 5 runtime-tripwirów pilnuje nawrotu (vs dziś 1).

---

## 5. POKRYCIE / JAWNE LUKI / co NIE jest R4 (anty-double-count)

**Zweryfikowane świeżym grepem (HEAD `8024705`):** `dispatch_pipeline.py:4057/4061/4067/4077/5162/5174/5199/5287/5610/5862/5877/737/739/743` · `feasibility_v2.py:127/355/442/628/638/749/751/904/1090/1123` · `common.py:261/430/467/807/2657/2822` · `gastro_edit.py:154/157` · `courier_resolver.py:740/1252/1269/1278` · `state_machine.py:61/515/533/779` · `plan_recheck.py:284/288/1071` · `panel_client.py:566/692` · `tools/czasowka_uwagi_oracle.py:153` · `auto_koord.py:32` · WARSAW-const count (6 nazw).

**Jawne luki (nie cisza):**
1. **Materialność skew F1-C** (pre_shift `:4057`≡`:5877`?) — strukturalnie CONFIRMED, runtime PLAUSIBLE → Faza C oracle.
2. **Display↔display** (Telegram-floor vs konsola-surowy `eta_pickup_hhmm`→time_arg) — czy floor zmienia committed: NIE potwierdzone runtime → Faza C.
3. **L1 realny żywy +2h** poza checkpoint — NIE zmierzone (1 nawrót CONFIRMED, reszta latentna); Faza C `ziomek_time_route_monitor`.
4. **courier-app Kotlin** — lokalny re-format `eta_pickup`/`uwagi`/TZ poza serwerowym build_view — NIE czytany (granica A6 LUKA #1).
5. **Most paczki** — natywny `notes`/`uwagi`↔coords parcel-lane — NIE prześwietlony (A6 LUKA #2; `notes` paczki ≠ gastro `uwagi`).
6. **Pełna lista konsumentów `drop_zone_from_address`** — spot-check 3, nie wszystkie ~10.

**NIE-R4 (cross-ref, NIE double-count — owns gdzie indziej):**
- **Floor `pickup ≥ shift_start`** (17 powierzchni, `available_from`) = **R1-B** (`earliest-pickup-floor-no-chokepoint`). R4 owns TYLKO display-floor-overlay jako derywat S1 + L3 date-anchoring; mechanizm floor = R1-B.
- **`eta_source` VOID / serializer-allowlist / objm-canary** = **R3** (`serializer-allowlist-metrics-vanish`, `objm-shadow-canary`). R4 owns rolę pola; prowenancja-instrument = R3.
- **HARD-reject>60 / R-RETURN re-layer / split-layer** = **R2** (`hard-feasibility-split-layer`). R4 owns NAZWĘ (NB); umiejscowienie HARD/SOFT = R2.
- **Sentinel `(0,0)` / coord-ingest-chokepoint** = **R5/F1** (`coord-sentinel-no-ingest-chokepoint`, K5). R4 owns coupled-write-asym + cross-field-reuse (S2); zatrucie sentinel = R5.
- **`flag-state-3-layer` / fingerprint / REGEOCODE w ETAP4** = **R1-D** (`flag-state-3-layer-no-single-source`). R4 owns asymetrię pary (S2); leak-flagi domyka R1-D.
- **Kalibracja zła-oś (G)** = **R5** (`calibration-on-wrong-axis`). Osobny root; NIE semantyka pól.
- **Numeric-threshold-scatter (N)** = **R3** (`numeric-threshold-scatter-mixed-override`). dual-60 (L4) = nazewnicza fasetka; progi-rozsyp = R3.
- **`DEPRECATE_LEGACY_HARD_GATES` martwy / R7-LONG_HAUL=99 / skeletony** = **R6c-1** (`dead-decision-code`). R4 owns misnomer NAZWY; usunięcie martwego = R6.
- **route-order `podjazdy` 2 implementacje** = **R1-A** (`one-route-order-module`). L-słownictwo odnotowane, root = R1.
- **STOP na dyspozytorni** — Mailek/Papu poza zakresem.

---

## 6. HANDOFF — otwarte decyzje Adriana (przed PoC)

1. **NB rename-vs-relayer (koherencja, ACK):** czy HARD-misnomery PRZEMIANOWAĆ (zachowanie SOFT jest świadome → `PICKUP_SPAN_PENALTY`, R-RETURN-METRIC) czy PRZENIEŚĆ do HARD (zachowanie ma być twarde)? To intencja, nie grep — szczególnie R-RETURN „VETO" (czy ma realnie wetować, czy zostaje instrumentacją). Rozstrzygnięcie wspólnie z **R2/R7**. Rekomendacja-DRAFT: **rename** dla SPAN_HARD/LATE_GATE (zachowanie świadomie SOFT), **pytanie otwarte** dla VETO.
2. **L1 `assert-no-naive` (ACK):** zamiana defensywnego `if None:→UTC` na fail-loud `assert` w math-layer — bezpieczniejsze długoterminowo (demaskuje minę), ale ryzyko wybuchu jeśli JAKAŚ ścieżka realnie podaje naive dziś. Wymaga replay PRZED (czy assert nie odpala na żywym ruchu). Rekomendacja-DRAFT: assert za flagą-shadow najpierw (mierz nawrót), potem fail-loud.
3. **F1 eta_pickup decision/display (ACK, najwyższa dźwignia F):** additive NOWE pole (wzorzec #8) — ale dotyka selekcji+committed LIVE. GATE na R1-B (floor-overlay) + R3 (eta_source) + R2 (reject→L5). PoC kandydat R4 = **S1 eta_pickup split** (archetyp display≠decision) ALBO **S1v tier-rename** (najtańszy, mechaniczny, disarm-mine). Rekomendacja-DRAFT PoC: **S1v tier** (zero ryzyka zachowania, natychmiastowa redukcja audit-friction) jako rozgrzewka, **F1 eta_pickup** jako docelowy high-value.
4. **Kolejność vs inne rodziny:** S6 (runtime-tripwiry) PRZED F-rozdziałami — potwierdź że to fundament (analogicznie do R1 FAZA 0 rejestr-flag PRZED rdzeniem). S2v `order_type` enum przed jakimkolwiek nowym positive-matcherem (disarm `oracle:153`).

---

> **DRAFT — koniec.** Rodzina R4 to dług SEMANTYCZNY: dziś głównie latentny (maskowany negacją/flagą/granicą), ale każda dwuznaczność = uzbrojona mina dla przyszłej zmiany. Stan docelowy = **mapa == terytorium**: każda nazwa mówi prawdę o zachowaniu, każdy display wywodzi się z decyzji (nigdy odwrotnie), jedna konwencja per prymityw, a 5 runtime-tripwirów (vs 1 dziś) pilnuje że nie wróci. Wykonanie = osobny ACK + ETAP 0→7.

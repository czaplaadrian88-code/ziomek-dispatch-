# C16 — ADDRESS MISMATCH (TEKST↔MIASTO + TEKST↔PIN) — LANE C RUNTIME-ORACLE

**Agent:** C16-address-mismatch · **Lane:** C (runtime-oracle, C9/C11) · **Tryb:** READ-ONLY · **Data:** 2026-06-30 ~17:00 UTC · sesja tmux 2.
**Backing:** ten plik. **Numery linii re-grepowane świeżo dziś** (dryfują — nie z seed).
**DoD:** ZERO edycji silnika / restartów / flipów / git / --notify. Oracle = recompute DRUGĄ metodą; narzędzia piszące do `dispatch_state` NIE odpalane (review tool pominięty — pisze VERDICT). Output oracle → scratchpad.

---

## 0. CO TO ZA PRZYRZĄD (dwa detektory w jednym module + jeden review-tool)

`dispatch_v2/address_mismatch.py` — SHADOW/log-only, **gates NIC** (advisory; decyzja A/B/C = człowiek/Adrian):
1. **ulica↔miasto** (`check_street_town:85` → `maybe_log_mismatch:108`): ulica „silnie białostocka" (≥`_ADDR_CHECK_MIN_BIA=5` trafień w cache, `:28`) wpisana w innym mieście gdzie prawie nie występuje (≤`_ADDR_CHECK_MAX_HERE=1`, `:29`) → wpis. Flaga `ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW`. Caller: `shadow_dispatcher.py:1187` (per NEW_ORDER event).
2. **tekst↔pin** (`check_text_coords:185` → `maybe_sweep_text_coords:223`): geokoduj `delivery_address` (cache-first) i porównaj z `delivery_coords` (pin, na którym kurier realnie jedzie); `_haversine_m` > `_COORDS_MISMATCH_MIN_M=400.0` (`:141`) → wpis. Flaga `ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW`. Caller: `shadow_dispatcher.py:1128/1131` (throttled sweep `orders_state`, raz/tick ~300s).

Oba → ten sam `dispatch_state/address_mismatch_shadow.jsonl` (text-pin oznaczone `check:"text_coords"`).
Review/werdykt: `tools/address_mismatch_review.py` (read-only liczy, pisze `VERDICT.txt` do dispatch_state → **NIE odpalałem**, czytam istniejący snapshot 07:00).

**Flagi efektywne (flags.json):** `ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW=true` (l.223), `ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW=true` (l.224), `ENABLE_REGEOCODE_SYNC_TEXT=true` (l.225 — fix-u-źródła `gastro_edit.regeocode_and_update`).

### ⚠ Reconcile „no-op do restartu" (z zlecenia) — **ROZWIĄZANE**
Caveat „kod+flaga ON = no-op do restartu dispatch-shadow" był HISTORYCZNIE prawdziwy w momencie flipu flagi. **DZIŚ NIEAKTUALNY:** `dispatch-shadow` `ExecMainStartTimestamp=2026-06-30 09:55:55 UTC` (+ wcześniejszy restart 29.06). Detektor **ŻYWY** — 16 wpisów w logu, w tym 2 text_coords z DZIŚ po restarcie (484496 13:25 UTC, 484525 14:46 UTC). Najwcześniejszy text_coords = 484346 2026-06-29 17:36 UTC ⇒ aktywny już od restartu 29.06. **Aktywność POTWIERDZONA danymi, nie deklaracją.**

---

## 1. ORACLE A — case 484269 „Można"≠„Mroźna" (motywujący oracle; NIE w żywym logu)

**Ustalenie:** 484269 **NIE występuje** w `address_mismatch_shadow.jsonl`. To case sprzed aktywacji detektora — zakodowany WYŁĄCZNIE jako unit-oracle `tests/test_address_coords_mismatch.py:31 test_oracle_484269_fires` (+ docstringi `address_mismatch.py:128`, `shadow_dispatcher.py:1124`, review tool `:98`, `gastro_edit.py:138`). 484269 nieobecny w bieżącym `orders_state.json` (rolled-over/pruned).

**Druga metoda (niezależny recompute, scratchpad/c16_oracle.py, 2× determinizm):**
- Z `geocode_cache.json` (bezpośredni odczyt, NIE przez instrument): `Można 10/23` → `(53.1324886, 23.1688403)`; `Mrozna 10/23/2` (key `mrozna 10, białystok`) → `(53.1610167, 23.1261602)`.
- Pin z fixtury testu `_MROZNA=(53.1610167, 23.1261602)` = **BAJT-IDENTYCZNY** z cache (potwierdza że fixtura = realny stored pin 484269).
- `haversine(Można, Mroźna)` = **4262.0 m** ORAZ `spherical-law-of-cosines` = **4262.0 m** (dwie niezależne formuły zgodne).
- Deklaracja w docu/teście „4,26 km" = **4260 m** → recompute **4262 m** = MATCH (zaokrąglenie 4262→4,26 km).

**Werdykt A: VALIDATED.** Oracle 484269 jest realnym, odtwarzalnym rozjazdem 4262 m; >> próg 400 m → detektor BY ZAFIROWAŁ. Liczba instrumentu PRAWDZIWA. `proxy-certified` (odległość prawdziwa; KTÓRA współrzędna jest poprawna — tekst czy pin — NIE rozstrzygnięte; pin = button/panel-truth, nie GPS-ground-truth).

---

## 2. ORACLE B — recompute distance_m KAŻDEGO wpisu text_coords w żywym logu

8 wpisów `check:"text_coords"`. Recompute haversine ze STORED `text_coords`+`used_coords` (zaokr. 6 dec) + niezależny geokod ulicy z cache:

| oid | logged_m | recompute_m | hav2_m (2. formuła) | Δ | >400? | geokod-recheck (cache→pin) |
|---|---|---|---|---|---|---|
| 484346 (Wesoła 21/28) | 7598.3 | 7598.3 | 7598.3 | 0.05 | OK | cache 53.12103,23.15481 → 7598 m |
| 900138096 (Zwycięstwa 8) | 1071.4 | 1071.4 | 1071.4 | 0.02 | OK | cache 53.13589,23.13378 → 1071 m |
| 900138097–100 (Zwycięstwa 8 ×4) | 1071.4 | 1071.4 | 1071.4 | 0.02 | OK | identyczny sender |
| 484496 (Wł. Broniewskiego 18) | 4512.9 | 4513.0 | 4513.0 | 0.06 | OK | cache 53.13949,23.12803 → 4513 m |
| 484525 (Ogrodniczki 12) | **14036.1** | 14036.1 | 14036.1 | 0.02 | OK | cache 53.14181,23.11679 → 14036 m |

**Inwarianty-tripwire:** (1) wszystkie 8 `distance_m > 400` → **ZERO sub-progowych fałsz-fire**; (2) logged == recompute do Δ≤0.06 m (różnica = zaokrąglenie 6-dec w logu); (3) dwie niezależne formuły zgodne; (4) niezależny geokod ulicy z cache == `text_coords` w logu (potwierdza że `text_coords` = geokod cache, a `distance_m` = luka do `used_coords`).

**Werdykt B: VALIDATED.** Instrument tekst↔pin liczy odległość WIERNIE. `proxy-certified`.

---

## 3. ORACLE C — town-detector counts (niezależny recount z cache)

Odtworzyłem `_street_town_counts` (`address_mismatch.py:54-82`) niezależnie z `geocode_cache.json` i porównałem z 8 wpisami ulica↔miasto:

| street | key | bia (recount) | here | logged bia | fires |
|---|---|---|---|---|---|
| Słonimska 24/47 | slonimska | 44 | 1 (Grabówka) | 44 | MATCH |
| Złota 6 | zlota | 9 | 1 (Kuriany) | 9 | MATCH |
| Wesoła 21/28 | wesola | 38 | 1 (Grabówka) | 38 | MATCH |
| Ogrodniczki 12 | ogrodniczki | 17 | 1 (Ogrodniczki) | 17 | MATCH |
| Akacjowa 10 | akacjowa | 6 | 1 (Zaścianki) | 6 | MATCH |
| Rzemieślnicza 28/26 | rzemieslnicza | 35 | 1 (Grabówka) | 34 | drift +1 |
| Wiejska 8 lokal 18 | wiejska | 70 | 1 (Olmonty) | 67 | drift +3 |
| Wł. Broniewskiego 18 | wladyslawa broniewskiego | 35 | 1 (Kleosin) | 34 | drift +1 |

**Werdykt C: VALIDATED** (jako heurystyka-flag, `proxy-certified`, NIE ground-truth). Counts wierne cache; 3 „drift" = cache URÓSŁ między log-time a teraz (counts MONOTONICZNIE rosną z akumulacją cache) — boolean fire (bia≥5∧here≤1) STABILNY. ⚠ **Cień semantyczny:** WSZYSTKIE 8 zaproponowanych „miast" to realne wsie-satelity Białegostoku (Grabówka/Kuriany/Olmonty/Kleosin/Ogrodniczki/Zaścianki) → ekspozycja na FAŁSZ-POZYTYW dla pospolitych nazw ulic (Wiejska/Wesoła/Złota/Akacjowa) które LEGIT istnieją też we wsi (`here=1` znaczy że ulica wystąpiła tam 1×). Verdict tool sam to dyskla­muje („sprawdź pod false-positive"). Instrument NIE kłamie o tym co mierzy (rozkład cache); inferencja „→ Białystok" jest niepewna.

---

## 4. ORACLE D — werdykt at-189 (07:00) = STALE (czytany jako „bieżący" wprowadza w błąd)

`address_mismatch_review_verdict.txt` mtime **2026-06-30 07:00 UTC**. Konwersja ts wszystkich wpisów (scratchpad) vs werdykt:
- Werdykt text-pin mówi: „wpisów: 6 | śr/dzień: 3.0 | mediana 1071 m | **max: 7598 m**".
- Żywy log (mtime 16:54): **8** text_coords, **max 14036.1 m** (Ogrodniczki, logged **14:46 UTC** — 7,7h PO werdykcie) + 484496 4513 m (13:25 UTC) — OBA niewidoczne w werdykcie.
- Werdykt town mówi „wpisów: 5"; żywy log = **8** (484496 15:21, 484525 16:39, 484577 18:54 — wszystkie po 07:00).

**Werdykt D: VOID jako CURRENT-state oracle.** Tool liczy poprawnie, ale plik-werdykt to ZAMROŻONY snapshot bez TTL/markera „stale". Sesja czytająca go o 16:54 dostaje 07:00-stan, który UNDER-reprezentuje severity (gubi największy rozjazd 14 km). Klasa H (stale-read) — bliźniacze z A4 §8 (`drive_speed_overshoot_verdict.txt`, `bug4_reseq_verdict.txt` bez TTL). at-198 (01.07 17:00) odświeży, ale to ten sam tool → nowy snapshot, dalej bez TTL.

---

## 5. GUARD `_skip_for_text_pin` (fałszywki) — analiza

`address_mismatch.py:159` pomija detekcję gdy geokod tekstu byłby NIEPEWNY: (1) kod pocztowy NN-NNN na początku (`_POSTAL_PREFIX_RE:156`); (2) BRAK `delivery_city` + ulica wielomiastowa (≥2 w innym mieście w cache).
- **Żywe wpisy:** wszystkie 8 mają `city="Białystok"` → branch-2 nieaktywny; żaden nie ma postal-prefix → **guard nie odsiał ŻADNEGO żywego fire** (wszystkie przeszły legalnie). Zero zaobserwowanych fałsz-pozytywów PRZEPUSZCZONYCH przez guard.
- **Znana luka (by-design, kalibracja at-198):** branch-2 = FAŁSZ-NEGATYW — realne typo bez `delivery_city` na ulicy wielomiastowej zostanie POMINIĘTE (komentarz `:153-155` to przyznaje). Koszt świadomy (unik FP > złapanie tych FN).
- **Zależność od mutowalnego cache:** branch-2 czyta `_street_town_counts` (mtime-keyed lru) → filtr DRYFUJE gdy cache rośnie. Niedeterministyczny w czasie (boolean fire stabilny, ale granica skip ruchoma).
- Testy pinują: `_skip_for_text_pin("16-070 Porosły",None)=True`, `("11 Listopada 5","Białystok")=False`, `("Spacerowa 17","Białystok")=False` (`test_address_coords_mismatch.py:70-99`). 23/23 PASS (uruchomione, log NIE tknięty — mtime 16:54:32 niezmieniony).

---

## 6. BLIŹNIAKI (B/J) — cross-repo + asymetria powierzchni

| # | Bliźniak | Stan | Dowód |
|---|---|---|---|
| **B-twin town** | engine `address_mismatch.check_street_town` ↔ panel `nadajesz_clone/panel/backend/app/api/dispatch.py:503 check_street_town` | **DWIE OSOBNE KOPIE, BRAK wspólnego importu.** Identyczne progi (`_ADDR_CHECK_MIN_BIA=5` panel:449 == engine:28; `MAX_HERE=1` panel:450 == engine:29). Parytet trzymany TYLKO komentarzem (`address_mismatch.py:11-12` „Zmiana progu = zmień OBA miejsca") + 2 osobne golden-testy per repo. **Każde repo czyta WŁASNY geocode_cache** → `street_bialystok_count` RÓŻNI się między repo (panel test oczekuje 42 dla „Armii Krajowej", engine cache inny) → twin parzysty w LOGICE/progu, nie w wyjściu. | grep + test_address_check.py:34 |
| **B-asym text↔pin** | engine `check_text_coords:185` ISTNIEJE ↔ panel **BRAK** detektora tekst↔pin | Panel ma `_haversine_m` tylko w `tracking_map.py:45`/`finance.py`/`economics.py` (inne cele — trasa/wypłata), ZERO address text-pin detektora. `shadow_dispatcher.py:1127` jawnie: „check tekst↔pin w app/api/dispatch.py — **N-D w v1** (dołożymy po dowodzie)". Złe piny wpisane PRZEZ panel nie są łapane u źródła panelu; tylko engine ingestion-sweep + `regeocode_and_update` sync. | grep panel |

---

## 7. FINDINGS (instancje, plik:linia świeże)

| id | klasa | plik:linia | kind | summary | sev | open |
|---|---|---|---|---|---|---|
| C16-1 | H/E | dispatch_state/address_mismatch_review_verdict.txt + tools/address_mismatch_review.py:149 | symptom | Werdykt 07:00 STALE: max 7598 vs żywy 14036 m, 5/6 vs 8/8 wpisów; brak TTL — czytany jako bieżący gubi 14 km Ogrodniczki | P2 | tak |
| C16-2 | B/J | address_mismatch.py:11 ↔ panel app/api/dispatch.py:503 | source | Cross-repo twin town-detector: 2 kopie, brak wspólnego importu, parytet tylko przez komentarz+2 golden; każde repo inny cache → liczby różne | P2 | tak |
| C16-3 | B | address_mismatch.py:185 ↔ shadow_dispatcher.py:1127 | source | Asymetria: engine ma detektor tekst↔pin, panel NIE → złe piny z panelu nie łapane u źródła | P2 | tak |
| C16-4 | N/M | address_mismatch_shadow.jsonl(L7-11) + tools/address_mismatch_review.py:118 | symptom | Materialność zawyżona: 5/6 wpisów to TEN SAM parcel-sender „Zwycięstwa 8" (900138096-100, coarse 3-dec coords); liczone per-order („3.0/dzień") nie per-distinct-adres (realnie 4 adresy) | P3 | tak |
| C16-5 | M | address_mismatch.py:118 + :257 | source | `except OSError: pass` — cichy fail zapisu jsonl, utrata wpisu niewidoczna (wzorzec A4 §8) | P3 | tak |
| C16-6 | H/O | address_mismatch.py:172-173 | source | dedup `_coords_logged`/`_sweep_last_ts` per-proces in-mem → reset na restarcie; order aktywny przez restart może być re-logowany (double-count); restart 09:55 zresetował | P3 | tak |
| C16-7 | G | address_mismatch.py:54-82 | source | `street_bialystok_count` zależny od mutowalnego cache (34→35, 67→70 drift); logged number nieodtwarzalny później; `_skip_for_text_pin` branch-2 też dryfuje z cache | P3 | tak |

---

## 8. ORACLE-VERDICTS (P0 wg C9)

| instrument | verdict | 2. metoda | proxy/ground | co napędza (flip/decyzja) | inwarianty |
|---|---|---|---|---|---|
| address_coords_mismatch (text↔pin distance_m) | **validated** | własny haversine + spherical-law-of-cosines + bezpośredni geocode_cache lookup; 484269=4262 m (2 formuły), 8/8 żywych Δ≤0.06 m | proxy-certyfikowany (odległość prawdziwa; która współrzędna poprawna=nieokreślone; pin=button-truth) | NIC bezpośrednio (shadow advisory); informuje decyzję Adriana A(alert koord)/B(363-par gate)/C(zostaw) + zmotywował LIVE `ENABLE_REGEOCODE_SYNC_TEXT` | wszystkie >400; logged==recompute; 2 formuły zgodne; geokod cache==text_coords |
| address_town_mismatch (street↔town counts) | **validated** | niezależny rebuild street→town z cache; 5/8 exact, 3/8 drift +cache-growth, 8/8 fire (bia≥5∧here≤1) | proxy-certyfikowany (counts wierne; „suggest Białystok"=heurystyka, FP-ekspozycja dla wsi-satelit) | NIC bezpośrednio; advisory ten sam decision A/B/C | same fire-condition; counts monotoniczne z cache |
| address_mismatch_review.py verdict (at-189 07:00 .txt) | **void** (jako current-state) | konwersja ts każdego wiersza vs mtime werdyktu | proxy | feeds decyzję A/B/C Adriana — stale snapshot under-reprezentuje severity (gubi max 14 km) | max-live 14036 > werdykt 7598; 8>6 i 8>5 wpisów |

---

## 9. POKRYCIE

**coverage_declared:**
- `address_mismatch.py` PEŁNY (check_street_town:85, maybe_log_mismatch:108, check_text_coords:185, maybe_sweep_text_coords:223, _skip_for_text_pin:159, _haversine_m:176, _street_town_counts:54-82, progi :28/29/141/156).
- Callery `shadow_dispatcher.py:1128/1131` (text-pin sweep) + `:1187/1190` (town per-event); komentarz twin `:1124-1127`.
- `tools/address_mismatch_review.py` (cała logika werdyktu, NIE odpalony — pisze do dispatch_state).
- Żywy `address_mismatch_shadow.jsonl` (16 wierszy: 8 text_coords + 8 town) — recompute 2×.
- `address_mismatch_review_verdict.txt` (07:00 snapshot) — stale-check.
- `geocode_cache.json` (bezpośredni odczyt — 2. metoda geokodu).
- Unit testy `test_address_coords_mismatch.py` + `test_address_mismatch.py` (23/23 PASS, izolowane monkeypatch _SHADOW_LOG→tmp; żywy log NIE tknięty).
- Panel twin `panel/backend/app/api/dispatch.py:503` (istnienie + progi 449/450) + brak text-pin twina (grep).
- flags.json (3 flagi true) + `dispatch-shadow` restart time (aktywność).

**coverage_gaps (jawne):**
1. **KTÓRA współrzędna poprawna (tekst vs pin)** — NIEdeterminowalne bez GPS-ground-truth (join `gps_delivery_truth.jsonl`); instrument = proxy „rozjazd istnieje", nie „adres błędny". Nie joinowałem GPS-truth per-oid (zlecenia pruned/parcel).
2. **Panel `check_street_town`** czytany przez test+docstring+progi, NIE linia-po-linii pełnej funkcji (granica cross-repo; potwierdzony jako kopia o identycznych progach).
3. **`gastro_edit.regeocode_and_update` / `ENABLE_REGEOCODE_SYNC_TEXT` source-fix path** — NIE oracle-testowany na żywym case (484269 pruned z orders_state; tylko unit `test_regeocode_sync_text.py` istnieje).
4. **Parcel coords SOURCE** — dlaczego `used_coords` parceli coarse 3-dec [53.129,23.145] (900138xxx) NIE prześledzony do kodu ingestii paczki (parcel lane poza modułem address_mismatch).
5. **at-198 (01.07 17:00)** PENDING — text-pin review-werdykt jeszcze nieprodukowany; mój oracle wyprzedza go niezależnym recompute.
6. **Bliźniak ulica↔miasto: liczbowy parytet engine↔panel** — NIE policzony (różne cache per repo → z definicji się różni; to B/J nie liczba).

**Determinizm:** c16_oracle.py 2× identyczny output. Testy 1× (23/23). Brak zapisu do dispatch_state (mtime logu 16:54:32 przed i po).

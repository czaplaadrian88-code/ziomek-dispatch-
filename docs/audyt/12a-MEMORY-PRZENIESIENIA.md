# K2.6 — PRZENIESIENIA (rejestr „NIC NIE GINIE") dla kompaktu MEMORY.md

Ten plik = ubezpieczenie. Wypisuje bloki, które w kompakcie (`12-MEMORY-PROPOZYCJA.md`) ZNIKAJĄ z indeksu lub są mocno skrócone, wraz z **pełną treścią do dopisania/weryfikacji w pliku-temacie**, jeśli plik-temat jej NIE pokrywa.

## A. Bezpiecznik 3-warstwowy (dlaczego wiedza nie ginie nawet bez dopisań)
1. **Git.** `memory/` jest repozytorium git z auto-commitami (`git log memory/` — ostatni `9ba7441 2026-07-03 12:16`). Pełna treść każdej wersji MEMORY.md jest odzyskiwalna `git show`.
2. **Backup shrinku.** Realny kompakt MUSI zacząć się od `cp MEMORY.md MEMORY.md.bak-pre-shrink-2026-07-03` (konwencja 7 poprzednich shrinków). Ten backup = pełny 218 KB snapshot obok nowego indeksu.
3. **Pliki-tematy.** 240/244 plików nadal zalinkowanych wprost; 4 zwinięte mają własne pliki (patrz sekcja D). Detal operacyjny każdego tematu = w jego pliku (architektura pamięci: MEMORY.md=indeks, plik=źródło prawdy).

**Wniosek:** dopisania z sekcji C są zalecane (żeby detal był w pliku-kanonie, nie tylko w backupie), ale ich brak NIE kasuje wiedzy — backup+git ją trzymają.

## B. Tabela pokrycia (wpis indeksu → plik-temat → status)

| Blok z MEMORY.md (stare linie) | Plik-temat docelowy | Status pokrycia |
|---|---|---|
| KANON REGUŁ / PRZED-TEMATEM / PROSTYM-POLSKIM / NIE-PYTAJ / U-ŹRÓDŁA / PROTOKÓŁ / SZKIELET / TODO / DŁUG (reguły nadrzędne) | odpowiednie pliki (ZIOMEK_REGULY_KANON, feedback_rules, feedback-*, ziomek-change-protocol, ziomek-architecture-skeleton, todo_master, tech_debt_checklist) | ✅ POKRYTE — tylko skrót linii, detal w pliku |
| L52 „ZMIANA ZIOMKA + Reguły biznesowe ZAKODOWANE" | ziomek-change-protocol.md | ✅ POKRYTE — zweryfikowane grep: sekcja „Reguły biznesowe ZAKODOWANE" (l.38) + „DYSKRYMINACJA POZYCJI 8 bliźniaków" (l.45) są w pliku |
| L54 „AUDYT ANTY-KŁAMSTWO 28.06" (11 kłamstw + 16 commitów 29.06) | shadow-jobs-registry.md | 🟡 CZĘŚCIOWO — plik ma „BACKLOG NAPRAW Z AUDYTU" + status #1..#12; **enumeracja 16 commitów z 29.06 do weryfikacji** → sekcja C1 |
| L56 „ZAPLANOWANE JOBY SHADOW" (snapshot reconcile 27.06) | shadow-jobs-registry.md | 🟡 SNAPSHOT HISTORYCZNY — plik ma świeższy PENDING (29.06); ten z 27.06 archiwalny → sekcja C2 |
| L58 „JUTRO RANO 26.06 (resweep)" | pending-global-resweep-2026-06-24.md | 🔴 ZDEZAKTUALIZOWANE (26.06 minęło) — case w pliku; przypomnienie do archiwum → sekcja C3 |
| L50 „OFF-PEAK: A ZROBIONE, B zostaje" | address-wrong-town-geocode-2026-06-26.md + nadajesz_coordinator_route.md | 🟡 CZĘŚCIOWO — index sam wskazuje „Detal: [[address-wrong-town-geocode]]"; **LEKCJA shared-deploy + b1/b2 activation do weryfikacji** → sekcja C4 |
| L33 „AUTONOMIA (deploy LIVE + next steps + rollback)" | autonomy-readiness-2026-06-30.md | 🟡 CZĘŚCIOWO — plik 10,7 KB pokrywa analizę; **deploy 30.06 10:13 + 5 next-steps + rollback do weryfikacji** → sekcja C5 |
| L35/L37 „KALIBRACJA / CARRIED-FIRST" | ziomek-calibration-2026-06-29.md | ✅ POKRYTE — index sam mówi „szczegóły w [ziomek-calibration] sekcja CARRIED-FIRST" |
| L39 „TOP-10 HANDOFF tmux13" | top10-progressive-potential-2026-06-29.md | ✅ POKRYTE — index mówi „NA GÓRZE HANDOFF NASTĘPNA SESJA" (jest w pliku) |
| L122/L125 „KONTROLING GASTRO sty-kwi + per-kurier wnioski" (log sesji) | nadajesz-kontroling-wynagrodzenia-2026-06-20.md + kontroling-zrodla-metodyka.md (KANON 02.07) | 🟡 CZĘŚCIOWO — kanon 02.07 supersedes; **wniosek „per-kurier vs col15 nie reconciliują" do weryfikacji** → sekcja C6 |
| L118-253 „Log sesji" (≈60 paragrafów, każdy z linkiem → plik) | odpowiednie pliki-tematy | ✅ POKRYTE architekturą (każdy ma dedykowany plik); pełne paragrafy w backupie+git → sekcja D (blanket) |
| Sekcje dolne „Panel/Ziomek/Papu/Mailek/Feedback" (już 1-linijkowce) | jw. | ✅ POKRYTE — przeniesione do sekcji tematycznych nowego indeksu 1:1 co do linków |

## C. Pełne bloki DO DOPISANIA/WERYFIKACJI (verbatim z MEMORY.md — jedyna kopia poza backupem)

> Instrukcja: przy realnym kompakcie wkleić każdy blok do wskazanego pliku (jeśli plik go jeszcze nie ma — grep po commit-hashu/frazie), potem usunąć z indeksu.

### C1 → `shadow-jobs-registry.md` (sekcja „BACKLOG NAPRAW Z AUDYTU" / „DONE") — enumeracja 16 commitów 29.06

```
🔴 AUDYT ANTY-KŁAMSTWO 28.06 (11 kłamstw przyrządów POTWIERDZONYCH). ✅ ZROBIONE 29.06 (sesja audyt-fix — wszystkie HIGH wg priorytetu audytu): #9 conftest-leak DOKOŃCZONY (257d315, baseline flag-higieny 10→8 failed; brakowało stałej ENABLE_PLN_QUALITY_AWARE+ratchet doc +3), #4 b_route env-parytet 13 flag+provenance route_env+archiwum widma (c8c5f86, review 30.06 czyta tylko wierne), #1/#2 bundle_calib O2 GATE=overage-only (477b731 — improved 317→304=oracle silnik, 7 zamaskowanych regresji świeżości ODSŁONIĘTE, under_z Z≤35 12,4%=rejestr) ← NAJWYŻSZY pozostały (bramkuje flip 02.07). Wcześniej: #6 would_hard_cap serialize (sesja 18 d23d8a1, kod OK, ⏳ weryfikacja LIVE na peaku), feas_carry rollback, #5a sla_tracker. ✅ ZROBIONE 29.06 c.d. (detection-only sweep, wszystkie z oracle-walidacją): #6a objm_lexr6 G2c per-decyzja (397a665, LIVE 13,7%→GO, był fałszywy WARN 60,5%), #7 reassign_quality (7717cf6, rescue_eta vs infeasible→precyzja N/A), #5 drive_speed (20dec97, flaga OFF→N/A nie CLEAN), #11 carried-anchor docstring (132bcce). + #1 bug4_reseq (5623122, plan.sequence+skip fikcyjnych pickupów+inwariant; widmo 13% delta<0 dowód) + próg bundle_calib 20%→2% (f568228, Adrian) + fix testów courier_reliability FLT-04 (5d7c293, unikalny order_id). ✅ WSZYSTKIE 11 KŁAMSTW PRZYRZĄDÓW NAPRAWIONE; baseline 10→1 failed (tylko time-flaky working_override). ✅ #12 Haversine NAPRAWIONY (ea0569b, oba bliźniaki osrm_client+dispatch_pipeline, guard osrm_fallback, zero żywego wpływu fallback=0, aktywacja=restart ACK). 🟢 geocode-centroid ZBUDOWANY+LIVE (79ffab7, flaga ENABLE_BUNDLE_COLOC_CENTROID_GUARD ON — flip hot 29.06 ~08:48, wyraźny ACK Adriana „zatrute adresy mogą w każdej chwili wpaść w bundling") — guard 0km coloc na BIALYSTOK_CENTER (122 zatrute adresy = mina); weryfikacja LIVE: centroid blokowany, realny coloc zachowany, bez błędów. Rollback flaga OFF hot. ⏭ #5b geofence DOSTAWY (cross-repo fundament, OSTATNI — osobny sprint). Sesja 29.06 audyt-fix: 16 commitów, baseline 10→1 (tylko time-flaky), WSZYSTKIE 11 przyrządów + #12 + geocode + 2 decyzje zrobione; #12 LIVE (restart off-peak), geocode flaga OFF; #5b = jedyne co zostało. ⚠ OTWARTE DECYZJE Adriana: (a) bundle_calib próg MATERIAL_PCT 20%→2% (decyzja 28.06) przed 02.07; (b) 8 pre-existing test_courier_reliability czerwone = FLT-04 ranking, niezwiązane z audytem. Raporty: /tmp/claude-0/-root/5dff4e05-.../tasks/{wvdued2fx,wfey3x75j}.output. Lekcja: oracle-gate (C9/C11) > więcej recenzentów.
```

### C2 → `shadow-jobs-registry.md` (sekcja „DONE / odpalone — archiwum") — snapshot reconcile at-jobów 27.06

```
ZAPLANOWANE JOBY SHADOW — snapshot reconcile 27.06 22:20 (archiwalny): Pending atq: 168 bundle-calib reminder 02.07 (O2 objektyw Faza 1 ZBUDOWANY+PUSHED fe233d1+22ba058, flaga ENABLE_O2_READY_ANCHOR_SWEEP OFF → na GO = FLIP+stroj cap-Z z under_z, NIE build od zera; zostało gate-fix feasibility:1135 osobno), 188 bug#4 reseq-shadow werdykt 28.06 18:00 (WAIT/NO: seq_differs 30% OK, material delta≥1min tylko 10% <próg — dobiera 2. dzień), 189 b2 ulica↔miasto shadow review 30.06 07:00 (flaga ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW LIVE od 27.06; tools/address_mismatch_review.py), 192 B2 feas-carry-readmit POST-FLIP monitor 28.06 21:00 (tools/feas_carry_readmit_postflip.py --notify; CLEAN→zostaje / ALARM→rollback hot flaga=false) (LIVE shadow ENABLE_BUG4_RESEQ_SHADOW, commit 999e84c). ✅ 167 feas-carry ZAMKNIĘTE 27.06 (atrm 167): werdykt sygnał trzyma → B2 #483000 ZBUDOWANE+LIVE od 22:18 UTC (ENABLE_FEAS_CARRY_READMIT=1, commit e72139e+monitor 3eecef6; replay GO 52,6%/−9,7min worst-breach; rollback hot flaga=false). ✅ 182 checkpoint-TZ ZAMKNIĘTE 27.06 ~21:08 (CLEAN — interp_on 958, bag_dropped=ghost cid393/1 peak; atrm 182 + kolektor dispatch-checkpoint-tz-shadow.timer disabled; flaga ENABLE_CHECKPOINT_TS_WARSAW_PARSE zostaje ON). Odpalone 27.06 (odczytane, DONE): 166 min-delivered = INCONCLUSIVE/mało danych → przedłuż shadow NIE flip; 175 poranne podsum. pickup-floor = ✅ PASS oba peaki; 185 off-peak reminder = ping wysłany; 187 drive-speed overshoot = werdykt Telegram-only (brak pliku), brak auto-rollbacku (DRIVE_SPEED_TIER_CORRECTION OFF / PLAN_RECHECK_TIER_DWELL ON) → wygląda CLEAN. Wcześniej 26.06: 170/171/178/179/181/183 + timery resweep/watchdog/time-route exit0.
```

### C3 → `pending-global-resweep-2026-06-24.md` (dopisz „ARCHIWUM — przypomnienie 26.06, zamknięte") — ZDEZAKTUALIZOWANE

```
[ARCHIWALNE — reminder z 26.06, minęło] JUTRO RANO 26.06 (Adrian explicit 25.06 „czytaj resweep jutro rano"): odczytaj werdykt pending-global-resweep review (timer 07:00 UTC → Telegram + pending_global_resweep.jsonl w dispatch_state/; watchdog 07:15). Kontekst: case Adriana „Paweł Ściepko (376) proponowany jednocześnie do Chicago Pizza + Sushi Rany Julek" = greedy per-order pile-on, shadow go łapie (25.06: 22% would_repropose, 31× rozjazd_kierunkow, case dosłownie w logu 15:15-15:16). Przynieś analizę + DECYZJA Adriana A (re-ranker, ~90% gotowy) vs B (fix u źródła) + ACK na ścieżkę LIVE (PENDING_RESWEEP_LIVE, dziś niewpięta — edit msg TG + atomic update pending_proposals z lockiem).
```

### C4 → `address-wrong-town-geocode-2026-06-26.md` (dopisz „OFF-PEAK aktywacja 27.06 + LEKCJA shared-deploy") — CZĘŚCIOWO

```
OFF-PEAK 27.06: A ZROBIONE, B zostaje. RUNBOOK nadajesz_clone/panel/RUNBOOK_offpeak_activation_2026-06-26.md. ✅ A = AKTYWACJA EDYCJI W KONSOLI — DONE 27.06 ~13:16 UTC (commit cf9569c, restart nadajesz-panel off-peak, ACK Adriana): COORDINATOR_EDIT_LIVE=1 (realny zapis uwag/miasta/telefonu/COD/adresu do gastro), ikonka uwag odsłonięta, + ołówek czasu odbioru HH:MM w OrderModal (elastyk→czasówka przez przypisz-zamowienie, ≥60min=auto) i pełna lista aktywnych restauracji z gastro (68→75). Kontrolny test gastro_edit EDIT_OK http=200 odwracalny; regresja 1000/1. Restart aktywował też committed GLOBAL_ALLOC_OVERLAY=1 innej sesji (Faza C 50f158f, intencjonalne). ✅ B+b1 = DONE 27.06 ~13:32 UTC. b2 ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW=True — flip-only HOT-RELOAD BEZ restartu dispatch-shadow (kod b2 już w procesie od restartu 26.06 23:55 > commit 39d3a5c 17:19; C.flag() per-tick): ŚWIADOMIE bez restartu, bo restart wrzuciłby cudzą committed-flag-ON global_alloc_store/pending_global_resweep (mtime 12:34) → mniejszy blast-radius. Doc flagi committed c77937a. KROK 4 bezprzedmiotowy (483504 terminalne; pin Olmonty już był). b1 render address_warning w NewDeliveryForm.tsx + typ AddressCheckOut w api.ts (restaurant-frontend) — commit 45c51fa, vite build (bypass pre-existing czerwonego tsc -b frontend-shared), deploy /var/www/html/panel/ (gps.nadajesz.pl/panel HTTP 200, b1 w bundlu). Rollbacki: b2 flaga=false hot / flags.json.bak-pre-b2-activation-2026-06-27; b1 /var/www/html/panel.bak-pre-b1-20260627-133154 / git revert 45c51fa. LEKCJA shared-deploy: aktywuj FLAGĄ (hot-reload) gdy kod już w biegnącym procesie — restart shippuje cudzą committed-flag-ON pracę. ⏳ ZOSTAJE OSOBNO (protokół+ACK): feature „pin lokalizacji" w konsoli + walidacja ulica↔miasto 363 pary (offline).
```

### C5 → `autonomy-readiness-2026-06-30.md` (dopisz „DEPLOY LIVE 30.06 + next-steps + rollback") — CZĘŚCIOWO

```
✅ KROK 1 ZROBIONY 30.06 (Adrian ACK cel ~62%/plaster D): profil bramki (G2 classifier+G12 margin zdejmowane flagą AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO/_REQUIRE_MARGIN default strict; G13 shift_end+G14 parser_degraded jawne; D=pool≥2 i D'=pool≥3 liczone OBOK strict→shadow_decisions; testy 7/7; executor OFF=zero zmiany live). Kod w commit 78401ed (wyścig wspólnego indeksu git multi-sesji → prowenancja 976afbf). ✅ DEPLOY LIVE 30.06 ~10:13 (Adrian „puszczaj wszystko live", peak override): dispatch-shadow ZRESTARTOWANY (telemetria D/D' się wypełnia, ENABLE_AUTO_ASSIGN=0) + PRZYCISK „Autonomia Ziomka WŁ/WYŁ" LIVE w gps.nadajesz.pl/admin (commit konsoli d42444a branch coordinator-console, endpoint /api/coordinator/auto-assign 401-gated, most auto_assign_flag.py→flags_admin, deploy_panel.sh backend+admin, backup admin-panel.bak-20260630-101259). ⚠⚠ PIERWSZE wciśnięcie „Włącz" = NIEPRZETESTOWANE E2E gastro_assign → MUSI być nadzorowane (1. auto-assign na realnym zleceniu, off-peak). NASTĘPNE (osobny ACK): (1) ½-1 dzień pomiaru would_auto_d≈125/dzień, (2) monitor+stop-loss PRZED ON, (3) kontrolowane 1. wykonanie max_per_hour=1, (4) flip profilu D w flags.json (REQUIRE_CLASSIFIER_AUTO=false+REQUIRE_MARGIN=false+MIN_POOL=2), (5) ramp. Rollback: przycisk WYŁ / flagi profilu OFF(default) / .bak-pre-auton02-20260630-093549. Skrypty: scratchpad/{analiza_29,decompose_57,physical_compare,slice_size,ceiling}.py.
```

### C6 → `nadajesz-kontroling-wynagrodzenia-2026-06-20.md` (zweryfikuj obecność wniosku; jeśli brak — dopisz) — CZĘŚCIOWO

```
KONTROLING GASTRO 24.06 — WNIOSEK reconciliacji per-kurier: dopóki kurierzy robią gastro+paczki, per-kurier (pełna płaca brutto = gastro+paczki+Epaka+ZUS) i P&L gastro-only (col15 = alokacja GASTRO) NIE reconciliują — agregat col15 = poprawny model kosztu GASTRO (exact). Σper-kurier 83 156 > Σcol15-gastro 80 498 o 2 658 = praca paczkowa wielocentrowych → ZUS-catch-all schodził na MINUS (−2058) → bezsens. Implikacja maj/cze: per-kurier=rejestr płac (pełne, wszystkie centra); P&L GASTRO maj/cze koszt=alokacja gastro (col15), NIE Σper-kurier (zawyży o paczki). Maj OK (Σpłac 81 900, 36 kur.). Czerwiec częściowy (Σ 41 466, 22 kur.) — 13 kur. 0 zł = luki arkusza czerwca → Adrian dokończy na panelu. ⚠ dwa Krystiany: Bruliński (cid 61, koord do końca marca), Drząszcz (cid 488, kurier). Biuro=Rafał Suchocki; Łukasz Szmyga/Martyna Kołosowska→paczki/Epaka. [Uwaga: kanon metodyki 02.07 = kontroling-zrodla-metodyka.md może to już supersede — zweryfikować przed dopisaniem.]
```

## D. Pliki zwinięte z indeksu (nie linkowane wprost) + blanket na log sesji

**4 pliki batche 13.06** — istnieją jako pełne pliki-tematy, w indeksie zwinięte do wskaźnika `→ sprint_timeline.md` (są to logi runu autonomicznego, w pełni opisane w sprint_timeline + własnych plikach + backupie):
- `autonomous-run-2026-06-13.md`, `auton-batch-2026-06-13.md`, `auton-batch2-2026-06-13.md`, `sprint-a-batch-2026-06-13.md`.
Jeśli reviewer woli 0 zwinięć: dopisać z powrotem do linii „Powiadomienia/inne" jako `· batche [A](autonomous-run-2026-06-13.md)/[b](auton-batch-2026-06-13.md)/[b2](auton-batch2-2026-06-13.md)/[sa](sprint-a-batch-2026-06-13.md)` (koszt ~110 B → indeks ~17,05 KB, wciąż <17,4 KB). Wtedy dociąć ~110 B parentheticali reguł (patrz `12-MEMORY-PROPOZYCJA.md` uwaga o marginesie).

**Blanket na „Log sesji" (stare linie 118-253, ≈60 paragrafów):** każdy paragraf był rozbudowanym STRESZCZENIEM sesji z linkiem do dedykowanego pliku-tematu na końcu. W kompakcie zostają jako 1-linijkowce w sekcjach tematycznych. Pełna treść paragrafów:
- jest zachowana w `MEMORY.md.bak-pre-shrink-2026-07-03` (backup shrinku) oraz w `git log memory/`;
- powinna (z założenia architektury pamięci) już być w pliku-temacie danego wpisu — weryfikacja per-plik = przy następnej edycji tego pliku (nie blokuje kompaktu, bo backup+git trzymają oryginał).
Pliki-tematy o celu GENERYCZNYM (gdzie warto sprawdzić pokrycie przy okazji): wpisy „RECON-WERYFIKACJA / DŁUG TECH cross-projekt / SPRINT 19-20.06" kierowały do `todo_master.md` (230 KB) i `sprint_timeline.md` (667 KB) — te dwa pliki są ogromne i niemal na pewno pokrywają, ale to jedyne miejsca warte spot-checku.

## E. Podsumowanie przeniesień
- Bloki DO DOPISANIA/WERYFIKACJI: **6** (C1-C6) — pełna treść verbatim powyżej.
- Pliki zwinięte: **4** (batche 13.06) — istnieją, dostępne przez sprint_timeline+git.
- Blanket: log sesji (≈60) — backup+git+pliki-tematy.
- **0 plików-tematów usuniętych; 0 wiedzy skasowanej.** Wszystkie 244 pliki nadal na dysku i w git.

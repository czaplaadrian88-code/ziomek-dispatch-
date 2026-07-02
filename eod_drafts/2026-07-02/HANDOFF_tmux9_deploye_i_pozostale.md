# HANDOFF → sesja tmux 9 (02.07 rano) — deploye, restarty i pozostałe zadania po nocy tmux 8

**Od:** sesja tmux 8 (L1.2+L2.2/L2.3+D.3, raport: `SESJA_tmux8_RAPORT_L12_L22_D3.md` w tym katalogu; pamięć [[l12-l22-d3-sesja-tmux8-2026-07-02]]). **Masz GO Adriana na pracę wg tego handoffu.** Restarty/flipy silnika NADAL za jego ACK — Twoim zadaniem jest też PRZYGOTOWAĆ mu na końcu prostą listę instrukcji (wzór na dole).

## ⛔ ZANIM zaczniesz (ETAP 0, nie pomijaj)
1. Przeczytaj: raport tmux 8 (jw.) + `D3_RECON_migracja_env_frozen_flags.md` + memory relay [[ziomek-unified-audit-2026-06-30]] FAZA 3 + [[ziomek-change-protocol]] (PROMPT przy każdej zmianie silnika).
2. `tmux ls` + `git log -5` — równolegle działała sesja „fala 1 audytu 2.0" (commity `aab1e17..d20bd27`: watchdog/cron_health, staging 10 drop-inów, perf-SLO w canary za flagą OFF). **Jej tracker ma WŁASNĄ listę deploy-za-ACK** (`git show d20bd27 --stat`) — zrekonsoliduj z tą listą, nie dubluj.
3. Testy bazowe: pełna regresja była zielona po `b8cdd35`: **3781 passed / 0 failed / 11 xfailed**. To Twój baseline (uwaga: 2 testy food-age migoczą xfail↔xpass — nie-strict, znane).

## 🔴 DO ZROBIENIA — deploye/restarty (WSZYSTKO za ACK Adriana, off-peak >14:00)
1. **Zbiorczy restart `dispatch-shadow`** — aktywuje w długo-żyjącym procesie: L2.2 (klasyfikacja data_poison/real_bug + serializacja `v328_fail_causes`) + L1.1-serializer już aktywny od 01.07 + wszystkie nocne zmiany tooli (oneshot-timery już je mają, bo świeży proces per tick). Procedura: py_compile+import → git log -3 (kolizje sesji) → ACK → restart 1 serwisu → journal 10 min. NIE dotykaj dispatch-telegram.
2. **Po restarcie — weryfikacja L2.2 LIVE:** po pierwszych świeżych decyzjach `grep -c '"v328_fail_causes"' scripts/logs/shadow_decisions.jsonl` na nowym oknie (>0; wartość None gdy czysto = OK). Wpisz wynik do [[l12-l22-d3-sesja-tmux8-2026-07-02]].
3. **(Opcjonalnie, osobna decyzja Adriana) flip `ENABLE_V328_POISON_ALERT=true`** w flags.json — hot-reload, bez restartu; zbiorczy alert data-poison (okno 30 min / próg 5 / realert 30 min). Rollback = ta sama flaga na false.
4. **Sprawdź listę deploy-za-ACK fali 1** (pkt 2 wyżej — drop-iny cron_health/watchdog itd.) i połącz z restartem z pkt 1 w JEDEN zbiorczy deploy, żeby nie robić dwóch restartów.

## 🟡 DO ZROBIENIA — weryfikacje poranne/dzienne (read-only, bez ACK)
5. **Weryfikacja L1.1 (zobowiązanie fali L0):** `grep -c '"eta_source"' scripts/logs/shadow_decisions.jsonl` na oknie PO restarcie 01.07 21:27 (>0) — jeśli inna sesja już zrobiła, tylko odnotuj; wynik → [[serializer-completeness-l11-2026-07-01]].
6. **Strażnicy po dniu pracy:** odczytaj `dispatch_state/pickup_floor_guard.jsonl` (baseline naruszeń plan/recheck_leak w peaku) + `carried_first_guard.jsonl` (udział no_position po drop-inie env — miał spaść z 91,7%). To jest WARUNEK startu fali **L3 plan_recheck** (potrzebny zdrowy strażnik + ~dzień pomiaru — od 22:31 01.07, czyli dziś wieczorem najwcześniej).
7. **Bramka O2:** review-timer 07:00 i at-168 08:00 dziś ODPALONE (na starym, spójnym kodzie review — celowo). Odczytaj poranny werdykt bundle_calib (`dispatch_state/` + log timera) i zrelacjonuj Adrianowi w kontekście flipu #2 (bramka z top10, [[top10-progressive-potential-2026-06-29]]); sam flip = pełny protokół + ACK. Narzędzie review jest OD TERAZ przepięte na żywy sla (`b8cdd35`) — następne runy będą miały pełny klik-fallback.

## 🟢 KOLEJKA PRAC (osobne sprinty, protokół ETAP 0→7 per sztuka)
8. **L3 plan_recheck** (następna fala Fazy 3) — dopiero po pkt 6 (zdrowy baseline strażników). Zakres wg roadmapy L0-L8 (`FAZA1_05_roadmapa_poc.md`).
9. **D.3 fale A→D** (migracja env-frozen→flags.json, raport w tym katalogu): Fala A (12 flag neutralnych) i B (para V326 atomowo) = standardowy ACK; **Fala C i D = pod-ACK Adriana**. Przed C/D domknij OPEN-1 (czy recanon/redecide w panel-watcher wchodzą w gałęzie COMMITTED_PROPAGATION/LIVE_ETA_REFRESH) i OPEN-2 (mapa wołających parse_panel_html).
10. **Werdykt B-lite:** re-run `tools/b_route_shadow_review.py --no-telegram` — join ożył (0→322) → realne liczby do decyzji Adriana „budować B-lite czy zamknąć".
11. Drobne: `min_delivered_at_verdict.py` przepiąć na kanon przy ewentualnym re-runie · adopcja `schedule_utils.py` do repo (dziś NIETRACKOWANY nigdzie!) · 2 migoczące xfail/xpass food-age — ureguluj przy okazji flipu at#151.

## 📅 NIE RUSZAĆ / tylko odczytać po odpaleniu
- **at-200 (pt 03.07 18:10)** = checkpoint objm-lexr6 (narzędzie przepięte, bajt-parytet bramek udowodniony) i **at-201 (pt 03.07 19:00)** = werdykt L2.1 (rotation-proof od L0.3). Po odpaleniu: odczytać werdykty, wpisać do notatek tematycznych.
- **pickup-slip-review (sob 04.07 07:00)** = bramka load-aware ETA; monitor przepięty i VALIDATED.
- Obszar O2/R6/bundle_calib merytorycznie = osobny proces decyzyjny (pkt 7), nie „przy okazji".

## 📝 NA KONIEC — instrukcja dla Adriana (obowiązkowa, wzór z [[feedback-explain-before-work-plain-language]])
Po skończeniu przygotuj Adrianowi krótką listę PROSTYM JĘZYKIEM, per pozycja: **CO robimy / JAKI WPŁYW / JAK BEZPIECZNIE (rollback)** — minimum: (a) zbiorczy restart shadow (co aktywuje), (b) flip alertu data-poison tak/nie, (c) decyzja po werdykcie O2 z bramki, (d) fale A/B migracji flag, (e) decyzja B-lite. Czekaj na jego GO per pozycja — nic nie flipuj/restartuj bez ACK.

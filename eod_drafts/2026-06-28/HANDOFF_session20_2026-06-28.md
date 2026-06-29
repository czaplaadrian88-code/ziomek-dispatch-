# HANDOFF → sesja tmux 20 (od sesji 18 „Bartek/audyt", 2026-06-28)

## Skąd to się wzięło
Sesja 18 (analiza trasy Bartka na Rukoli Kaczorowskiego) wykryła łańcuch:
1. **Ziomek jest ślepy na czasówkę-w-uwagach** (twardy deadline dostawy wpisany w free-text `uwagi`, nieparsowany) — to Twoje zadanie niżej.
2. **Przyrządy-werdykty (shadow/at-joby) potrafią KŁAMAĆ** (bug4 reseq shadow = VOID: logował „brak korzyści", realny re-solve −3,6 km). Stąd uruchomiony **audyt anty-kłamstwo** = workflow **`wf_29bb4804`** (~150 agentów, runtime-oracle — odpala przyrządy vs niezależna prawda). Audyt jest W TOKU.

## ⛔ TWARDE ZASADY (masz auto-mode ON — pilnuj się, łatwo o nadgorliwą edycję)
1. **ZERO mutacji.** Żadnej edycji kodu / `flags.json` / systemd, żadnych restartów, flipów, commitów, deployów. Jedyny dozwolony zapis = **JEDEN plik spec** w `eod_drafts/2026-06-28/`.
2. **HOLD buildu (ETAP 4→7) do:** (a) zakończenia audytu **`wf_29bb4804`** i przeglądu jego lane'a *oracle-completeness* (on definiuje, czego Twój oracle walidacyjny MUSI pilnować: R6 tier-aware 35/40, czasówka-z-uwag, committed R27), **oraz** (b) **ACK Adriana**. Wcześniej — tylko read-only prep.
3. **NIE wchodź w cudze lany (protokół C1 — multi-sesja, wspólne repo+deploy):**
   - sesja **17** = reassignment_shadow / console ghost / fidelity #15 → NIE tykaj `reassignment_*`,
   - sesja **19** = reassignment `save_min` / dane,
   - sesja **15** = audyt techniczny + `ZIOMEK_PLAN_NAPRAW_I_PRIORYTETY_2026-06-28.md` (read-only),
   - moja sesja **18** + audyt `wf_29bb4804` = czyta WSZYSTKIE instrumenty.
   Trzymaj się **wyłącznie** ścieżki czasówka-z-uwag (panel_client/panel_watcher/feasibility-SLA/plan) — to nie jest niczyj inny lane.
4. **NIE ufaj werdyktom przyrządów oznaczonym `untested`/`void`** w `shadow-jobs-registry.md` → blok „🔬 STATUS KALIBRACJI" (C9/C10/C11).
5. **Najpierw przeczytaj** `eod_drafts/2026-06-28/ZIOMEK_PLAN_NAPRAW_I_PRIORYTETY_2026-06-28.md` (priorytety całości — Twoje zadanie to pozycja klasy **„B. ślepota"**, jeszcze niespeccowana) **oraz** protokół `/root/.claude/projects/-root/memory/ziomek-change-protocol.md` (wklej PROMPT, ETAP 0→7).

## TWOJE ZADANIE: czasówka-w-uwagach → live (READ-ONLY prep + SPEC)
**Dowód (sesja 18, zlecenie 484034 Sikorskiego):**
- `uwagi = "Dania \r\nPiętro 1\r\nCzasówka na 17:10"` → twardy deadline dostawy **17:10**.
- `order_type = "elastic"`, `prep_minutes = 30`, `uwagi_pickup_parsed = null`, `proposed_delivery_time = null`, `expected_delivery_by = 17:34` (= odbiór 16:59 + 35 generyczny R6 — **NIE 17:10**).
- Silnik klasyfikuje czasówkę **wyłącznie** po minutach: `panel_client.py` (~`order_type = "czasowka" if prep_minutes >= CZASOWKA_THRESHOLD_MIN else "elastic"`). Deadline z `uwagi` nie wchodzi do feasibility/scoringu/planu.
- Parser frazy **istnieje, ale tylko w shadow**: `tools/bundle_calib_shadow.py` (~`_DEADLINE_RE` / `_parse_deadline`, łapie „Czasówka na 17:10").

**Co robisz TERAZ (read-only, grepuj — linie dryfują):**
- **ETAP 0** — stan na żywo: `git log -3`/tagi, flagi EFEKTYWNE per proces (który systemd unit odpala kod + drop-iny + FLAG_FINGERPRINT, wzorzec #9), baseline `pytest tests/` ZIELONY vs baseline-count, reconcile at-joby (`atq` + STATUS KALIBRACJI). Nic nie zmieniaj.
- **ETAP 1** — źródło nie objaw: potwierdź grepem, że `_parse_deadline` żyje TYLKO w `bundle_calib_shadow`; zmapuj ŻYWĄ ścieżkę ingestu (panel_client → panel_watcher → state_machine → `orders_state`), gdzie deklarowany deadline dostawy ma wejść.
- **ETAP 3** — MAPA KOMPLETNOŚCI (wszystkie miejsca, bliźniaki RAZEM): klasyfikacja `order_type`/**nowe pole** `delivery_deadline_uwagi`, `state_machine` upsert, **3 bliźniaki SLA-anchor** (`route_simulator._count_sla_violations` + `feasibility_v2` SLA-loop ~1156 + `plan_recheck._o2_key`), plan/`scheduled_at`, serializer LOCATION A+B, każdy konsument pola.
- Zapisz **SPEC** → `eod_drafts/2026-06-28/CZASOWKA_UWAGI_PARSER_SPEC.md`:
  - problem + dowód (powyżej),
  - mapa kompletności (tabela miejsce→dotknięte?),
  - **fix ADDITIVE** (nowe pole `delivery_deadline_uwagi`; **NIE nadpisuj** `order_type`/`czas_kuriera` — wzorzec #8 „pole display bywa zmienną decyzyjną"; udowodnij grepem każdego konsumenta),
  - **jak walidować** (oracle: realny `delivered_at` vs deadline; ⚠ **czego oracle musi pilnować dopisze audyt `wf_29bb4804` — ZACZEKAJ na to przed budową**, inaczej powtórzysz błąd „geometryczne optimum łamie czasówkę" z case Bartka).

**Czego NIE robisz:** nie piszesz kodu parsera w silniku, nie ruszasz `panel_client`/`feasibility`/`plan`, nie flipujesz flag, nie restartujesz. Tylko spec + mapy.

## KOLEJKA (po audycie — NIE teraz)
- **czasówka-w-uwagach build** (ETAP 4→7) — po audycie + przegląd oracle-completeness + ACK.
- **bug4 reseq shadow — fix POMIARU** (C9: czytaj `p.sequence` nie sort-timestampów; wyklucz fikcyjne pickupy odebranych; inwariant `delta≥0`) — **POD AUDYTEM, NIE startuj**; audyt dostarczy spec/dowód.
- **bundle_calib O2 kalibracja** — **w audycie** (`wf_29bb4804`), nie dotykaj.

## Gdy skończysz prep
Jedna wiadomość do Adriana: „spec czasówka gotowy, HOLD do wyniku audytu wf_29bb4804 + ACK". **Nie startuj buildu sam.** Wątpliwość co do priorytetów/kolizji → pytaj Adriana, nie zgaduj.

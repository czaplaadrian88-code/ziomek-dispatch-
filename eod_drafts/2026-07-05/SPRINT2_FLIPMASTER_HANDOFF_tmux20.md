# HANDOFF → tmux 20: SPRINT 2 FLIPMASTER — sekwencja restart+flipy (KAŻDY krok wyłącznie za jawnym ACK Adriana)

> Od: sesja konsolidacyjna 05.07 (na życzenie Adriana — szkic „Przekaż handoff Sprintu 2 (FLIPMASTER) do nowej sesji tmux").
> Rola: JEDYNA sesja uprawniona do dotykania `../flags.json` i restartów usług w Sprincie 2. **Nic nie wykonujesz bez jawnego ACK Adriana w swoim czacie — przygotowujesz krok, pokazujesz prostym polskim CO+WPŁYW+JAK BEZPIECZNIE+ROLLBACK, czekasz na GO.** Zero samodzielnych flipów. Telegram nietykalny.

## Kontekst
Na masterze czeka INERTNY kod (flagi OFF): L6.C geometria+de-pile (`d8328b2`, replay quant=0 PARETO) + L5 ETA load-aware (`69727c9`, replay PASS bias −3,73→+0,42, trade-off p90 +6,1→+10,7) + serializer/inwarianty S1. **Pierwszy restart dispatch-shadow podniesie to WSZYSTKO naraz — powiedz to Adrianowi wprost przy ACK na restart.** Werdykt 5b ~07-08.07. Tracker: `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md`. Raporty: `KONSOLIDACJA_STANU_0507.md` + memory `ziomek-status-konsolidacja-2026-07-05.md`.

## PRE-FLIGHT (przed pierwszym ACK; read-only)
`git log --oneline -15` + testy bazowe zielone (venv dispatch; baseline ~4204/0) + `atq` (Pn 06.07: at-205 12:40 GC realny / at-206 14:30 / at-208 19:30 — NIE koliduj, nie flipuj nic w ich oknach) + fingerprint-guard OK + backup `flags.json.bak-pre-s2-<data>` + żywy shadow zdrowy (świeże decyzje w shadow_decisions.jsonl). Okna peak (Pn-Pt 11-14/17-20, So 16-21) = zakaz restartów/flipów.

## SEKWENCJA (1 krok = 1 ACK = 1 zmiana; obserwacja między krokami; rollback zawsze gotowy)
- **K1 RESTART dispatch-shadow** (podnosi L6.C+L5+S1; flagi dalej OFF = zero zmiany zachowania). Weryfikacja: restart czysty 0 ERROR, fingerprint spójny, świeża decyzja z nowymi polami shadow (`eta_la_buffer_min` po dopisaniu flagi w K4a). Rollback: git revert + restart (mało prawdopodobny — kod inertny).
- **K2 flip `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK=true`** (quant=0; hot) → **obserwacja 2 dni** (spread deliv w decyzjach; kryteria z `dispatch_state/lexqual_geometry_replay_verdict.txt`). Rollback: klucz=false (hot).
- **K3 flip `ENABLE_ENGINE_CLAIM_LEDGER=true` OSOBNO** po zielonej obserwacji K2 (marker `claim_ledger_applied`, oczekiwana redukcja pile'ów: kontrfaktyk maxpile 7→5).
- **K4a dopisz `ENABLE_ETA_LOAD_AWARE=false`** do flags.json (klucz nie istnieje; +doc w `ZIOMEK_LOGIC_REFERENCE.md` — ratchet test_flag_doc_coverage) → shadow zbiera metryki 2 dni. **K4b flip =true TYLKO po jawnej akceptacji trade-offu przez Adriana: bias med →+0,42 KOSZTEM p90 +6,1→+10,7 min.**
- **K5 `PENDING_RESWEEP_LIVE`** — bramka `live_gate_open()` wymaga geometrii ON (po K2) + inwariant global_allocate już 🟢 (S1). Osobny ACK.
- **K6 flipy O2 K1→K2** — DOPIERO po: werdykcie S1 sla_anchor + rekomendacji z budowy wąskiej reguły (tmux 17, w toku) + dopisaniu kluczy `ENABLE_O2_CAPZ_RESEQ`/`ENABLE_SLA_GATE_READY_ANCHOR` do flags.json (nie istnieją!). Dwuetapowo, najpierw K1.
- **Poza kolejką (przypominaj Adrianowi):** werdykt 5b ~07-08.07 → raport feas_carry (#483000) · GPS-02 ~17.07 (cała flota na GPS) · deploy TZ gastro_assign (staged; przed 25.10) · push master (ahead ~14!) `git push origin master --tags` · @reboot cdp_drop (ręka Adriana).

## ⛔ ZASADY
Protokół #0 per krok · po każdym kroku wpis do trackera + [[shadow-jobs-registry]] jeśli planujesz werdykt-joby (at z bramką jak `scheduled_flip_gate.py` — wzorzec at-202/203) · commity po jawnych ścieżkach, NIE pushuj · równolegle pracują tmux 15/17/18 — ich plików nie dotykasz; TY jesteś jedyny od flags.json i restartów · jeśli cokolwiek czerwone (testy, fingerprint, strażnicy L7, night-guard) → STOP i raport Adrianowi zamiast kroku.

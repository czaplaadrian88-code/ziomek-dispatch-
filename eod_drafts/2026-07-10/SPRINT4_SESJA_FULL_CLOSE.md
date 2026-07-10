# SPRINT 4 + FOLLOW-UPY — ZAMKNIĘCIE SESJI (2026-07-10, tmux 55, lider Fable 5)

**Stan końcowy: master = `72ae4d5` (== origin), suita z kanonu 4847 passed / 27 skipped / 10 xfailed / 0 failed, wszystkie serwisy active, parser_health healthy/v2/anomaly=False. WSZYSTKO Z TEJ SESJI JEST NA MASTER I (tam gdzie dotyczy) LIVE.**

## Chronologia rund (wszystko za jawnymi ACK Adriana)

| Runda | Co | Commity na master |
|---|---|---|
| 1. SPRINT 4 (Z-P1-05/Z-P1-07/Z-P2-07 Faza A) | identity/ + rejestr 504 flag + hermetyzacja suity; 39 nowych plików, 0 kolizji z S2/S3, 3× APPROVE reviewera | merge `270c21a` |
| 2. Follow-upy ACK #2 | kuracja rejestru 504/504 + fix panel_packs u źródła (3 testy z kwarantanny) | `94978b3`+`afe56db` |
| 3. „Po kolei jak rekomendujesz" | dane 504/543 + backfill names 19→0 + onboarding 5 plików (`c73fd5f`) · subprocess-guard sitecustomize (`cf3e4cb`) · USE_V2_PARSER dual-carrier inertny (`44017e1`) · identity **Faza B** delegacja 1:1 (merge `ce82f34`) | jw. |
| 4. „Flip teraz" | USE_V2_PARSER: ETAP4 (`259ac07`) + flags.json true (hot) + restart shadow/watcher + obserwacja zielona + re-seed rejestru (`72ae4d5`) | jw. |

## Co jest LIVE (i jak zweryfikowano)

- **Parser v2 = kanon globalny przez flags.json** (`USE_V2_PARSER: true`, hot-reload). Werdykt: parser_health healthy/v2, 0 błędów w journalach shadow/watcher/czasówka/plan-recheck, FLAG_FINGERPRINT shadow `USE_V2_PARSER=1`. Rollback hot = klucz `false` (backup: `flags.json.bak-pre-usev2-flip-2026-07-10`).
- **Hermetyzacja suity aktywna dla KAŻDEGO biegu pytest na kanonie**: sandbox stanu, write/delete-guard, STRICT (suita bez dispatch_state = 0 failed), subprocess-guard (dzieci też). Testy już NIE mutują żywego `panel_packs_cache.json` ani niczego w dispatch_state/logs/flags.
- **Tożsamość:** żywy `courier_names.json` = 65/65 CID (backfill, `.bak-pre-zp105-backfill-2026-07-10`); pisownie 504→„Artsem Kmets", 543→„Darek Osmólski" (kanon grafiku); onboarding pisze 5 plików transakcyjnie. Silnik/normy/scoring zdelegowane do `identity/` 1:1 (golden 21 417 par = 0 różnic, parity live 177/177).
- **Rejestr flag:** `tools/flag_lifecycle_registry.json` 504 wpisy, kuracja 504/504 (chroniona przy re-seed `--merge`), checkery repo+live exit 0.
- ⚠ **Restart-coverage Fazy B:** dispatch-shadow i dispatch-panel-watcher biegną na nowym kodzie (restart 15:39 przy flipie). `dispatch-sla-tracker` i `courier-api` biegną na kodzie sprzed Fazy B — **semantycznie identycznym** (delegacja 1:1, golden=0), podchwycą przy najbliższym naturalnym restarcie; ŻADNE działanie nie jest wymagane.

## Gałęzie na origin (audyt trail)
`sprint4/{z-p1-05-identity, z-p1-07-flags, z-p2-07-hermetic, integration, fix-panel-packs-test, flag-curation, identity-faza-b}` — wszystkie zmergowane do master.

## Świadomie ODŁOŻONE (wymagają osobnych decyzji/pomiarów — NIE są zaległością wykonawczą)
1. Unifikacja profili resolverów (worker ×10/×5 vs panel_roster ×10/×10) — zmiana zachowania, wymaga pomiaru+ACK.
2. Krok 4 identity: przełączenie czytelników PLIKÓW (kurier_ids/courier_names load-sites w hot path silnika) na registry.
3. Konsolidacja zdenormalizowanego `courier_api.db` (5 tabel z kopią courier_name).
4. Migracja pozostałych flag 1b (intencjonalne per-process, np. ENABLE_PANEL_BG_REFRESH shadow=1/watcher=0 — NIE nadaje się do globalnego flags.json) — skurowane w rejestrze jako kandydaci z datą review 2026-08-10.
5. Kuracja merytoryczna rejestru przez Adriana (ownerzy/review-daty = seed polityką, do przejrzenia przy okazji).
6. Rozjazdy pełnych nazwisk 370 (Kuba/Jakub — zamierzone zdrobnienie) i 376 (ascii-skrót vs diakrytyka) — udokumentowane, decyzja czy ujednolicać.

## Rollbacki (wszystkie przygotowane)
- USE_V2_PARSER: flags.json `false` (hot, ≤1 tick) lub backup pliku flag.
- Hermetyzacja: `rm dispatch_v2/conftest.py` (1 plik).
- Identity Faza B: `git revert a8b3225 955dab2 0b4b096`.
- Dane: `.bak-*-2026-07-10` przy każdym żywym pliku.
- Kuracja/rejestr/pakiety: `git revert` (dane+tooling, zero runtime).

## Wiedza dla następnych sesji
- Pełne handoffy: `SPRINT4_HANDOFF.md` (sprint) + per-task `SPRINT4_ZP10*_RAPORT.md` + `SPRINT4_ZP105_FAZAB_RAPORT.md` + ten plik (całość dnia).
- Memory: `sprint4-kontrakty-ci-2026-07-10` (indeks w MEMORY.md) + statusy w `todo_master.md` + wpis w `sprint_timeline.md`.
- Statusy kart: `ZIOMEK_BACKLOG.md` (Z-P1-05/Z-P1-07/Z-P2-07 = DONE z datą i zakresem odłożeń).
- Nowe narzędzia do codziennego użytku: `python -m dispatch_v2.identity.report [--parity]` (kolizje/braki/parytet), `tools/flag_lifecycle_check.py [--live]` (dryft flag), `HERMETIC_STRICT=1 pytest tests/` (dowód hermetyczności), `tools/flag_lifecycle_seed.py --merge --out tools/flag_lifecycle_registry.json` (re-seed BEZ utraty kuracji — ⚠ zawsze `--merge` na kanonicznym pliku).

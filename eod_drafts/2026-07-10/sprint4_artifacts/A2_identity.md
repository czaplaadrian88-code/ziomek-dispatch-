# A2 — Mapa tożsamości kuriera (READ-ONLY) — pod Z-P1-05 Faza A

Repo HEAD: `3c43573` (2026-07-10 08:19). Wszystko poniżej = odczyt żywego stanu + repo, zero zmian.

## A. TABELA ŹRÓDEŁ (plik → schema → liczność → PISARZE → CZYTELNICY)

Ścieżki żywe: `/root/.openclaw/workspace/dispatch_state/` (poza `daily_accounting/kurier_full_names.json`, który jest W REPO).

| Plik | Schema | Liczność | PISARZE (write) | CZYTELNICY (główni) |
|---|---|---|---|---|
| **kurier_ids.json** | `{alias:str → cid:int}` | 121 aliasów → 65 CID | `courier_admin.add_new_courier`; `migrations/migrate_couriers_2026-05-05` (jednorazowa „no-dots") | courier_resolver, common.py, courier_info, manual_overrides, shift_notifications/worker, telegram_approver, new_courier_pairing, courier_ranking, sla_tracker, gps_server, event_bus, parcel_assign, panel_watcher, daily_accounting/{main,panel_scraper}, tools/{reassignment_*,pending_global_resweep} |
| **kurier_piny.json** | `{pin:str(4) → alias:str}` | 60 | `courier_admin.add_new_courier`; `migrations/migrate_couriers` | courier_resolver, courier_info, common.py, telegram_approver, new_courier_pairing, gps_server |
| **courier_names.json** | `{cid:str → name:str}` | 46 | **BRAK żywego pisarza** (mtime 2026-06-10; legacy) | courier_resolver, manual_overrides, courier_ranking, sla_tracker, telegram_approver, new_courier_pairing, tools/{rebuild_courier_whitelist,courier_speed_build,sequential_replay,faza7_daily_kpi,build_speed_tiers} |
| **courier_tiers.json** | `{cid:str → {name, bag:{tier,cap_override}, speed, tier_label, coordinator?}}` + `_meta` | 64 CID (+_meta) | `courier_admin.add_new_courier`; `build_v319h_courier_tiers` (rebuild); `migrations/migrate_couriers` | courier_resolver, common.py, plan_recheck, dispatch_pipeline, state_machine, ml_inference, world_record, event_bus, r04_apply/evaluator, eta_residual_infer, flags_admin, telegram_approver, core/{config_reload_subscriber,broadcast_handlers}, ml_data_prep/twomodel_common, ~10 tools/ |
| **grafik_full_names.json** | `{pełne_imię:str → cid:int}` | 56 | `new_courier_pairing._ensure_grafik_full_name` (self-heal, l.185) | courier_resolver, manual_overrides, new_courier_pairing |
| **daily_accounting/kurier_full_names.json** (W REPO) | `{alias:str → pełne_imię:str}` | **55 żywy / 54 git** | `courier_admin.add_new_courier` (4. plik) | daily_accounting/main, new_courier_pairing (`cod_ok = alias in full`) |
| **shift_ignored_names.json** | `{names:[str], comment:str}` | 3 nazwiska | ręcznie (Adrian) — brak pisarza w kodzie | shift_notifications/worker, new_courier_pairing (skip) |
| **courier_whitelist_v1.json** | `{_meta, WHITELIST:[], CONDITIONAL:[{cid,name,tier,...}], ...}` | 5 kluczy | `tools/rebuild_courier_whitelist` | tools/{faza7_daily_kpi,backfill_decisions_outcomes} |
| **new_courier_pairing_state.json** | `{data:str → {paired:[], alerted:[]}}` | 7 dni (retencja 7d) | `new_courier_pairing` (idempotencja) | new_courier_pairing |
| **courier_api.db** (sqlite, strona APKI) | 13 tabel; tożsamość = `courier_id TEXT` (denormalizowany) + `courier_name TEXT` skopiowany w 5 tabelach | sessions=313, courier_status_events=2817, courier_phones=16, pin_attempts=341 | serwis `courier_api` (osobny proces, katalog `scripts/courier_api/`) | jw. |

**courier_api.db — tabele:** coordinator_messages, courier_availability(+_audit), courier_payment_overrides, courier_phones, courier_status_events, gps_history, pin_attempts, schedule_ack, sessions, shift_offer_claims, vehicle_issues. Klucz tożsamości = `courier_id` jako **TEXT** (nie int, nie FK); brak kanonicznej tabeli kuriera — `courier_name` zduplikowany w sessions/courier_status_events/courier_phones/courier_availability(+audit). PIN-auth: `pin_attempts.pin_hash`+`pin_last2`, sesje `sessions.token→courier_id`. **To OSOBNA, denormalizowana powierzchnia tożsamości** poza plikami JSON silnika.

## B. ŚWIEŻE LICZBY vs karta (121 / 65 / 54 / 20)

| Metryka | Karta | Dziś (żywo) | Uwaga |
|---|---|---|---|
| Aliasy łącznie (kurier_ids) | 121 | **121** | zgadza się |
| Unikalne CID | 65 | **65** | zgadza się |
| CID z >1 aliasem | 54 | **54** | zgadza się (wzorzec: skrót panelu + pełne imię grafiku) |
| CID bez wpisu w courier_names | 20 | **19** | delta −1; courier_names NIE jest utrzymywany przez onboarding (mtime 06-10), rośnie z każdym nowym kurierem |
| CID bez tieru (courier_tiers) | — | **1** | tylko `26` Koordynator (wirtualny) |
| Duplikaty PIN | — | **0** | 60 PIN-ów, 0 kolizji, wszystkie aliasy PIN-ów rozwiązywalne przez kurier_ids |
| **Kolizja: znormalizowany alias → >1 CID (twarda)** | — | **0** | migracja „no-dots" trzyma; ani dots-only, ani dots+diakrytyki nie kolidują |
| Rozjazd pełnego imienia między źródłami | — | 54 „różne" ale tylko **2 realne konflikty** | reszta = skrót panelu vs pełne imię grafiku (ten sam człowiek) |

**Realna powierzchnia „kolizji" NIE jest w kluczach dicta (0), tylko w score-based fallbacku + gołych kluczach-imionach:**

**8 gołych kluczy-imion (mina ciché mis-resolucji)** w kurier_ids: `Adrian→21, Koordynator→26, Krystian→61, Patryk→75, Gabriel→179, Marek→207, Edward→267, Grzegorz→500`. Każdy nowy kurier o tym imieniu jest cicho pochłaniany przez goły klucz w score-fallbacku (score=1) i (do 06.07) zatruwał self-heal grafik_full_names. To była realna produkcyjna kolizja (patrz przykłady).

**3 przykłady (skrócone nazwiska):**
1. **Goły klucz** — `Gabriel→179` (G. Ostapczuk) cicho pochłonął nowego `Gabriel P.` (cid 541) 06.07: zero alertu, self-heal wpisał złe cid do grafik_full_names. Naprawione doraźnie; ryzyko dotyczy wszystkich 8 gołych kluczy.
2. **Realny konflikt cross-source** — cid **370**: grafik = „Kuba O.", panel/courier_names/tiers = „Jakub OL"; kurier_ids trzyma OBA aliasy („Jakub Olchowik" + „Kuba Olchowik"). „Kuba" to zdrobnienie „Jakub" → first-name różny stringowo, przeżywa TYLKO dzięki jawnemu podwójnemu aliasowi (score-fallback by nie połączył).
3. **Diakrytyka** — cid **376**: „Paweł SC" (ascii) vs „Paweł Ściepko" (Ś). Normalizacja NIE składa diakrytyki, więc `SC`≠`ści…` → score-fallback = 0; działa wyłącznie przez exact-match jawnego klucza „Paweł SC". Każdy nowy kurier z Ś/Ł/Ż w nazwisku + skrótem ascii ma tę samą lukę.
4. **Braki courier_names** (19 CID: 492, 523–543 i in.) — wszyscy onboardowani po 06-10; onboarding pisze 4 pliki, ale courier_names NIE jest wśród nich.

## C. KONTRAKT NORMALIZACJI ALIASÓW (dziś — musi być odtworzony 1:1)

**Jedna funkcja, 6 kopii inline (identyczne):**
```
_norm(s) = (s or "").strip().rstrip(".,;:").lower()
```
- `courier_info.py:27-28` `_norm`
- `panel_roster.py:141-143` `_norm_token`
- `telegram_approver.py:1921-1922` `_norm`; też inline `2770`, `2774`
- `courier_resolver.py:1259, 1285, 1289, 1301` (inline, panel_packs)
- `common.py:1259` (inline, panel_packs)
- `shift_notifications/worker.py:118` `resolve_cid` (lower() + startswith)

**Reguły kontraktu:** (1) strip whitespace; (2) `rstrip(".,;:")` — obcina kropkę skrótu („Ch." → „ch"); (3) `lower()`; (4) **BEZ składania diakrytyki** (Rafał→rafał, Ś zostaje ś — patrz mina cid 376). „No dots" od 2026-04-24.

**Warstwa dopasowania nad normalizacją (2 bliźniacze implementacje — do zunifikowania w Fazie B):**
- `shift_notifications/worker.py:resolve_cid` — exact(case-sens) → exact(case-insens) → score-fallback: first-name MUSI się zgadzać; `s_last.startswith(a_last)`→`len(a_last)*10`; `a_last.startswith(s_last)`→`len(s_last)*5`; goły alias-imię→`score=1`; remis→ambiguous (None); all-zero→None.
- `panel_roster._score` (l.155-181) — pierwsze imię musi się zgadzać; prefiks nazwiska **dwukierunkowo** `len(prefix)*10` (oba kierunki ×10, inaczej niż worker ×10/×5); goły first-name→1. `match_name_to_cid`: remis → `ambiguous`.
- `courier_info.resolve_courier_query` — cyfry 3-7 → cid; exact-norm; substring-norm → lista ambiguous.
- `new_courier_pairing._resolve_cid_trusted` (l.252) — resolve na kurier_ids **bez** kluczy jednowyrazowych (bariera „bare-key strict", flaga `NEW_COURIER_AUTOPAIR_BARE_KEY_STRICT` default ON w kodzie).

**Kanon do odtworzenia:** CID jest KLUCZEM; w JSON wartości to int, w courier_api.db `courier_id` to TEXT → registry musi traktować cid kanonicznie jako `str`. Rozwiązywanie musi zachować kolejność exact→exact-ci→score i zachowanie remis=ambiguous.

## D. PROCEDURA ONBOARDINGU DZIŚ (pliki dotykane)

**Ścieżka automatyczna** (`new_courier_pairing.scan_once`, timer `dispatch-new-courier-watch` co 30 min): grafik → cid z rosteru gastro `list-users` → `courier_admin.add_new_courier` → DM PIN → `verify_courier_wired`.

**`courier_admin.add_new_courier(cid, full_name)` — atomowo pisze 4 pliki** (temp+fsync+rename, fcntl.LOCK_EX, backup `.bak-pre-add-<cid>-<data>`, rollback na partial-fail):
1. `dispatch_state/kurier_ids.json` — dodaje **DWA** aliasy: `kids[alias]=cid` (skrót „Marcin By") **i** `kids[full_name]=cid` (pełne z grafiku) → stąd wzorzec 2 aliasy/CID
2. `dispatch_state/kurier_piny.json` — nowy bezkolizyjny PIN → alias
3. `dispatch_state/courier_tiers.json` — `tier="new"`, cap_override {off_peak:1, normal:2, peak:2}
4. `dispatch_v2/daily_accounting/kurier_full_names.json` — `full[alias]=full_name`

**Plus poza `add_new_courier`:** `new_courier_pairing._ensure_grafik_full_name` pisze **`grafik_full_names.json`** (5. plik, self-heal cid↔imię) + `new_courier_pairing_state.json` (idempotencja).

**Ślad `.bak-pre-add-543-2026-07-09` potwierdza:** kurier_ids + kurier_piny + courier_tiers (w dispatch_state) + kurier_full_names (w daily_accounting) = 4 backupy. **courier_names.json NIE jest dotykany** (stąd 19 braków). Derywacja aliasu: `derive_alias` = `<Imię> <2 litery nazwiska>` bez kropki. Ręcznie: `/nowy <cid> <imię>` (telegram_approver) lub `/nowy <imię>` (auto-resolve).

**Offboardingu brak jako narzędzia** — dziś to ręczna edycja + dopisanie do `shift_ignored_names.json` (`names`). EXCLUDED_CIDS (`daily_accounting/config.py:7`) = `{21 Adrian, 23 Rutcom, 26 Koordynator, 61 Krystian, 207 Marek, 284 Mateusz L, 354 Filip P, 426 Mykyta K, 476 Antoni Tr, 498 Kamil Dr}` — wykluczenia rozliczeń, edytowane ręcznie. Koordynator (cid 26) = wirtualny (`is_coordinator` z flagi `coordinator` w courier_tiers; `observability/data_alerts.py` domyślnie wyklucza „26").

## E. REKOMENDACJA — MINIMALNY PAKIET `dispatch_v2/identity/` (Faza A)

**Przestrzeń `dispatch_v2/identity/` jest WOLNA** — nie istnieje, zero importów/referencji w repo. Faza A = nowy pakiet czytający istniejące źródła, **zero dotykania istniejących modułów**.

Minimalny zestaw (7 plików + testy):
- `identity/__init__.py`
- `identity/normalize.py` — JEDNO źródło kontraktu: `norm(s)=(s or "").strip().rstrip(".,;:").lower()` (BEZ diakrytyki) + resolver score-based odtwarzający `worker.resolve_cid`/`panel_roster._score` 1:1 (exact→exact-ci→score, remis=ambiguous). To kanon, który Faza B podmieni pod 6 kopii inline.
- `identity/sources.py` — stałe ścieżek 10 źródeł + read-only adaptery (każde źródło → surowe wpisy). CID kanonicznie jako `str`.
- `identity/schema.py` — `@dataclass CourierRecord`: `cid:str` (KLUCZ, niezmienny), `aliases: {source: [wersje]}` (panel/gps/grafik/app — wersjonowane), `full_name`, `tier`, `pin_ref`, `active`, `added_at`, `excluded`, `is_coordinator`. + JSON-schema walidacyjny.
- `identity/registry.py` — read-only builder: scala 10 źródeł + courier_api.db w rekordy per CID; API `resolve(name)`, `by_cid(cid)`, `all()`. Fail-open jak dziś.
- `identity/collisions.py` — walidator kolizji/braków: (a) znormalizowany alias→>1 CID; (b) zbiór 8 gołych kluczy-imion (poison); (c) rozjazd pełnego imienia cross-source (z odsianiem skrót-vs-pełne); (d) CID bez courier_names / bez tieru; (e) duplikaty PIN; (f) rozjazd git-vs-live daily_accounting/kurier_full_names.
- `identity/report.py` (lub `tools/identity_report.py`) — raport braków nazw/tierów + kolizji (to czego karta wymaga: „raport brakujących nazw/tierów").
- `identity/onboarding.py` — narzędzie onboard/offboard **komponujące** `courier_admin.add_new_courier` (NIE reimplementujące zapisu) + dry-run diff po 5 plikach; offboard = plan wpisu do shift_ignored_names/EXCLUDED_CIDS. Zapis nadal przez sprawdzony `courier_admin` (atomowy, z backupami) — „bez zmiany CID i historycznych rozliczeń".
- `tests/test_identity_registry.py` — oracle na realnych casach: 370 (Kuba/Jakub), 376 (diakrytyka Ś), 541/179 (goły klucz), 19 braków courier_names, parytet resolvera vs worker/panel_roster.

**DO ODŁOŻENIA na Fazę B (migracja czytelników — dotyka istniejących modułów):**
- Podmiana 6 kopii inline `_norm` → `identity.normalize` (jedno źródło).
- Zunifikowanie DWÓCH rozbieżnych resolverów score-based (`worker.resolve_cid` ×10/×5 vs `panel_roster._score` ×10/×10) w jeden.
- Przełączenie czytelników (courier_resolver, common.py, manual_overrides, telegram_approver, daily_accounting, shift worker) z surowego JSON na registry.
- `courier_admin.add_new_courier` pisze PRZEZ registry; retire/rebuild legacy `courier_names.json` (dziś bez pisarza) — albo uzupełnić onboardingiem, albo formalnie wycofać.
- Konsolidacja denormalizowanego `courier_name` w courier_api.db (5 tabel) do referencji po `courier_id`.

**Ryzyka do zachowania 1:1 w registry:** goły-klucz poison (8 imion), brak składania diakrytyki, podwójny alias przy onboardingu (skrót+pełne), cid=26 wirtualny + EXCLUDED_CIDS, cid jako str vs int/TEXT między JSON a sqlite.

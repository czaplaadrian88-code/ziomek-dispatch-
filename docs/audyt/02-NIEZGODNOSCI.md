# 02 — NIEZGODNOŚCI: obowiązujące dokumenty vs stan faktyczny (Agent C)

**Data:** 2026-07-03 · **Zakres:** kanon reguł/architektury Ziomka (dispatch_v2) + pamięć `memory/` + `/root/CLAUDE.md` + `workspace/CLAUDE.md` vs. rzeczywistość (flags.json, drop-iny systemd + EnvironmentFile, `systemctl`, `atq`, kod, ścieżki).
**Metoda:** WYŁĄCZNIE READ-ONLY (git log/show, systemctl status/show/cat/list-timers, journalctl -n, grep, stat). ZERO zapisu poza tym raportem. Sekrety = tylko ścieżka:linia.
**Rola audytu:** katalogować do decyzji Adriana — **niczego nie rozstrzygam ani nie naprawiam.**

> **⚠ Landmine metodyczny (wzorzec #9 protokołu) potwierdzony na żywo:** `systemctl show -p Environment nadajesz-panel` pokazuje TYLKO 3 flagi inline — realne flagi panelu (44) siedzą w **`EnvironmentFile=.../flags.systemd.env`**, którego `show -p Environment` NIE renderuje. Weryfikacja flag panelu samym `show -p Environment` = fałszywy obraz OFF. Ten raport czyta EnvironmentFile wprost.

---

## 1. ŹRÓDŁA KANONU — mapa i status własny

| Plik | Rola | Ost. zmiana (git/mtime) | Status wg SAMEGO pliku |
|---|---|---|---|
| `/root/.claude/CLAUDE.md` | Global PRYWATNY (auto-load, każda sesja) | mtime 2026-06-02 (322B) | brak — „Ruflo Integration" (MCP ruflo/swarm) |
| `/root/CLAUDE.md` | Global instr. (index+lookup Ziomka) | mtime 2026-07-01 22:44 (poza git) | żywy indeks; sam wskazuje „bieżący stan = memory/" |
| `workspace/CLAUDE.md` | (auto-load w hierarchii dispatch_v2) | mtime **2026-05-06** (2047B, poza git) | **BRAK adnotacji** — treść = stary „AI ROUTING SYSTEM / AIDER" |
| `dispatch_v2/CLAUDE.md` | Przykazanie #0 + reference | `76daf25` 2026-07-01 | **HYBRYDA**: głowa (l.1-9) żywa; body l.13 „STATYCZNY SNAPSHOT od 2026-05-10" |
| `dispatch_v2/ZIOMEK_ARCHITECTURE.md` | Kanon architektury (Faza 2) | `76daf25` 2026-07-01 | „**STATUS: DRAFT do przeglądu Adriana (2026-07-01)**" |
| `dispatch_v2/ZIOMEK_INVARIANTS.md` | Inwarianty + strażnicy | `c6e2c13` 2026-07-02 | „**STATUS: DRAFT do przeglądu Adriana (2026-07-01)**" |
| `dispatch_v2/ZIOMEK_DEFINITION_OF_DONE.md` | DoD 1 ekran | `76daf25` 2026-07-01 | „**STATUS: DRAFT (2026-07-01)**"; baseline „3611/2" |
| `dispatch_v2/ZIOMEK_MASTER_KB.md` | Reference historyczny | `c06434b` 2026-06-13 | „STATYCZNY od 2026-05-10" (spójne) |
| `dispatch_v2/TECH_DEBT.md` | Archiwum tech-debt | `82c4580` 2026-05-18 | „STATYCZNY od 2026-05-05" |
| `memory/MEMORY.md` | Indeks pamięci (auto-load) | git `92f984f` 2026-07-03 | żywy indeks |
| `memory/ziomek-change-protocol.md` | Protokół zmiany 0→7 | git `dc591ec` 2026-07-03 | żywy kanon |
| `memory/ZIOMEK_REGULY_KANON.md` | Reguły + „prawdziwy stan flag" | git `289d8e8` 2026-06-29 | „Status: KANON v1.0" |
| `memory/ZIOMEK_REGULY_PROSTO.md` | Wersja dla człowieka | mtime 2026-06-29 | pochodna kanonu |
| `memory/todo_master.md` | Jeden punkt wejścia zadań | git `ef7c164` 2026-07-03 | „zweryfikowane NA ŻYWO **2026-06-06**" |
| `memory/project_overview.md` | Esencja + reguły biznesowe | mtime 2026-05-24 | READ-FIRST (evergreen) |
| `memory/feedback_rules.md` | Reguły operacyjne | mtime 2026-06-14 | żywy |
| `memory/sprint_timeline.md` | Handoff + chronologia | git 2026-07-03 | żywy (CURRENT HANDOFF na górze) |
| `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` | **Tracker = źródło prawdy postępu** | mtime 2026-07-03 11:16 | żywy, wielosesyjny |
| `eod_drafts/2026-07-02/HANDOFF_po_dniu_0207_wieczor.md` | Kolejka ACK | mtime 2026-07-03 | żywy |

**Wniosek:** kanon jest ROZPROSZONY na 3 warstwy (global `/root` → repo `dispatch_v2/*.md` → pamięć `memory/*`), a warstwy niosą sprzeczne autoetykiety statusu (patrz §2, §6). `workspace/CLAUDE.md` = sierota poza mapą.

### 1a. Łańcuch CLAUDE.md AUTO-ŁADOWANY po cwd (rozszerzenie zakresu — koordynator)

Sesja z cwd=`dispatch_v2` łączy w kontekst wszystkie CLAUDE.md w łańcuchu katalogów. Stan faktyczny (`find` + `stat`):

| Ścieżka | mtime/rozmiar | Rola faktyczna | Ocena |
|---|---|---|---|
| `/root/.claude/CLAUDE.md` | 2026-06-02 / 322B | „Ruflo Integration" — każe używać MCP ruflo (`memory_store/swarm_init/agent_spawn`) + „[INTELLIGENCE] pattern" | **ORPHAN** — projekt używa pamięci PLIKOWEJ `memory/`, nie ruflo MCP (N14) |
| `/root/CLAUDE.md` | 2026-07-01 / 35KB | Żywy index+lookup | obowiązujący |
| `/root/.openclaw/CLAUDE.md` | (brak) | — | — |
| `/root/.openclaw/workspace/CLAUDE.md` | **2026-05-06** / 2KB | „ZIOMEK AI ROUTING SYSTEM" (aider/DeepSeek router) | **RELIKT** — sprzeczny z praktyką (N5) |
| `/root/.openclaw/workspace/scripts/CLAUDE.md` | (brak) | — | — |
| `dispatch_v2/CLAUDE.md` | 2026-07-01 / 90KB | Przykazanie #0 (głowa) **+ zdublowany router (ogon l.1624-1725)** | głowa obowiązująca; ogon = **RELIKT self-sprzeczny** (N13) |
| `scripts/wt-audyt/CLAUDE.md`, `wt-frozenobj/CLAUDE.md` | worktree (kopie 90KB) | kopie robocze worktree audytu | dziedziczą ogon N13 (propagacja) |
| `nadajesz_clone/ordering-site/CLAUDE.md` | (Papu) | inny projekt (`@AGENTS.md`) | poza zakresem Ziomka |

**Realny efekt:** przy pracy w dispatch_v2 sesja dostaje JEDNOCZEŚNIE: (a) `/root/CLAUDE.md` „PYTAJ Adriana + ACK", (b) `workspace/CLAUDE.md` „Adrian does NOT make technical decisions / NOT ask", (c) ogon `dispatch_v2/CLAUDE.md` „NO USER DECISION RULE", (d) `/root/.claude/CLAUDE.md` „użyj ruflo MCP". Trzy z czterech głoszą reguły pracy WYKLUCZAJĄCE się z Przykazaniem #0. `feedback_rules.md:21` sam to nazywa: „*Workspace/CLAUDE.md `## TOOL LIMITATION` … pozostałość z wcześniejszego setup — user explicit directive trumps stale CLAUDE.md*" i `:29` „*zaproponuj Adrianowi update*" — flaga postawiona **2026-05-08, nigdy niewykonana** (plik nietknięty od 05-06).

---

## 2. FLAGI — kanon vs fakt (rdzeń zadania)

### 2a. Model faktyczny = TRZY ROZDZIELNE ŚWIATY (żaden dokument nie opisuje kompletu)

| Świat | Nośnik faktyczny (zweryfikowane) | Kanon wg dokumentów |
|---|---|---|
| **Silnik** (dispatcher/feasibility/scoring/selekcja) | `flags.json` (hot-reload) — po migracji **D3 02.07** (17 kluczy env→flags.json; stare env→`*.conf.bak-pre-d3-ab` martwe) | `dispatch_v2/CLAUDE.md:13` „Live flag → flags.json" ✓ · ALE `/root/CLAUDE.md:10` + `MEMORY.md:1` + `ZIOMEK_REGULY_KANON.md` mówią „drop-iny, NIE flags.json" ✗ |
| **Panel** (nadajesz-panel) | `EnvironmentFile=.../flags.systemd.env` (44 flagi) **+** 3 inline `.conf` **+** `DEFAULT_FLAGS` w `app/core/flags.py` | nieopisane jako całość; „drop-iny" ~częściowo |
| **Apka** (courier-api) | drop-iny `.conf` (env) + `courier_api/config.py` defaults | nieopisane |

**Sprzeczność źródłowa (do decyzji):** stwierdzenie „**stan flag = DROP-INY systemd, NIE flags.json**" (`/root/CLAUDE.md:10`; `MEMORY.md:1`; `ZIOMEK_REGULY_KANON.md` opis) jest **NIEAKTUALNE dla silnika** po D3 02.07 — dowód wprost w trackerze `ZIOMEK_STAN_AUDYTY_1i2.md:19`: „*17 kluczy=true w flags.json (backup .bak-pre-d3-ab), 38 tokenów env usuniętych z 3 serwisów*". Dla panelu/apki „drop-iny/env" nadal PRAWDA. Ani jeden dokument nie opisuje 3-warstwowości + roli `flags.systemd.env`.

### 2b. Spot-check 12 flag: deklaracja vs efektywny stan

| Flaga | Deklaracja docs | Nośnik faktyczny | Efektywny | OK? |
|---|---|---|---|---|
| `ENABLE_AUTO_ASSIGN` | OFF (autonomia OFF) | flags.json=false | **OFF** | ✓ |
| `ENABLE_CARRIED_FIRST_RELAX` | ON | flags.json=true | **ON** | ✓ |
| `ENABLE_COORD_SENTINEL_INGEST_GUARD` | ON (L2.1) | flags.json=true | **ON** | ✓ |
| `ENABLE_COURIER_LAST_KNOWN_POS` | ON | flags.json=true | **ON** | ✓ |
| `ENABLE_OBJM_LEXR6_SELECT` | ON (canary) | flags.json=true | **ON** | ✓ |
| `ENABLE_PARCEL_LANE_LIVE` | ON | flags.json=true | **ON** | ✓ |
| `ENABLE_FROZEN_PICKUP_ETA` | ON | `courier_api/config.py:113` default "1", brak env | **ON** (przez CODE default, nie env) | ✓* |
| `CLAMP_PRESHIFT_PICKUP_ETA` | ON (env, default OFF) — MEMORY.md:8 | `flags.systemd.env`: `PANEL_FLAG_CLAMP_PRESHIFT_PICKUP_ETA=1`; code default False | **ON** | ✓** |
| `PANEL_FLAG_TRUST_CANON_ORDER` | ON | inline `.conf` `trust-canon-order.conf=1` | **ON** | ✓ |
| `PIN_AGREED_PICKUP_TIME` | ON (konsola) | `flags.py` DEFAULT_FLAGS=True | **ON** | ✓ |
| `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` | „**default ON**" (`/root/CLAUDE.md:111`) | **flags.json=false** | **OFF** | ✗ **ROZJAZD** |
| `ENABLE_LOAD_PLAN_PURE_READ` | ON | flags.json=true | **ON** | ✓ |

`*` FROZEN: ON tylko dzięki code-default "1"; osobna flaga env `ENABLE_PICKUP_TIME_READY_FALLBACK=1` (drop-in) NIE jest w tabeli lookup `/root/CLAUDE.md`.
`**` CLAMP: deklaracja MEMORY **poprawna** — ale niewidoczna w `systemctl show -p Environment` (siedzi w EnvironmentFile). Bez czytania `flags.systemd.env` audytor zobaczy fałszywe OFF.

**Rozjazd realny (N12, P2):** `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` — lookup `/root/CLAUDE.md:111` mówi „default ON, gate BUG-C aktywny", a `flags.json=false` (efektywnie WYŁĄCZONY). W pamięci `ziomek-fala-l0` jest ślad „mina COMMIT_DIVERGENCE rozbrojona" (świadomy OFF), ale lookup nie zaktualizowany → czytelnik lookup uzna gate za żywy.

---

## 3. SERWISY / TIMERY deklarowane w dokumentach

| Jednostka | Deklaracja | Fakt (systemctl/atq) | OK? |
|---|---|---|---|
| `dispatch-carried-first-guard.timer` | co 3 min | `OnUnitActiveSec=3min`, biegł 1min temu | ✓ |
| `dispatch-pickup-slip-monitor.timer` | poślizg odbioru 22:30 UTC | `OnCalendar=*-*-* 22:30 UTC` | ✓ |
| `dispatch-parcel-merge.timer` | 30 s | `OnUnitActiveSec=30` | ✓ |
| `nadajesz-parcel-shadow.timer` | 60 s | `OnUnitActiveSec=60` | ✓ |
| `dispatch-new-courier-watch.timer` | wykrywa nowych | `OnCalendar=06..20:0/30 Warsaw` | ✓ |
| `nadajesz-epaka-ingest.timer` | 06:20/12:20 UTC | `OnCalendar=*-*-* 06,12:20 UTC` | ✓ |
| `dispatch-pickup-floor-guard.timer` | L0 floor-guard żywy | aktywny, biegł 1min temu | ✓ |
| `dispatch-proposal-churn.timer` | zainstalowany 02.07, 05:15 UTC | enabled, biegł 6h temu | ✓ |
| `dispatch-objm-lexr6-canary-monitor.timer` | canary aktywny | biegł 9min temu | ✓ |
| **`dispatch-cod-weekly.service`** | „backfill 4 tyg za ACK" | **● FAILED (exit 1)**, ostatnio Pn 29.06; pada 15/22/29.06 | ✗ **N8** |
| `atq` at-200..208 (flipy L3/L4/objm) | at-200/201 03.07, at-202/203 04.07, at-205 06.07, at-208 06.07 | **8 jobów, wszystkie zgodne** | ✓ |

Wszystkie 10 deklarowanych timerów istnieją i biegają. **Jedyny martwy unit: `dispatch-cod-weekly.service`** — pada co poniedziałek, timer nadal uzbrojony (patrz §6/N8).

---

## 4. ŚCIEŻKI — spot-check ~40 (istnienie)

- **OK (silnik/tools/audyt):** wszystkie 26 z listy dispatch_v2 istnieją — `tools/{reassignment_forward_shadow,carried_first_guard,entropy_dashboard,epaka_fetcher,scheduled_flip_gate,eta_truth_map,greedy_vs_lap_replay,proposal_churn_monitor,perf_budget_report,pickup_floor_guard,reassignment_global_select}.py`, `courier_resolver.py`, `auto_assign_{gate,executor}.py`, 3× `ZIOMEK_*.md`, `eod_drafts/{2026-05-26,2026-06-08,2026-06-29,2026-06-30,2026-07-01,2026-07-02}/*`.
- **OK (cross-repo):** `courier_api/{courier_orders,config}.py`, `panel/.../{fleet_state,epaka_ingest,auto_assign_flag}.py`, `papu_dispatch_bridge/bridge.py`.
- **„MISS" = zła ścieżka w moim spot-checku, NIE błąd docs** (pliki żyją indziej — konwencja ścieżek w docs jest RELATYWNA i niejednolita):
  - `dispatch_state/` = **`workspace/dispatch_state/`** (NIE `scripts/dispatch_v2/dispatch_state/`); `courier_plans.json` tam.
  - `shadow_decisions.jsonl` = **`scripts/logs/`** (nie dispatch_state).
  - `log_rotation.py` = **`dispatch_v2/observability/`** (nie `tools/`).
- **Realny MISS (N6, P3):** `/root/.claude/projects/-root/memory_backup_2026-05-06.tar.gz` (cyt. `/root/CLAUDE.md`) — **NIE ISTNIEJE**; są inne (05-18/05-26/05-31/06-14).
- **Drobny (P3):** `config.py COMPANIES` (lookup) — `dispatch_v2/config/` to katalog (jest `cities.json`), brak `config/config.py`; dict COMPANIES mostu Dr Tusz mieszka gdzie indziej — do sprecyzowania w lookup.

---

## 5. todo_master — spot-check 8 statusów Ziomka

| Pozycja | Status wg todo | Fakt | OK? |
|---|---|---|---|
| #7 D3 fale A+B (17 flag→flags.json) | ✅ wykonane 02.07 | flags.json ma D3-klucze; env `.bak-pre-d3-ab` | ✓ |
| L2.1 `ENABLE_COORD_SENTINEL_INGEST_GUARD=true` LIVE | ✅ | flags.json=true | ✓ |
| flipy L3/L4 zaplanowane at-202/203 So 04.07 | 📅 | atq 202/203 = So 04.07 | ✓ |
| `dispatch-proposal-churn` zainstalowany 02.07 | ✅ | timer enabled, biega | ✓ |
| objm-lexr6 canary + at-200 03.07 | 🔵 | flag SELECT=true, monitor żywy, at-200 03.07 18:10 | ✓ |
| `ENABLE_AUTO_ASSIGN` autonomia OFF | 🟡 | flags.json=false | ✓ |
| log-rotation `--apply` za ACK | 🟡 | `observability/log_rotation.py` istnieje | ✓ |
| backfill 4 tyg COD za ACK | 🟡 | **dodatkowo: sam unit cod-weekly FAILED** (todo tego nie woła) | ⚠ N8 |
| baner „zweryfikowane 2026-06-06" | — | plik żywy do 2026-07-03 | ✗ N7 |

---

## 6. TABELA NIEZGODNOŚCI (główna)

| # | Dokument (ścieżka:linia + cytat ≤15 sł.) | Stan faktyczny (dowód) | Typ | Waga |
|---|---|---|---|---|
| **N1** | `/root/CLAUDE.md:38` „`dispatch_v2/CLAUDE.md` … STATYCZNE od 2026-05-10 (snapshot)" | Plik: commit `76daf25` 2026-07-01; głowa l.1-9 = ŻYWE Przykazanie #0 + wskaźnik ZIOMEK_ARCHITECTURE (Faza 2). Body faktycznie snapshot (samo-adnotacja l.13). Etykieta myląca dla głowy. | SPRZECZNOŚĆ DOC↔KOD | **P2** |
| **N2** | `/root/CLAUDE.md:10` + `MEMORY.md:1` + `ZIOMEK_REGULY_KANON.md`(opis): „stan flag = DROP-INY systemd, **NIE flags.json**" | Po D3 02.07 silnik = flags.json (dowód tracker `ZIOMEK_STAN_AUDYTY_1i2.md:19` „17 kluczy=true w flags.json"). Prawda tylko dla panelu/apki. `dispatch_v2/CLAUDE.md:13` mówi ODWROTNIE („flags.json"). | SPRZECZNOŚĆ DOC↔DOC + NIEAKTUALNE | **P2** · ✅ naprawione 05.07 (agent docs-spójność): `dispatch_v2/CLAUDE.md` START TUTAJ wymienia teraz 3 światy (silnik/panel/apka); `ADR-004` już opisywał komplet |
| **N3** | `ZIOMEK_ARCHITECTURE.md:3` / `ZIOMEK_INVARIANTS.md:3` / `ZIOMEK_DEFINITION_OF_DONE.md:3` „STATUS: **DRAFT do przeglądu Adriana**" | `dispatch_v2/CLAUDE.md:8` „Kanon architektury (**zatwierdzony przez Adriana 01.07**, Faza 2)"; MEMORY „CEL ZAAKCEPTOWANY + SZKIELET W GIT 01.07". Nagłówki „DRAFT" nieaktualne. | SPRZECZNOŚĆ DOC↔DOC | **P2** |
| **N4** | `ZIOMEK_DEFINITION_OF_DONE.md:10` „baseline **3611 passed / 2 failed**" | Bieżąca regresja 4096–4110/0-1 (tracker l.10-11: „4110/0", „4101/1-flaky"). Baseline ~500 testów za mały. | NIEAKTUALNE | **P3** · ✅ już rozwiązane 03.07 (audyt N3) — potwierdzone 05.07: `ZIOMEK_DEFINITION_OF_DONE.md:10` NIE ma „3611/2"; jest „4109/0 (03.07)" + zastrzeżenie „porównuj z ostatnim zielonym biegiem, nie z tym nagłówkiem" = niewersjonowane |
| **N5** | `workspace/CLAUDE.md:5,58,113,118` „Adrian does NOT make technical decisions / NOT ask clarifying questions / >30 lines MUST use AIDER / You do NOT have direct access to aider" | Sprzeczne z bieżącą praktyką na 3 osiach: (a) `feedback_rules.md:19-21` — **Claude sam odpala aider przez Bash** (NIE „Adrian executes manually"); (b) `:100-105` — aider TYLKO bulk/boilerplate, kod z architectural-judgment pisze sesja sama (NIE „>30 linii MUST aider"); (c) Przykazanie #0 — ACK+„PYTAJ Adriana" (NIE „NO USER DECISION"). **`feedback_rules.md:21,29` SAM oznacza ten plik jako „pozostałość/stale, zaproponuj update" — flaga z 2026-05-08 nigdy niewykonana** (plik nietknięty od 05-06). | SPRZECZNOŚĆ DOC↔DOC (znany-stale) | **P1** · ✅ rozwiązane (potwierdzone 05.07): `/root/.openclaw/workspace/CLAUDE.md` JUŻ NIE ISTNIEJE (usunięty) → router aider nie jest już auto-ładowany z tej ścieżki |
| **N6** | `/root/CLAUDE.md` „Backup pre-konsolidacji 06.05: `…/memory_backup_2026-05-06.tar.gz` (155KB)" | Plik NIE ISTNIEJE (są 05-18/05-26/05-31/06-14). Ścieżka martwa. | NIEAKTUALNE | **P3** |
| **N7** | `todo_master.md:3,14` „Statusy ZWERYFIKOWANE NA ŻYWO **2026-06-06**" | Plik aktualizowany codziennie (git 2026-07-03; treść ref 02.07). Baner „ostatnia pełna weryfikacja 06-06" ~4 tyg. stary (plik sam dopuszcza dryf). | NIEAKTUALNE | **P3** |
| **N8** | `todo_master.md`/`tracker`: COD Weekly = tylko „backfill 4 tyg za ACK" (dane) | `dispatch-cod-weekly.service` = **● FAILED exit 1**, pada 15/22/29.06; timer uzbrojony (next Pn). Docs nie wołają, że sam UNIT tygodniowy pada aktywnie (root „brak bloku tygodnia" znany w `FALA1_codweekly_raport`). | SPRZECZNOŚĆ DOC↔KOD (martwy unit) | **P2** |
| **N9** | `MEMORY.md:8` „`CLAMP_PRESHIFT_PICKUP_ETA` ON (env, default OFF)" — POPRAWNE | Potwierdzone (`flags.systemd.env`=1; code default False). ALE `systemctl show -p Environment` NIE pokazuje (EnvironmentFile). Żaden kanon nie dokumentuje warstwy `flags.systemd.env` → weryfikacja `show -p Environment` daje fałszywe OFF (landmine #9). | BRAK-W-DOC (method) | **P2** |
| **N10** | Lookup `/root/CLAUDE.md`: frozen-pickup = tylko `ENABLE_FROZEN_PICKUP_ETA` | Żywy drop-in courier-api: `ENABLE_PICKUP_TIME_READY_FALLBACK=1` (osobna flaga, default "0") — nieudokumentowana. | BRAK-W-DOC | **P3** |
| **N11** | `flags.py` komentarze: `COORDINATOR_ASSIGN_LIVE/…_CANCEL/…_EDIT/…_PLAN/DISPATCH_PUSH_LIVE` = „SHADOW dopóki False" (outward-facing) | `flags.systemd.env` ustawia je **=1** na ŻYWYM panelu; dodatkowo `PANEL_ENVIRONMENT=staging` + PLANNED-OFF (`AI01/AI02/OPS07_BATCHING/QLT01_SCORING`)=1. Rozjazd default↔env. | SPRZECZNOŚĆ DOC↔KOD | **P2** (→ §8) |
| **N12** | `/root/CLAUDE.md:111` „flag `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` **default ON**" | `flags.json=false` → efektywnie **OFF** („mina rozbrojona" w `ziomek-fala-l0`, lookup nie zaktualizowany). | SPRZECZNOŚĆ DOC↔KOD | **P2** · ✅ naprawione 05.07 (agent docs-spójność): lookup `/root/CLAUDE.md` przeredagowany — prowadzi żywym stanem „flags.json=false → OFF", nie zaczyna od „default ON" |
| **N13** | `dispatch_v2/CLAUDE.md:1624-1725` OGON „🚨 ZIOMEK DISPATCH AI SYSTEM": l.1628/1665 „Adrian does NOT make technical decisions", l.1683 „>30 lines MUST use AIDER", l.1663 „NO USER DECISION RULE" | Ten SAM plik ma w GŁOWIE (l.5) Przykazanie #0 „Wątpliwość → PYTAJ Adriana" → **self-sprzeczność wewn. pliku**. Zdublowany router aider ≈ kopia `workspace/CLAUDE.md` (N5), doklejony na końcu 1725-liniowego pliku aktywnie edytowanego 07-01. Propaguje się do worktree (wt-audyt/wt-frozenobj). (Bonus l.1574: „model openai/gpt-5.4-mini" = stary runtime-note.) | SPRZECZNOŚĆ DOC↔DOC (wewn.-pliku) | **P1** · ✅ rozwiązane (potwierdzone 05.07): ogon routera aider WYCIĘTY commitem `ab9ac2d` (K2.4) — `grep` „NO USER DECISION"/„MUST use AIDER"/„does NOT make technical" w `dispatch_v2/CLAUDE.md` = pusto. Zostaje jedynie l.~1588 runtime-note „gpt-5.4-mini" (nie router; poza zakresem) |
| **N14** | `/root/.claude/CLAUDE.md` (global prywatny, auto-load): „use ToolSearch to find and invoke ruflo MCP tools … `memory_store, memory_search, swarm_init, agent_spawn`" | Projekt używa pamięci **PLIKOWEJ** `/root/.claude/projects/-root/memory/*.md` (auto-load via MEMORY.md), NIE ruflo MCP. Dyrektywa auto-ładowana, ale nierealizowana/nieadekwatna do faktycznego workflow pamięci. | SPRZECZNOŚĆ DOC↔KOD (orphan) | **P3** |

**Podsumowanie ilościowe:** **14 niezgodności.** Wg typu: DOC↔DOC = 4 (N2,N3,N5,N13), DOC↔KOD = 6 (N1,N8,N11,N12,N14,+N2 częściowo), NIEAKTUALNE = 4 (N2,N4,N6,N7), BRAK-W-DOC = 2 (N9,N10). Wg wagi: **P1 = 2** (N5,N13), **P2 = 7** (N1,N2,N3,N8,N9,N11,N12), **P3 = 5** (N4,N6,N7,N10,N14). Zero P0 w warstwie DOC-consistency (P0-security = inny agent, §8). ⭐ **Skupisko P1 = zabłąkane routery aider auto-ładowane po cwd** (N5 workspace + N13 ogon repo) każą „nie pytać Adriana / delegować kod do aidera" — wprost przeciw Przykazaniu #0.

---

## 7. Co uznałem za wersje OBOWIĄZUJĄCE (do potwierdzenia Adrianowi)

1. **Reguły/priorytety:** `memory/ZIOMEK_REGULY_KANON.md` (KANON v1.0) + `ziomek-change-protocol.md` — mimo że opis KANON niesie stary model flag (N2), reszta reguł żywa.
2. **Architektura/inwarianty/DoD:** `dispatch_v2/ZIOMEK_{ARCHITECTURE,INVARIANTS,DEFINITION_OF_DONE}.md` — traktuję jako ZATWIERDZONE (za `dispatch_v2/CLAUDE.md:8` + MEMORY), mimo nagłówków „DRAFT" (N3).
3. **Stan flag silnika:** `flags.json` = KANON (po D3 02.07). Panel = `flags.systemd.env` + inline `.conf` + `flags.py` defaults. Apka = `.conf` + `courier_api/config.py`. **Reguła „drop-iny NIE flags.json" = odrzucona jako nieaktualna dla silnika.**
4. **Bieżący postęp/zadania:** `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (tracker) + `todo_master.md` + `sprint_timeline.md` (CURRENT HANDOFF). Trio spójne między sobą i z git/atq/systemd.
5. **`dispatch_v2/CLAUDE.md`:** głowa (Przykazanie #0) = żywa; body „Current state/flagi/wersje" = ignorować (snapshot 05-10).
6. **Routery aider auto-ładowane** (`workspace/CLAUDE.md` + ogon `dispatch_v2/CLAUDE.md:1624-1725`): uznaję za **NIEobowiązujące** (sprzeczne z Przykazaniem #0 i praktyką z `feedback_rules.md`) — obowiązuje Przykazanie #0 (głowa `dispatch_v2/CLAUDE.md` + `/root/CLAUDE.md`). Do usunięcia/przepisania decyzją Adriana.
7. **Pamięć = PLIKOWA** `memory/*.md` (auto-load via MEMORY.md) — NIE ruflo MCP z `/root/.claude/CLAUDE.md` (N14).

---

## 8. ⚠ DO WYJAŚNIENIA (dla Adriana — nie rozstrzygam)

1. **N11 — flagi „outward-facing" =1 na żywym panelu:** czy `COORDINATOR_ASSIGN_LIVE/CANCEL/EDIT/PLAN_LIVE`, `DISPATCH_PUSH_LIVE` w `flags.systemd.env` = ŚWIADOMY stan produkcyjny (konsola realnie pisze do gastro), czy pozostałość testowa? Zaskakuje też `QLT01_SCORING/AI01/AI02/OPS07_BATCHING`=1 przy komentarzu „PLANNED/OFF" oraz `PANEL_ENVIRONMENT=staging` na `gps.nadajesz.pl/admin`. Analogia do „papu/idziem = świadomie zachowane identyfikatory" — możliwe że część =1 jest celowa; wymaga potwierdzenia flagą-po-fladze.
2. **N2 — który dokument ma być JEDNYM kanonem flag?** Trzy stwierdzenia się wykluczają (drop-iny / flags.json / 17-w-flags.json). Rekomendacja audytu (do decyzji): jeden akapit „3 światy flag + gdzie który" w `ZIOMEK_REGULY_KANON.md`, z jawnym wskazaniem `flags.systemd.env`.
3. **N8 — `dispatch-cod-weekly.service` FAILED:** czy trzymać uzbrojony timer, który pada co poniedziałek (szum OnFailure), do czasu backfillu — czy zamaskować/naprawić wcześniej? (Naprawa = poza moim read-only.)
4. **N5+N13 — dwa zabłąkane routery aider:** czy skasować/przepisać (a) `workspace/CLAUDE.md` w całości oraz (b) OGON `dispatch_v2/CLAUDE.md:1624-1725` „ZIOMEK DISPATCH AI SYSTEM"? Oba auto-ładowane, oba głoszą „NO USER DECISION / nie pytaj Adriana / >30 linii do aidera" — sprzeczne z Przykazaniem #0 z GŁOWY tego samego repo-pliku. `feedback_rules.md` już 05-08 zalecił update workspace-a, nie wykonano. Ryzyko: inna sesja potraktuje ogon jako regułę. **(Naprawa = poza moim read-only.)**
5. **N14 — `/root/.claude/CLAUDE.md` „Ruflo Integration":** zostawić orphan-dyrektywę MCP (auto-ładowaną każdą sesją), czy dostroić do faktycznego pamięci-plikowego workflow?
6. **P0-SECURITY** (firewall hosta, `/stop` bez auth :8765, CDP :9222 — z `ziomek-audyt-2-wyniki-2026-07-02`): udokumentowane i śledzone, ale **weryfikacja portów należy do innego agenta** — świadomie NIE sondowałem (poza rolą DOC-consistency + potencjalnie inwazyjne).
7. **Konwencja ścieżek w docs:** relatywne `dispatch_state/…`, `tools/…` bez kotwicy repo dają fałszywe „MISS" (§4). Do rozważenia: kotwiczyć ścieżki od `workspace/` w lookup.

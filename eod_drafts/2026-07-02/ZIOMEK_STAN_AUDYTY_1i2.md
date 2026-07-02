# ZIOMEK — STAN OBU AUDYTÓW: gdzie jesteśmy + co zostało (widok zunifikowany)

> 🟢 **ŻYWY TRACKER (Adrian 02.07): to jest źródło prawdy o postępie napraw obu audytów. AKTUALIZUJ PO KAŻDEJ FALI.**
> Protokół aktualizacji (część DoD fali): po zamknięciu fali/naprawy → (1) zmień jej status w §2 (fale L) lub §3 (findingi 2.0) na ✅/🟡/🔴 + commit/flaga/data; (2) przenieś pozycję z §4 „co zostało" jeśli domknięta; (3) bumpnij `Ostatnia aktualizacja` niżej + 1-linijka „co się zmieniło"; (4) jeśli finding 2.0 zamknęła fala L (jak L1.2→rotation-aware) — odnotuj krzyżowo w §3. Nie kasuj historii — dopisuj. Sesja, która zamknęła falę ale nie ruszyła trackera = NIEZAKOŃCZONA.
>
> **Ostatnia aktualizacja:** 2026-07-02 ~06:45 UTC (tmux 9: **FALA-1 PARALLEL-SAFE W TOKU** — 5 lane'ów w worktree `wt-{tz,watchdog,perf,gc,cod}` branch `fix/*`: TZ-consolidate + watchdog-close + perf-SLO + gc-observability + cod-weekly-diag; baseline regresji 3709/0 zapisany; merge SERYJNY po zakończeniu agentów; NIE ruszać tych plików/worktree z innych sesji).
> Poprzednia: 2026-07-02 ~02:30 UTC (utworzenie: stan po L1.1/L1.2/L2.1/L6.A/L0.2 LIVE; audyt 2.0 zamknięty; re-enable 3 monitorów).

**Data snapshotu:** 2026-07-02 ~02:30 UTC · **Źródło stanu:** git log (ground-truth) + flags.json na żywo + master-syntezy obu audytów. ⚠ **Multi-sesja:** ≥2 sesje pchają Fazę 3 tej nocy — stan DRYFUJE, każda zmiana re-grepuje git.

**Mapa dokumentów (co jest czym):**
- **Ten plik** = „na jakim etapie jesteśmy + co zostało" (oba audyty razem).
- `AUDYT2/MASTER_synteza.md` = szczegół audytu 2.0 (niezawodność/jakość/skala/security).
- `ZIOMEK_FINDINGS_LEDGER.md` = JEDEN rejestr wszystkich findingów (27.06+30.06+05.07+02.07) ze statusem/właścicielem.
- `2026-06-30/FAZA1_00..06` = szczegół audytu 1.0 (spójność). `ZIOMEK_ARCHITECTURE/INVARIANTS/DEFINITION_OF_DONE.md` = kanon docelowy.

---

## 1. GDZIE JESTEŚMY (jednym rzutem)

| Audyt | Co bada | Status |
|---|---|---|
| **1.0 spójność** (30.06) | czy Ziomek jest SPÓJNY, czy przyrządy mówią prawdę | ✅ Faza 1 (audyt) + Faza 2 (8 kontraktów ZATWIERDZONE, szkielet w git `76daf25`) DONE. **Faza 3 (naprawy) W TOKU** — ~połowa fal LIVE (§2). |
| **2.0 niezawodność/jakość/skala** (02.07) | czy decyzje są DOBRE, czy przeżyje AWARIE/CZAS/WZROST, + security | ✅ Audyt DONE (14 pasów). Findingi mają właścicieli; naprawy = mini-sprinty (§3-4). |

**Jednozdaniowo:** silnik decyzji zdrowy i aktywnie utwardzany (Faza 3 leci); realne żywe ryzyka są POZA rdzeniem — **security P0** (nowe, 2.0), regres wydajności, 2 bomby TZ (25.10), higiena obserwowalności. Autonomia = OFF (`ENABLE_AUTO_ASSIGN=False`, bezpiecznie).

---

## 2. FAZA 3 audytu 1.0 — fale naprawcze L0-L8 (status ze świeżego git 02.07)

| Fala | Co konsoliduje | Status (git/flags) |
|---|---|---|
| **L0** fundament wiarygodności (rejestr-flag, strażniki, env-parytet) | F6 | 🟡 CZĘŚCIOWO — **L0.2 parytet env carried-first-guard DONE** (`131b555`, de-void); L0.1 rejestr-flag/fingerprint = pending |
| **L1.1** serializer-kompletność | F6 | ✅ **LIVE 01.07** (`85d92f7`; `_METRICS_EXCLUDE` deny-lista, 38 kluczy/14 HARD) |
| **L1.2** prawda przyrządów (rotation-aware + żywy sla_log) | F6 | ✅ **DONE tej nocy** (`fec417e`/`3ba0fdc`/`97f27e9`/`da2fa9b`/`e8a95d2`) — werdykt-toole na `ledger_io`, `b_route real_joined 0→322`, 15+9 tooli rotation-aware |
| **L2.1** sentinel-ingest (most K5) | F3 | ✅ **LIVE 01.07 ~21:29** (`ENABLE_COORD_SENTINEL_INGEST_GUARD=True` potwierdzone; werdykt at-201 03.07) |
| **L2.2/L2.3** catch-all rozróżnia data_poison/real_bug + głośny fail-open grafiku | F3 | 🟡 **BUILD-ONLY, flaga OFF** (`f8ae4ce`) — czeka na flip |
| **L6.A** route-order golden (parytet konsola==kanon) | F5 | ✅ **DONE 01.07** (`tests/golden/route_order_corpus.json`, 13/13; zastępuje wygasający monitor) |
| **L3** plan_recheck nie-cofa (GC + pure-read + regen przez te same bramki) | F2 | 🔴 PENDING |
| **L4** dostępność 1 źródło `available_from=max(now,shift_start)` | F1 | 🔴 PENDING (najgłębsze „nigdy nie wraca") |
| **L5** ETA load-aware (kalibracja na osi poślizgu odbioru) | F4 | 🔴 PENDING ⛔HARD — **bramka 04.07** |
| **L6.B/C/D** O2/geometria-de-pile/objm-frozen-lex | F5 | 🔴 PENDING — **bramki 02.07 (O2) / 03.07 (objm)** |
| **L7** hardening/koherencja (R-declared tripwire, 1 chokepoint clampów, concurrency) | F7 | 🔴 PENDING (L7.5 fcntl pending PRZED re-enable Telegrama) |
| **L8** sprzątanie (dead-code, cache, threshold) | cleanup | 🔴 PENDING |

**Zrobione z Fazy 3:** L1.1 · L1.2 · L2.1 · L6.A · L0.2 (+L2.2/L2.3 build-only). **Zostało:** L0.1 · L3 · L4 · L5 · L6.B/C/D · L7 · L8. Najgłębsze wciąż przed nami = **L4 (available_from) + L5 (ETA) + strażniki L0**.

---

## 3. FINDINGI 2.0 — status + czy już zamykane przez Fazę 3

| Finding 2.0 | Sev | Właściciel / status |
|---|---|---|
| **Security P0** (firewall host OFF, `/stop` bez auth, CDP :9222, wyciek hasła/tokenów) | **P0** | 🆕 NOWY PION — brak właściciela; krok 0 = potwierdź Hetzner Cloud FW; remediacja = osobny sprint pod kierunkiem Adriana |
| **Regres wydajności 2×** (p50 840ms; człony compute-zawsze) | P1 | 🆕 brak właściciela — budżet+SLO+alert (rozszerz canary) |
| **2 bomby TZ** (`gastro_assign:11` + `shadow_outcome_enricher:45`, +klaster) | P1 (od 25.10) | 🆕 konsolidacja fixed-offset→ZoneInfo; **data twarda 25-26.10** |
| **Blokery autonomii** (fałszywy-sukces exit-code + 1.flip-nie-no-op) | P1 (przed ON) | 🆕 przed 1. flipem AUTON — RAZEM (F+G+TOCTOU) |
| **Martwe monitory** (watchdog+2) | P1 | ✅ RE-ENABLE DONE (ACK); domknięcie OnCalendar+cod-weekly pending |
| **`cod-weekly` FAILED+silent** | P2 live | 🆕 diagnoza exit1 + OnFailure + rejestr cron_health |
| **Alerty procesowe nie danowe / meta-strażnicy kłamią** | P1/P2 | częściowo → **L1.2 zamknęła część** (b_route live-sla, rotation-aware); reszta (danowe alerty) = 2.B |
| **Readerzy niespójnie rotation-aware** (L13) | P2 | ✅ **W DUŻEJ CZĘŚCI ZAMKNIĘTE tej nocy** (L1.2 T3/T3b: 15+9 tooli) — do potwierdzenia że komplet |
| **carried_first_guard VOID** (L09) | P2 | ✅ **ZAMKNIĘTE** (L0.2 env-parytet `131b555`) |
| **GC observability atrapa / events.db >90d ~10.07** | P2 | 🆕 → L8 + 2.D; **data ~10.07** |
| **Strażnicy feasibility cienkie/teatr** (verdict-gate polaryzacja) | P2 | 🆕 → dogęścić (L0/2.0 0.H) |
| **Mina flagi `ENABLE_LOAD_PLAN_PURE_READ`** (default False) | P2 | 🆕 → default True u callerów (powiązane z L3) |
| **pending 3-writer no-lock (O1) + klaster postpone** | P2 | → **L7.5** (przed re-enable Telegrama) |
| **Multi-city ~146 hardcode / brak city_id** | P2 skala | 🆕 → przed 2. miastem/Restimo (rejestr cities.json) |
| **`osrm-fallback-double-traffic`** | — | ✅ REFUTED — już naprawione 28.06 (był dziurą w rejestrze) |
| **grafik UTC vs Warsaw + literówka** | P2 | 🆕 → `today` z ZoneInfo |

---

## 4. CO ZOSTAŁO — backlog scalony (priorytet)

**A. Żywe/tanie (teraz):**
1. Security krok 0 (potwierdź Hetzner Cloud FW) → potem quick-wins (auth /stop, bind 9222/porty, .secrets→.gitignore+chmod, rotacja sekretów).
2. Domknąć watchdog (OnCalendar) + dorejestrować cod-weekly (+ OnFailure) + diagnoza exit1.
3. Bomby TZ → ZoneInfo (przed 25.10; jest zapas, ale data twarda).
4. Budżet wydajności + SLO + alert (rozszerz canary).

**B. Faza 3 pozostała (protokół ETAP 0→7 + ACK per fala):**
5. L4 available_from (najgłębsze) · L3 plan_recheck · L5 ETA load-aware (bramka 04.07) · L6.B/C/D (bramki 02-03.07) · L0.1 rejestr-flag · L7 (w tym L7.5 fcntl przed Telegramem) · L8 sprzątanie · flip L2.2/L2.3.

**C. Strukturalne (decyzja o ACK Pionów 2/3 z 2.0):**
6. Alerty danowe (2.B) · zasoby/GC/rotacja (2.D+L8) · governance systemd · security pełny · multi-city (przed skalą) · oś 05.07 (Postgres/DR/SLO — mierz KIEDY load-replay ×2/×5).

---

## 5. DATOWANY KALENDARZ (min czasowych)
- **02.07** bramka O2 (L6.B) — wyrównać kotwicę bundle_calib; bug4-gate WAIT z fałszywego powodu.
- **03.07** objm at-200 (L6.D).
- **04.07** load-aware ETA (L5) — **brać kotwicę `assign` 83%, NIE `last` 4%** (L07).
- **~10.07** events.db >90d (auto_vacuum=0) + wygasa monitor route-order (golden już zastąpił).
- **25-26.10** koniec DST → obie bomby TZ się uzbrajają.

---

## 6. UWAGA MULTI-SESJA (krytyczne dla następnej sesji)
Tej nocy ≥2 sesje pchają Fazę 3 (tmux 8 zrobił L1.2). **Przed KAŻDĄ zmianą: `git log --oneline -10` + `tmux ls` + sprawdź cudze `.bak-*`.** Część findingów 2.0 rozwiązuje się „w locie" przez fale L — przy podejmowaniu naprawy z §3-4 NAJPIERW re-grep czy już zamknięte (ETAP 0). Relay stanu: [[ziomek-audyt-2-wyniki-2026-07-02]] + [[ziomek-unified-audit-2026-06-30]] (Faza 3 relay).

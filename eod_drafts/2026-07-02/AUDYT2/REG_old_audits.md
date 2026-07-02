# REG — Rejestr starych audytów Ziomka (znormalizowany)

**Lane:** AUDYT 2.0 → REJESTR FINDINGÓW, część STARE AUDYTY.
**Wejście:** `AUDIT_2026-05-07/` (10 plików) + `eod_drafts/2026-06-27/ZIOMEK_DEEP_AUDIT_{FINDINGS.json,REPORT.md}`.
**Wyjście danych:** `findings_old.jsonl` — 128 wierszy (47 z 05-07 + 81 z 06-27).
**Tryb:** read-only wobec produkcji. `status_claim` = to co TWIERDZIŁ audyt w swoim czasie (dla 06-27 wzbogacone o werdykt jego własnej warstwy weryfikacji), **nie** zweryfikowane wobec bieżącego kodu — reconcile z żywym stanem to osobny lane.

---

## 1. Ile findingów per severity per audyt

### Audyt 2026-05-07 (47 wierszy: 20 R + 20 F + 7 RC)

Dwie skale współistnieją: ryzyka **R-1..R-20** używają `R=P×I`, findingi **F1..F20** używają `P0-P3`.

| Skala | Poziom | Liczba | ID |
|---|---|---|---|
| R (P×I) | Krytyczne R≥16 | 4 | R-1(20), R-3(16), R-4(16), R-6(16) |
| R (P×I) | Wysokie R 10-15 | 12 | R-2, R-5, R-7 (15) · R-8..R-15 (12) · R-16 (10) |
| R (P×I) | Średnie R 6-9 | 4 | R-17(9), R-19(9), R-18(8), R-20(8) |
| P | P0 | 1 | F2 |
| P | P1 | 4 | F1, F5, F8, F9 |
| P | P2 | 11 | F3, F4, F6, F7, F10, F12, F13, F14, F15, F18, F20 |
| P | P3 | 4 | F11, F16, F17, F19 |
| meta | RC (klasa root-cause) | 7 | RC1..RC7 |

Klasy (jako `klasa`): RC1=6, RC2=5, **RC3=11**, RC4=6, RC5=7, RC6=3, **RC7=9** wierszy je wskazuje.
Status: 45 open, 2 latent (F5 wyłączony przez override.conf; R-13 naming z rstrip-workaround).
Oceny audytu (nie-findingi, w prozie): Maintainability **5/10**, Scalability **3/10**, Production **6/10**.

### Audyt 2026-06-27 (81 wierszy)

| Widok | P1 | P2 | P3 | none/dissolved | nie-weryfikowane |
|---|---|---|---|---|---|
| **Filed (jak zgłoszono)** | 2 | 31 | 48 | — | — |
| **Po adwersarialnej weryfikacji** | 0 | **9** | 44 | 10 | 18 |

⚠ **Kluczowa obserwacja:** własna warstwa weryfikacji audytu 06-27 mocno go zdeflowała — z 33 findingów P1/P2 tylko **9 utrzymało rangę P2**, 10 rozpłynęło się do „none" (8 refuted + 2 partiale zeszły do zera), a 18 nigdy nie przeszło adwersarialnego re-checku. To rejestr „szeroki i płytki": dużo dead-code / flag-OFF / telemetrii, mało żywych bomb.
Status: 37 open, 35 latent (flag-OFF/dead/shadow/dormant), 8 unknown (refuted), 1 fixed (`p5-cancel-recanon`).
Top lane'y (klasa): metric-serialization-gaps(7), tests-shadow-coverage(7), twin-path-divergence(6), plan-recheck/shadow-serializer/courier-resolver-gps/panel-ingest-state/czasowka/console-app-parity/ml-calibration(po 5).

---

## 2. TOP-10 wg mnie nadal groźnych

Kryteria: confirmed + żywe w produkcji + dotyka poprawności/bezpieczeństwa/danych treningowych + klasa NAWRACAJĄCA (05-07 i 06-27 pokazują ten sam korzeń). Gdzie finding 05-07 mógł już zostać naprawiony punktowo — **klasa** żyje, co dowodzi echo w 06-27.

1. **F2 + RC3 (silent failures / observability tylko dla anticipated)** — P0. Klasa udowodniona jako wciąż żywa: 06-27 pokazuje że marker inwariantu P0 „SOFT obeszło HARD" i cała telemetria feasibility NIGDY nie trafiają do shadow_decisions → naruszenia są niewidoczne, a protokół zmiany nie ma jak udowodnić ETAP-4/5.
2. **cap-carried-relax-app-console-divergence (06-27, P2 confirmed, LIVE)** — 44-75 worków/dzień: apka kuriera każe wieźć stygnące jedzenie NAJPIERW, sprzecznie z konsolą i silnikiem; trzy powierzchnie (engine/console/app) rozjechane, „jedno źródło prawdy" route_podjazdy to fikcja.
3. **osrm-fallback-double-traffic (06-27, P2 confirmed)** — podczas awarii OSRM każdy leg zawyża czas jazdy ~1,5-1,6× (bucket korkowy × mnożnik), generując fałszywe naruszenia R6 i lawinę KOORD/ALERT dokładnie gdy system już jest zdegradowany.
4. **tests-etap4-registry-drift-isolation-leak + feas-r6-bagcap-untested-live (06-27, P1)** — sama siatka bezpieczeństwa jest dziurawa: 24 żywe flagi wyciekają prod-wartościami do CAŁEGO test-suite, a żywa flaga twardej bramki R6 na gold ma ZERO testu ON≠OFF → regresja bezpieczeństwa przejdzie na zielono.
5. **R-4 / F1 / RC4 (multi-writer JSONL/stan bez locka)** — korupcja audit-trail/danych ML (fundament pivotu Z3). Klasa żywa dziś: `dsi-pending-multiwriter-shared-tmp-no-lock` (06-27) — 3 writery pending_proposals bez cross-proc locka, bezpieczne TYLKO dlatego że telegram jest wyłączony.
6. **F20 / RC5 (state ownership emergent) → dsi-postpone-sweeper-orders-state-schema-mismatch (06-27, P2 confirmed bug)** — dokładnie to przed czym ostrzegał F20: konsument czyta `cid` zamiast `courier_id` i `orders.{}` zamiast płaskiego dictu → rozwiązanie postpone martwe → duplikat/phantom propozycja albo błędna eskalacja KOORD.
7. **R-2 (single-server SPOF)** — strukturalnie bez zmian: jeden Hetzner, brak repliki/failover; reboot/OOM = pełny outage, twardy blocker 10×/multi-tenant (cały stan = filesystem-as-IPC).
8. **rst-grouping-greedy-double-pickup (06-27, P2 confirmed)** — udokumentowany awaryjny rollback „1 flaga `ENABLE_V326_OR_TOOLS_TSP=False`" NIE jest równoważny behawioralnie: greedy/bruteforce podwójnie odwiedza zgrupowany super-pickup → zawyżony czas + zły sequence. Groźne bo to dźwignia awaryjna.
9. **metric-serialization-gaps (06-27, cała rodzina 7+) + shser-inv-feas-marker** — „metryka policzona ale nie serializowana": R6 tier-cap, post_shift_overrun (wiodący klucz selekcji best_effort po flipie), end_of_day_salvage (rozluźnienie HARD), R1/R5/R8 magnitudy — wszystko ciemne w logu → replay-walidacja flipów (rdzeń protokołu zmiany) jest ślepa.
10. **R-1 / R-5 (telegram god object + utrata pending przy restarcie)** — de-facto immutable runtime; uśpione dopóki telegram OFF, ale mina przy re-enable — echo w `tk-pending-dualwriter` / `tk-watchdog-keyerror-twin` / `dsi-pending-multiwriter`.

---

## POKRYCIE

- **Kanoniczne findingi znormalizowane 1:1:** R-1..R-20 (ARCHITECTURE_AUDIT §2), F1..F20 (STATE_OWNERSHIP §4), RC1..RC7 (META §RC + SYNTHESIS §2, RC7 dodany w SYNTHESIS). Wszystkie 81 findingów z JSON 06-27 przeniesione z zachowaniem id, severity, plików, oraz werdyktu weryfikacji doklejonego do `mech` (`| weryf:<verdict>-><corrected>`).
- **Mapowanie klas:** 05-07 `klasa`=RC1..RC7 (primary per SYNTHESIS §2). 06-27 `klasa`=lane audytu (18 lane'ów = jego natywne grupowanie wzorców). Most między nimi: metric-serialization-gaps + shadow-serializer ≈ **RC3+RC5** (widoczność/ownership), twin-path-divergence + console-app-parity + recanon-symmetry ≈ **anty-wzorzec „fix w 1 z 2 bliźniaków"** (blisko RC7/RC6), data-state-integrity + panel-ingest-state ≈ **RC1+RC4+RC5**, flags-config-systemd ≈ **RC5** (config-as-code/regime split).
- **Deep-dive'y 05-07 (6 plików) wchłonięte, nie enumerowane osobno:** CONCURRENCY, MULTI_TENANT, OBSERVABILITY, OPERATIONAL_RESILIENCE, TELEGRAM_APPROVER_GOD_OBJECT to pogłębienia tych samych R/F. Ich korekty wplecione w `mech`: F1/R-4 — CONCURRENCY §1a koryguje premisę PIPE_BUF (nie dotyczy regular files, interleaving przez bufory → ryzyko niższe); R-1 — TELEGRAM audit: pending JUŻ atomic, realna luka = wąskie okno SIGTERM race; R-3 — TELEGRAM audit obniża (zajęcie slotu thread-poola, nie zamrożenie loopa).
- **Świeżość weryfikacji 06-27:** 63/81 przeszło adwersarialny re-check (32 confirmed, 23 partial, 8 refuted); 18 bez re-checku — oznaczone w `mech`.
- **Naprawione już w źródle:** `p5-cancel-recanon-confirmed-fixed` (commit 0426706) — jedyny wiersz `status_claim=fixed`.

## JAWNE LUKI

- **Brak reconcile z bieżącym kodem.** `status_claim` dla 05-07 = twierdzenie audytu z 07.05, NIE stan na 01.07. Wiele R/F prawdopodobnie naprawiono punktowo od tego czasu (MASTER_DEPLOY_PLAN planował F2/jsonl_appender/logrotate/MemoryMax jako P0) — weryfikacja „open vs już-fixed" to zadanie osobnego lane'a żywego stanu.
- **Deep-dive'owe sub-findingi nie mają własnych ID.** OPERATIONAL_RESILIENCE §4 ma własną „listę 12 pozycji", CONCURRENCY proponuje `#22 event_bus.emit() retry decorator (P0)`, TELEGRAM audit kategoryzuje „10 silent-killer except". Zwinięte do R/F — drobna utrata granularności (potencjalnie ~10-15 nie-zmapowanych 1:1 pozycji).
- **MASTER_DEPLOY_PLAN nie jest w rejestrze.** TOP-15 zadań deploy (08.05→04.06) + 5 decyzji dla Adriana to plan remediacji (co ROBIĆ), nie findingi — świadomie pominięte jako nie-findingowe.
- **Dwie skale severity nieujednolicone.** R=P×I (05-07 ryzyka) vs P0-P3 (F + całe 06-27). Zachowane obie w `sev`; porównanie cross-audyt jest przybliżone (R≥16 ≈ P0/P1).
- **Brak cross-audyt dedupu (świadomy).** Ten sam korzeń liczony wielokrotnie: R-4 ≈ F1 (learning_log), F1 ≈ `dsi-pending-multiwriter` (multi-writer bez locka), F20 ≈ `dsi-postpone-sweeper-schema-mismatch`, F5 ≈ `tk-pending-dualwriter`, R-13/R-16 ≈ `tests-bak-file-proliferation`. Rejestr trzyma je osobno (wierność źródłom) → surowe liczby zawyżają liczbę UNIKATOWYCH klas.
- **18 findingów 06-27 bez adwersarialnej weryfikacji** — ich severity jest as-filed, nieprzetestowane pod kątem zawyżenia (audyt sam pokazał ~30% deflację przy re-checku).
- **`mech` 06-27 = pierwsze zdanie `evidence`** (skrót ~200 zn.) — pełny mechanizm/impact/recommendation są w źródłowym JSON, nie w rejestrze.

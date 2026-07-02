# ZIOMEK — AUDYT 2.0: MASTER-SYNTEZA (niezawodność · jakość · skala)

**Data:** 2026-07-02 · **Tryb wykonania:** wieloagentowy read-only (workflow 14 pasów + 3 agenty domykające + weryfikacje w głównym wątku) · **Autor:** sesja główna
**Relacja do 1.0:** audyt spójności 30.06 pytał „czy Ziomek jest SPÓJNY i czy przyrządy mówią prawdę". 2.0 pyta „czy decyzje są DOBRE, czy przeżyje AWARIE/CZAS, czy przeżyje WZROST" + domyka dziury 1.0. Wykonano protokołem read-only (zero zmian silnika/flag/git); **jedyne działanie na żywo = re-enable 3 martwych monitorów za ACK Adriana (§2).**

**Deliverables (24 pliki w `eod_drafts/2026-07-02/`):** `ZIOMEK_FINDINGS_LEDGER.md` (jeden rejestr) + `AUDYT2/{L01..L13, SWEEP_*, ALERTY_matrix, PERF_budget, MULTICITY_inventory, HINDSIGHT_v0, VERIFY_p1}.md` + `findings_old/new.jsonl`.

---

## 1. NAJWAŻNIEJSZE W JEDNYM AKAPICIE (dla Adriana)

Ziomek DZIŚ nie ma żywego pożaru w silniku decyzji (potwierdza 1.0). **Największe zaskoczenie leży POZA silnikiem: bezpieczeństwo (nigdy nieaudytowane) ma P0** — firewall hosta wyłączony, nieuwierzytelniony kill-switch przydziału `/stop` na porcie publicznym, Chrome DevTools bez auth na 0.0.0.0:9222, wyciekłe hasło fleet-admina i hardcoded tokeny botów (§2.5; jeden caveat — ekspozycja zależy od niepotwierdzonego Hetzner Cloud FW). Poza tym **trzy realne, żywe długi, których 1.0 nie badał:** (1) **warstwa alertów staleości była MARTWA 5 dni** — 3 monitory zbiorowo wyłączone 26.06, przez co burza sentineli (0,0) i padnięty `cod-weekly` szły po cichu (naprawiłem: re-enable, §2); (2) **wydajność zregresowała 2× i nikt nie patrzy** — p50 decyzji ~840 ms (kwiecień 375, cel 150-200), przyczyna = akrecja członów „compute-zawsze" liczonych nawet przy fladze OFF; (3) **dwie datowane bomby czasowe** uzbrajają się ~26.10 (koniec DST) — hardcode strefy `+2` w ścieżce przydziału i w żywym narzędziu telemetrii. Plus: rejestry findingów NIE były scalone (16 cichych sierot z 27.06, m.in. `osrm-fallback-double-traffic`), a strażnicy feasibility są cienkie/teatralne (verdict-gate nie łapie inwersji). Metryki skali/odporności/security z audytu 05.07 leżą nietknięte 2 miesiące, a bezpieczeństwo nie było audytowane NIGDY.

---

## 2. ✅ DZIAŁANIE JUŻ WYKONANE (za ACK Adriana) — re-enable martwych monitorów

**Problem (ground-truth):** `dispatch-watchdog.timer` + `dispatch-delivered-integrity.timer` + `dispatch-state-panel-monitor.timer` = `disabled/inactive` od **26.06 19:43:41-42** (zbiorowo, w 1-2 s — brak śladu świadomego retire). Watchdog (jedyny danowy detektor staleości cronów, co 4h) nie przebiegł 124h. To meta-przyczyna, dlaczego `cod-weekly` FAILED (2 dni) i burza sentineli poszły bez alertu.
**Akcja:** `systemctl enable --now` na 3 timerach + jednorazowy bieg watchdoga (re-anchor 4h + pierwszy skan). Stan po: wszystkie `enabled/active`, watchdog `checked=14 stale=0`, następny bieg 02:46 UTC.
**⚠ NIE zamyka tematu w 100% (2 warstwy głębiej):** (a) watchdog śledzi tylko **14 z ~50+ timerów** — `cod-weekly` NIE jest zarejestrowany w `cron_health` (`thr=None`), więc i tak by go nie złapał; (b) root cichej śmierci = timer ma tylko `OnUnitActiveSec` bez `OnCalendar` → następny `daemon-reload` znów go uśpi. Trwały fix (osobny ACK): dodać `OnCalendar`, dorejestrować cod-weekly + 7 wpisów `thr=None`, dodać `OnFailure` do cod-weekly (standard, którego jako jedyny nie ma). Rollback re-enable: `systemctl disable --now <3 timery>`.

---

## 2.5 🔴 BEZPIECZEŃSTWO — P0 (pierwszy audyt security w historii projektu; największe zaskoczenie 2.0)

Obszar NIGDY nieaudytowany („biały obszar"). Zweryfikowałem 3 najcięższe punkty samodzielnie (ss/iptables/kod), reszta z pasa L12.

**⚠ JEDEN KLUCZOWY CAVEAT (określa całą severity):** z wnętrza hosta NIE DA SIĘ potwierdzić, czy działa **Hetzner Cloud Firewall** (konfigurowany w konsoli Hetznera, niewidoczny z VM). **Firewall HOSTA jest DEFINITYWNIE wyłączony** (ufw inactive, iptables INPUT policy=ACCEPT — potwierdzone). Jeśli NIE ma też Cloud FW → porty poniżej są PUBLICZNE (P0). Poszlaki że ekspozycja realna: certy Let's Encrypt + nip.io na 8443/8765. **Krok 0 przed wszystkim: Adrian sprawdza konsolę Hetznera LUB test jednego portu z zewnątrz (telefon poza wifi).**

| # | Ryzyko | Weryfikacja | Sev (jeśli brak Cloud FW) |
|---|---|---|---|
| S1 | **Brak firewalla hosta** — amplifikuje całą resztę | ✅ ja: ufw inactive, iptables INPUT ACCEPT | P0 |
| S2 | **Kill-switch `/stop` BEZ auth** — `gps_server.py:73` `open("/tmp/gastro_stop")` zatrzymuje przydział CAŁEGO biznesu, `/start` cofa | ✅ ja: kod, zero auth, na 0.0.0.0:8765 | P0 |
| S3 | **Chrome DevTools (CDP) na 0.0.0.0:9222** (kontener openclaw-browser) — protokół bez auth z definicji → zdalne przejęcie przeglądarki agenta (cookies/sesje/exfil/pivot); DOCKER-USER pusty | ✅ ja: ss potwierdza 9222 LISTEN 0.0.0.0 | P0 |
| S4 | **Słabe hasło fleet-admin na publicznym :8767** wzór „słowo+2026", **WYCIEKŁO cleartextem do `courier_api.log` (644, world-readable)**, bez rate-limit/2FA; panel = live-GPS floty + traile kurierów | L12 (grep) | P1 |
| S5 | **Hardcoded ŻYWE tokeny botów Telegram** — `dispatch_control.py:10`, `gastro_koordynator.py:34` (/root=755 czytelne), token zduplikowany w `ZIOMEK_MASTER_KB.md` | L12 | P1 |
| S6 | openclaw-gateway 18789/18790 na 0.0.0.0 → 200 bez auth; `.secrets/*` 644 + poza `.gitignore` (git add -A = wyciek); PIN-y kurierów 4-cyfrowe plaintext + `GET /api/couriers` bez auth = brute wykonalny | L12 | P1/P2 |

**✅ Dobra wiadomość — konsola koordynatora `gps.nadajesz.pl/admin` jest MOCNA:** bind `127.0.0.1:8000` (tylko nginx), JWT HS256 z allowlistą algo, sekret 64-zn ≠default, `/api/coordinator/auto-assign` za `require_roles("ziomek_admin")`, login rate-limit+lockout+2FA TOTP. Token sesji kuriera też mocny (`token_urlsafe(32)`, idle-logout 90min). ZERO podatności — przycisk autonomii jest dobrze chroniony.

**Quick-wins (leczą większość naraz, niskie ryzyko — ale Twoja decyzja/kierunek):** (1) firewall / bind portów do loopback+tailscale; (2) auth na `/stop`; (3) `.secrets/`→`.gitignore` + `chmod 600` + `chmod 700 /root`; (4) wyłączyć dump ciała żądań w courier_api + zrotować wyciekłe/hardcoded sekrety (tokeny botów, hasło fleet); (5) zbindować 9222 do loopback. **Nie dotykałem — remediacja security = osobny sprint pod Twoim kierunkiem (część rusza kontenery/inne systemy).**

---

## 3. POTWIERDZONE FINDINGI P1 (weryfikacja drugą metodą)

| # | Finding | Werdykt | Materialność | Właściciel/fix |
|---|---|---|---|---|
| **A** | **Martwa warstwa alertów staleości** (3 monitory 5 dni) — §2 | **CONFIRMED** (ground-truth systemctl, ja) | żywe: cod-weekly+sentinel bez alertu | ✅ re-enable; trwałe: OnCalendar+rejestracja (ACK) |
| **B** | **`cod-weekly` FAILED+SILENT 2 dni** (pada co pon. na braku bloku tygodnia w arkuszu; 0 alertu bo brak OnFailure + poza cron_health) | **CONFIRMED** (journal, 2 pasy) | ≥2 rozliczenia COD przepadły | diagnoza exit1 + OnFailure + rejestr (ACK) |
| **C** | **Bomba TZ #1 — `gastro_assign.py:11`** `_WARSAW=timezone(+2)` hardcode; zimą (CET=+1) ścieżka HH:MM (ołówek/legacy) liczy „now" +1h → odbiór w najbliższej godzinie wpada w guard „+1 dzień" → do gastro ~1410 min zamiast ~20 | **CONFIRMED** (przeczytałem kod :153-160, ja) | uśpiona do ~26.10; potem realna na HH:MM<1h | fix `ZoneInfo`; bramka przed 26.10 |
| **D** | **Bomba TZ #2 — `shadow_outcome_enricher.py:45`** `WARSAW_OFFSET_HOURS=2` w ŻYWYM narzędziu (timer active) → 1h błąd stempli po DST 25.10 → skrzywiona atrybucja godzin w `decision_outcomes` (karmi ewaluację) + klaster uśpionych narzędzi tej klasy | **CONFIRMED** (L08, deterministyczne) | uśpiona do 25.10; potem 1h dryf | konsolidacja fixed-offset→`ZoneInfo` (wzór `ontime_lib` istnieje) |
| **E** | **Regres wydajności 2×** — p50 ~840 ms (kwiecień 375, cel 150-200), płaski przez 14 dni; ~665 ms/decyzja = człony „shadow/compute-zawsze" liczone nawet przy fladze OFF (227 commitów obj/shadow od upgrade'u, „shadow"×242 w dispatch_pipeline) | **CONFIRMED** (pomiar ledgera n=1210+14d, PERF_budget) | peak p50 ~1000-1100, ogon >1500 ms rośnie 2%→19% | budżet warstw + SLO + alert regresji (zero nowej infry — rozszerz canary) |
| **F** | **Fałszywy sukces przydziału** — `gastro_assign`/`auto_koord`: puste ciało/HTML logowania przechodzi `'error' not in str(result)` → ASSIGN_OK; executor autonomii ufa returncode | **CONFIRMED** (dowód live w L05: `{'raw':''}`→ASSIGN_OK) | dziś maskowane (człowiek reconcile); P0 po flipie autonomii | twarda detekcja sukcesu PRZED 1. flipem AUTON |
| **G** | **Pierwszy flip autonomii NIE jest no-opem** — 10 strict `would_auto_assign=true` w ledgerze (~1-2/d), ścieżka executor→panel nigdy E2E, kurier przekazywany po NAZWIE (fuzzy), brak idempotencji per-zlecenie | **CONFIRMED** (L05+SWEEP_auto_assign_executor) | 0 dziś (OFF); realne przy 1. ON | E2E na sucho + idempotencja + detekcja F PRZED włączeniem |
| **H** | **Grafik: data w UTC vs godziny w Warsaw** → 00:00-02:00 Warsaw ładuje grafik ZŁEGO dnia (date-mismatch=True LIVE); + literówka w arkuszu → `parse_hour=None` → coupling kasuje CAŁY wpis kuriera (znika z puli) | **CONFIRMED, DOWNGRADE P1→P2** (VERIFY: date-mismatch nisko-wolumenowy 2h/noc; literówka bez bieżącej instancji; SPOF wymaga podwójnej awarii) | nocne okno codziennie; literówka = cicha utrata kuriera | `today` z `ZoneInfo`; walidacja wpisu zamiast kasowania |

**Werdykty adwersaryjne (VERIFY_p1) — dossłane:**
- **TOCTOU `gastro_edit`: CONFIRMED mechanizm, ale KOREKTA** — bliźniak `panel_sync` ma DETEKCJĘ post-write+alert, NIE lock (sam też nie zapobiega wyścigowi) → „asymetria strażnika" słabsza niż raportowano. Okno sub-sekundowe, edit-live=2 total → **P2 dziś / P1 latentnie pod autonomią**.
- **`osrm-fallback-double-traffic`: REFUTED — JUŻ NAPRAWIONE 28.06** (dzień po zgłoszeniu). Guard `osrm_client.py:321` żywy, bezwarunkowy, short-circuit przed mnożnikiem; bliźniak `dispatch_pipeline.py:4164` też załatany; `chain_eta.py:108` = inny (poprawny) fallback. **To NIE żywy bug — to błąd higieny rejestru** (sierota nieujęta w roadmapie 30.06) → potwierdza tezę L01 o potrzebie JEDNEGO rejestru findingów.
- **Przekrojowo (VERIFY):** findingi F+TOCTOU to ta sama klasa („walidacja sukcesu + integralność w rękach piszących do gastro") — jeden wspólny fix domyka oba + `auto_koord`; **F+G+TOCTOU MUSZĄ być zamknięte RAZEM przed 1. włączeniem autonomii** (executor = współbieżny writer, ufa returncode, brak idempotencji).

**Refuted samodzielnie:** „zawis workera po L2.1" (perf side-note) — `worker_alive=False` to heurystyka świeżości (`age<300s`), w nocy zawsze False (270/270 poprzedniej nocy), stan sprzed restartu. NIE incydent. Deploy L2.1 czysty. **Ale** ujawnia realny drobny finding: ta heurystyka jest ślepa dokładnie w godzinach cichych — realnego zawisu nocnego by nie odróżniła.

---

## 4. MOTYWY PRZEKROJOWE (to samo źródło, wiele objawów — zgodne z K1 z 1.0)

1. **Alerty PROCESOWE, nie DANOWE.** OnFailure łapie padnięcie procesu; NIE łapie: kłamiącego pomiaru, zamrożonego pliku, sentinela w danych (2046+14456 zdarzeń=0 alertów), pustej puli, martwego monitora. 42% jednostek bez żadnego alertu, realne pokrycie ciszy ≈ 0%.
2. **Meta-strażnicy sami niesprawdzeni.** Z 12 zbadanych instrumentów observability: 4 VOID/broken (watchdog-martwy, cron_health kłamie „failed" na 3 zdrowych oneshotach, koord_cascade obiecuje nieistniejący OnFailure), tylko `liveness_probe` realnie zadziała. „Kto pilnuje strażnika" = nikt.
3. **Fixed-offset TZ rozsypany** (nowy „threshold-sprawl" na strefę): 2 żywe bomby + ≥5 uśpionych narzędzi z `+2` hardcode, przy istniejącym poprawnym wzorcu `ontime_lib` (ZoneInfo). Data uzbrojenia: DST 25-26.10.2026.
4. **Miny na fladze/awarii** (klasa z 1.0, potwierdzona): `ENABLE_LOAD_PLAN_PURE_READ` czytane z domyślnym `False` → brak klucza/uszkodzony flags.json = oscylacja carried-first WRACA (bezpieczny default ustawiony na NIEBEZPIECZNY).
5. **Producenci danych i ręce piszące poza rdzeniem** — najgroźniejsza ciemna strefa 1.0: GPS-ingest (guard L2.1 trafił w martwe źródło PWA, nie w żywy `courier_api/gps/batch` — dziś latentne, bo apka nie emituje sentineli), grafik (SPOF, TZ, literówka-kasuje-kuriera), zapisy do gastro (TOCTOU, fałszywy-sukces).
6. **Strażnicy feasibility cienkie/teatralne** (mutation-test): verdict-gate sprawdza OBECNOŚĆ tokenu nie POLARYTET (inwersja `not` przechodzi), bag-cap off-by-one przeżywa wszystkie testy behawioralne. Klaster danych/sentineli — solidny.
7. **Rejestry findingów niescalone** (K1 dla samych audytów): 16 cichych sierot z 27.06 (grep korpusu 30.06 = 0), oś 05.07 (RC1 filesystem-IPC=K1, SPOF, SLO, unbounded state) leży 2 miesiące.
8. **Skala niezaadresowana:** ~146 hardcode'ów jedno-miastowych, zero `city_id`, OSRM jednoregionowy, walidator-prawdy = bbox Białegostoku; whole-file LOCK_EX na JSON = kandydat na contention ×2-×10; brak testów obciążeniowych.

---

## 5. CO 2.0 ZNALAZŁ, CZEGO 1.0 NIE MÓGŁ (wartość dodana)

- 1.0 był read-only → nie zmierzył **wydajności** (regres 2×), nie **reprodukował wyścigów** (O1/O3 + mina flagi), nie **przejechał krawędzi czasu** (2 bomby TZ + midnight), nie **zmierzył siły strażników** (mutation-test → teatr verdict-gate), nie badał **producentów/zapisu** (GPS/grafik/gastro), **odporności** (martwe monitory, cod-weekly, macierz awarii), **skali** (multi-city), **bezpieczeństwa** (w toku).
- 1.0 zostawił **sieroty** — 2.0 je scalił w jeden rejestr z właścicielem.
- 1.0 ufał przyrządom po 49 werdyktach — 2.0 pokazał, że **meta-strażnicy** (warstwa „czy system żyje") sami kłamią/są martwi.

---

## 6. REKOMENDOWANA KOLEJNOŚĆ NAPRAW (każda = osobny mini-sprint ETAP 0→7 + ACK)

**Teraz-tanie, żywe, niskie ryzyko:**
1. ✅ (zrobione) re-enable 3 monitorów → **domknąć**: OnCalendar + dorejestrować cod-weekly/7×thr=None + OnFailure dla cod-weekly.
2. Diagnoza exit1 `cod-weekly` (pieniądze COD, co poniedziałek).
3. **Obie bomby TZ → ZoneInfo** przed 25-26.10 (konsolidacja fixed-offset do `ontime_lib`; jeden grep-strażnik „fixed CEST offset" w higienie). Data twarda.
4. Budżet wydajności + SLO + alert regresji (rozszerz istniejący canary — zero nowej infry).

**Przed autonomią (blokery 1. flipu — obowiązkowe):**
5. Twarda detekcja sukcesu przydziału (F) + E2E gastro_assign na sucho + idempotencja per-zlecenie (G).

**Higiena danowa/odpornościowa:**
6. Alerty DANOWE dla realnych trybów: sentinel-rate, pusta pula, stale-grafik, stale-pozycje, ledger-stall (2.B).
7. `cron_health` — oneshoty wołają `record_run_success` (koniec false-positive „failed"); watchdog czyta systemd `is_active` zamiast ufać failure-only ledgerowi.
8. Mina flagi `ENABLE_LOAD_PLAN_PURE_READ` → default `True` u callerów; docelowo retire flagi.

**Strukturalne (decyzja o ACK Pionów 2/3):**
9. Pas security (w toku) → top ryzyka.
10. **Zasoby na lata (L13 — KOREKTA: dysk NIE grozi zapełnieniem ~6 lat; rdzeń Ziomka chudy bez wycieku; swap 3G = Papu/współlokacja, nie Ziomek).** Realne: (a) **GC observability = ATRAPA** — `log_rotation.py` (rzekoma retencja 14d) NIE ISTNIEJE, nic nie woła → 324M/119 plików rośnie ~18M/dobę bez końca (najczystszy unbounded-append-only); (b) **readerzy niespójnie rotation-aware** — helper `_rotated_logs.py` istnieje, ale ≥15 narzędzi hardkoduje `[".jsonl",".jsonl.1"]` → przy 2. rotacji (dziś jeszcze nie zaszła) replaye/ML PO CICHU obetną okno (bomba klasy „min_delivered"); (c) **events.db `auto_vacuum=0` + FAŁSZYWA deklaracja „0 wierszy >30d"** — audit_log 80666 wierszy/81 dni, **przekroczy 90d ~10.07** (element datowany, jak bomby TZ); (d) governance CZĘŚCIOWO OK (MemoryMax 53/87, OnFailure 62/69≈90%), ale ŻADNA usługa nie ma natywnego `WatchdogUSec`; retire: cod-weekly/diag-snapshot-771M/checkpoint-tz-shadow/nogps-equal-watch/objm-smoke-trio. Szansa: `/mnt/storagebox` 1TB 99% wolny, hook cold-archive nigdy niepodpięty. **Ilości jednostek (ground-truth): ~69 svc + 62 timery dispatch** (moje wcześniejsze „115" mieszało .service+.timer+martwe; 05.07=16+12 → realny 5× rozrost).
11. Multi-city: rejestr `cities.json` + `city_id` jako kolumna 1. klasy (przed 2. miastem/Restimo).
12. Oś 05.07 (RC1 Postgres/M1, SPOF/DR-drill, SLO) — mierz KIEDY obowiązkowa (load-replay ×2/×5).

**Bramki nadchodzące (checki z 2.0):** 02.07 O2 (wyrównać kotwicę bundle_calib min(ck,pu); bug4-gate WAIT z fałszywego powodu) · 04.07 load-aware ETA (**brać kotwicę `assign` 83%, NIE `last` 4%** — L07) · przed re-enable Telegrama: naprawić 3-writer pending (O1) + klaster postpone (5 dead-paths).

---

## 7. STATUS PASÓW + LUKI
**Ukończone (WSZYSTKIE 14 + weryfikacje):** L01 rejestr · L02 GPS · L03 grafik · L04 zapis · L05 autonomia · L06 konsola-API · L07 instrumenty · L08 wyścigi/czas · L09 strażnicy · L10 hindsight · L12 security · L13 zasoby/systemd · ALERTY · PERF · MULTICITY · VERIFY_p1 (6 werdyktów: 4 CONFIRMED, 1 downgrade, 1 refuted-już-naprawione) · weryfikacje TZ/watchdog/worker/firewall/ports/stop w gł. wątku.
**Świadome granice:** STOP na dyspozytorni (Mailek/Papu poza); courier-app Kotlin i parcel-lane API-driven, wnętrze nieczytane; **zewnętrzna osiągalność portów vs Hetzner Cloud FW = NIEZWERYFIKOWANA** (krok 0 dla Adriana); materialność „ile/dzień" dla większości findingów = nie policzona (prawda przyciskowa ±3 min, dziś dormant). Wszystkie liczby = proxy chwili 01-02.07, dryfują.

## 8. DATOWANE ELEMENTY (kalendarz min czasowych — pilnuj dat)
- **~10.07** — events.db audit_log przekroczy 90 dni (auto_vacuum=0, fałszywa deklaracja „0>30d").
- **~10.07** — monitor route-order wygasa (golden L6.A z 01.07 już go zastąpił — NIE przedłużać).
- **02.07 / 03.07 / 04.07** — bramki O2 / objm / load-aware ETA (checki §6).
- **25-26.10** — koniec DST → uzbrajają się OBIE bomby TZ (gastro_assign + shadow_outcome_enricher + klaster uśpionych narzędzi).

**STOP — to audyt.** Poza re-enable monitorów (§2, za ACK) nic nie naprawiono/flipnięto. Naprawy = osobne mini-sprinty, protokół + ACK per fala.

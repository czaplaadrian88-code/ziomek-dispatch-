# 10 — PLAN PORZĄDKÓW (Faza 1) — **DO AKCEPTACJI**

**Data:** 2026-07-03 · **Źródła:** raporty Fazy 0 (`00`–`05` + `01a`) · **Status:** czeka na „AKCEPTUJĘ PLAN" Adriana.
**Zakres:** wyłącznie porządki i nawigacja — **zero zmian zachowania silnika**. Wszystko, co dotyka logiki, ląduje w §4 „WYMAGA DECYZJI".

---

## 0. RAMA WYKONANIA (jak bezpiecznie)

1. **Gałąź:** `audyt/wielkie-porzadki-2026-07-03` w worktree `wt-audyt` (C12b). Zmiany TRACKOWANE commitowane tam; do żywego `master` wchodzą **merge'em za ACK**. Porządki na plikach NIEtrackowanych (`.bak`, `__pycache__`) muszą być zrobione bezpośrednio w żywym drzewie — każdorazowo z listą plików przed kasacją.
2. **⚠ Merge do master = de facto DEPLOY:** timery spawnują świeże procesy co 30 s–5 min, więc każda zmiana `.py` na masterze działa NATYCHMIAST bez restartu. Dlatego: merge tylko **off-peak**, po pełnej regresji, z obserwacją `journalctl` ~15 min po merge; rollback = `git revert` (natychmiast podchwycony przez kolejne tiki).
3. **Testy:** pełna regresja `venvs/dispatch/bin/python -m pytest tests/ -q` **przed i po każdym kroku dotykającym `.py`** (baseline dziś: **4109/0/23s/11xf**). Kroki doc-only: bez testów (odnotowane uczciwie, nie udaję weryfikacji).
4. **Protokół #0 — mapowanie:** ETAP 0 (stan+testy zielone) = Faza 0 ✅; ETAP 3 (mapa kompletności) = per krok „grep wszystkich referencji przed ruszeniem pliku"; ETAP 4 (dowody) = regresja+py_compile; ETAP 6 (deploy) = merge off-peak za ACK; ETAP 7 = rollback git revert. Kroki dotykające importów silnika — tu ich prawie nie ma; te które są (R-20/R-21) świadomie ODŁOŻONE.
5. **MEMORY.md / TODO / CLAUDE.md:** wszystkie zmiany pokazywane jako diff przed/po i wprowadzane **dopiero po Twojej akceptacji** (per brief Fazy 2 pkt 5). Auto-push crona (`0 * * * *`) wyśle każdy merge na GitHub w ≤1 h — commituję tak, żeby każdy stan pośredni był bezpieczny.

---

## 1. REJESTR DŁUGU TECHNICZNEGO (skonsolidowany: D1–D14 + N1–N14 + A/B/E)

Nakład: S <30 min · M 0,5–2 h · L >2 h. Ścieżka: **F2**=Faza 2, **F3**=Faza 3, **WD**=wymaga decyzji (§4), **SPRINT**=poza audytem.

| ID | P | Pozycja (ref) | Wpływ | Ryzyko wykonania | Nakład | Ścieżka |
|---|---|---|---|---|---|---|
| R-01 | **P0** | 4 tokeny botów TG w trackowanym `ZIOMEK_MASTER_KB.md` + auto-push co 1 h na GitHub (D1/S5) | przejęcie botów | rotacja rusza żywe boty | M | **WD-1** → sprint security (już śledzony) |
| R-02 | **P1** | 3 zabłąkane routery aider w auto-ładowanych CLAUDE.md: `workspace/CLAUDE.md` (N5) + ogon `dispatch_v2/CLAUDE.md` l.1624+ (N13) + ruflo w `/root/.claude/CLAUDE.md` (N14) | sesje dostają reguły sprzeczne z #0 („nie pytaj Adriana") | żadne (usunięcie tekstu instrukcji) | S | **WD-3** → F2 (K2.4/K2.5) |
| R-03 | **P1** | Kanon flag rozjechany: 3 sprzeczne zapisy (N2) + niewidzialna warstwa `flags.systemd.env` (N9) + lookup `COMMIT_DIVERGENCE` „ON" a jest OFF (N12) + flaga nieudokum. (N10) | audytor/sesja czyta zły stan flag → złe decyzje | doc-only | M | **WD-4** → F2 (K2.5) |
| R-04 | **P1** | `MEMORY.md` 211 KB przy limicie ładowania ~24 KB — większość indeksu NIE wchodzi do sesji | nowe sesje ślepe na starszą pamięć | cięcie treści → wymaga zasad | L | **WD-5** → F2 (K2.6, diff przed/po) |
| R-05 | **P1** | Brak warstwy nawigacyjnej (cel audytu): ARCHITECTURE/CODEMAP/ADR/„START TUTAJ" | orientacja nowej sesji = minuty zamiast godzin | zero (nowe pliki) | L | **F2** (K2.1–K2.4) |
| R-06 | **P1** | Zombie-cron `tomtom_poc` pisze co 10 min do 4 plików TRACKOWANYCH (D2/B⚠4) → wiecznie brudne drzewo, churn w auto-pushu | szum git, ryzyko `git add -A` | decyzja o losie PoC | S | **WD-2** → F3 (K3.2) |
| R-07 | **P1** | Dane/artefakty w gicie: 31 jsonl/log w `eod_drafts`, `epaka_data` (D14), 2 backup-stray tracked (D12), `events.db` 0 B (D13), luki `.gitignore` (A§4) | churn, rozmiar, mylna zawartość repo | niskie (`git rm --cached`, pliki zostają na dysku) | M | **WD-7/WD-8** → F3 (K3.1–K3.3) |
| R-08 | P2 | 338 plików `.bak-*` (17,5 MB), deklarowana retencja 24 h vs kumulacja od kwietnia (D3) | szum, mylące „źródła" | niskie (rollback=tagi git) | S | **WD-6** → F3 (K3.4) |
| R-09 | P2 | 25× `__pycache__` w `eod_drafts` (D4) + brak wzorca w `.gitignore` | śmieci | zero | S | F3 (K3.4) |
| R-10 | P2 | `.aider.chat.history.md` 122 K (D6) + draft `courier_resolver` 1620 linii w eod_drafts (D7) | clutter | zero | S | **WD-7** → F3 (K3.3) |
| R-11 | P2 | ~5 martwych plików `.py` (B§4: `docs/deploy/ha-lite/backup_sentinel.py`, `ml_data_prep/bundle_geo_experiment.py`, 3× `tools/verify_obj_f*_2026-05-19.py`; kandydat `czasowka_proactive/handlers.py` do re-weryfikacji) | martwy kod | niskie przy grep-dowodzie; regresja po | M | **WD-7** → F3 (K3.5) |
| R-12 | P2 | Stare doki luzem: `docs/` (kwiecień), `AUDIT_2026-05-07/`, `AUDIT_2026-06-03/`, `SESSION_HANDOFF_2026-04-30`, `PRE_MERGE_CHECKLIST_2026-05-10` (A/D) | mylą nawigację (wyglądają na żywe) | odnośniki w memory → do aktualizacji | M | F3 (K3.6) |
| R-13 | P2 | 4 lokalizacje jednostek systemd w repo (A§1) | niejasne co jest wdrożone | ⚠ jeśli unit w `/etc` jest symlinkiem do repo — przenosiny go zrywają (pre-check!) | M | F3 (K3.7) |
| R-14 | P2 | ~8 wystrzelonych one-shot timerów `enabled` (B⚠7) + legacy `@reboot` w cronie (`/root/gps_server.py`…, B⚠5) | szum w list-timers; niejasne relikty | dotyka systemd → ACK per protokół C2 | S | **WD-10** → F3 (K3.8) |
| R-15 | P2 | Kolizja nazw `dispatch_state` repo↔workspace (A§0) | pułapka nawigacyjna (audytowała już nas!) | rename dotyka epaka_fetcher → tylko za WD | S | **WD-9**; README = F2 (K2.7) |
| R-16 | P2 | Drobne rozjazdy doków: N1 (etykieta „STATYCZNE"), N3 (nagłówki DRAFT wbrew zatwierdzeniu), N4 (DoD baseline 3611/2), N6 (martwa ścieżka backupu), N7 (baner todo 06-06) | mylą czytelnika kanonu | doc-only | S | **WD-15** → F2 (K2.5) |
| R-17 | P2 | `dispatch-telegram` OFF od 26.06 a kanon zwie go żywą warstwą 10 (B⚠1); flagi outward panelu =1 + `PANEL_ENVIRONMENT=staging` (N11) | dokumentacja kłamie o powierzchniach | wymaga odpowiedzi Adriana | S | **WD-11/WD-12** → F2 doc-fix |
| R-18 | P2 | 38 lokalnych gałęzi git (A§1) | szum | kasacja tylko zmergowanych, po liście | S | **WD-14** → F3 (K3.9) |
| R-19 | P2 | `dispatch-cod-weekly.service` FAILED co poniedziałek (N8/E) | martwy proces biznesowy | naprawa = logika (poza audytem) | — | **WD-13** → SPRINT (jest w todo) |
| R-20 | P3 | `schedule_utils.py` hub POZA pakietem, ~14 bare-importów (B§4) | nieczysta granica pakietu | zmiana importów silnika = pełny #0 | L | **ODŁOŻONE** (rekomendacja) |
| R-21 | P3 | `monitoring/` vs `observability/` — dwa domy jednej troski (A/D) | lekki bałagan organizacyjny | przenosiny modułów z ExecStart | M | **ODŁOŻONE** (rekomendacja) |
| R-22 | P3 | Ciężkie dumpy w `eod_drafts` (~30 MB: 06-17/06-22/06-29) (D5) | rozmiar drzewa (nie `.git`) | niskie (przeniesienie poza repo) | S | **WD-7** → F3 (K3.6b) |
| R-23 | P3 | Brak indeksu historii audytów (A§5) | trudno znaleźć poprzednie ustalenia | zero | S | F2 (K2.2 sekcja w CODEMAP) |

---

## 2. ARCHITEKTURA DOCELOWA

### 2a. Warstwy — zostają LOGICZNE (świadome odchylenie od „idealnej" przeprowadzki)
Kanon 10 warstw (`ZIOMEK_ARCHITECTURE.md`, zatwierdzony 01.07) **odpowiada kodowi** (raport 01 §2). **Fizycznego przenoszenia modułów silnika NIE planuję** w tym audycie. Uzasadnienie: (a) systemd startuje `-m dispatch_v2.X` — zmiana ścieżek = deploy silnika; (b) konsola i apka **importują dispatch_v2 jako bibliotekę cross-repo** (01 §3) — przenosiny łamią 3 repo naraz; (c) zysk nawigacyjny osiągamy CODEMAP-ą i ARCHITECTURE.md za ~0 ryzyka, przeprowadzką za ryzyko maksymalne. Pakietyzacja rdzenia (jeśli kiedyś) = osobny sprint pod pełnym #0 (R-20/R-21).

### 2b. Struktura katalogów — docelowa DELTA (tylko nie-kod)
| Obszar | Dziś | Docelowo |
|---|---|---|
| `docs/` | 40 przestarzałych plików z kwietnia zmieszanych z niczym | **żywa nawigacja**: `ARCHITECTURE.md`, `CODEMAP.md`, `decisions/` (ADR), `audyt/` (ten audyt) + **`docs/archive/`** (kwiecień, AUDIT_2026-*, stare handoffy z korzenia) z 1-plikowym indeksem |
| korzeń repo | 12 md + 187 `.bak` + stray pliki | md kanonu (CLAUDE, ZIOMEK_*, TECH_DEBT, LESSONS) + kod; zero `.bak` starszych niż próg, zero stray (`SESSION_HANDOFF*`→archive) |
| `systemd/` | 4 lokalizacje (root, `deploy/`, `deploy_staging/`, per-moduł) | jedno `systemd/` z podkatalogami per moduł + `systemd/README.md` („co wdrożone, co staged") — po pre-checku symlinków |
| `eod_drafts/` | dziennik sesji + dane + pycache w gicie | zostaje jako dziennik; dane/`__pycache__` gitignored; ciężkie dumpy → archiwum poza repo |
| `dispatch_state/` (repo) | kolizja nazw z żywym stanem | README-ostrzeżenie (min.) lub rename za WD-9 |

### 2c. Konwencje nazewnictwa (do zapisania w CLAUDE.md „START TUTAJ")
- Backup TYLKO `*.bak-pre-<opis>-<data>` (gitignored); zakaz `-wip`/`-proven-bak`/`.orig` — `.gitignore` dostaje wzorce łapiące odstępstwa.
- Artefakty sesji → `eod_drafts/YYYY-MM-DD/`; dane pomiarowe tamże ale NIEtrackowane; raporty „final" jawnie commitowane z opisem.
- Dokument = żywy ALBO ma na górze `STATUS: snapshot/archiwum + gdzie jest prawda` (wzorzec już stosowany — egzekwować).
- Jednostki systemd: `dispatch-*.{service,timer}`; w repo tylko w `systemd/`.

---

## 3. PLAN KROKÓW (małe, niezależne, w kolejności)

### FAZA 2 — warstwa nawigacyjna (bez przenoszenia czegokolwiek)
| Krok | Co | Ryzyko | Weryfikacja |
|---|---|---|---|
| K2.1 | `docs/ARCHITECTURE.md` — warstwy+przepływ+Mermaid (baza: 01 + ZIOMEK_ARCHITECTURE, z odesłaniem do kanonu) | zero | review Adriana |
| K2.2 | `docs/CODEMAP.md` — spis treści repo (baza: 00/01/03), sekcja „gdzie szukać czego" + indeks historii audytów | zero | test 5 pytań (§5) |
| K2.3 | `docs/decisions/` — ~8 ADR-ów odtworzonych z kodu/historii (10 warstw i HARD-before-SOFT; flagi „3 światy"; shadow-first→flip za ACK; always-propose; dispatch_state poza repo; venv dispatch/sheets; worktree multi-sesja C12; rollback=tagi+`.bak`) | zero | review |
| K2.4 | `CLAUDE.md` repo: sekcja **„START TUTAJ"** na górze (kolejność czytania, zakaz skanowania repo, wskaźnik #0/ARCHITECTURE/CODEMAP) + **wycięcie ogona-routera N13** + poprawka etykiet | niskie (plik kanonu) | diff do przeglądu; py_compile n/d |
| K2.5 | Pakiet poprawek doków za zgodą: `/root/CLAUDE.md` (N2/N6/N12 + wpis o CODEMAP), `ZIOMEK_REGULY_KANON.md` (akapit „3 światy flag" + `flags.systemd.env`), nagłówki DRAFT→ZATWIERDZONE (N3), DoD baseline (N4), baner todo (N7), kasacja/przepisanie `workspace/CLAUDE.md` (N5), sekcja ruflo (N14) | niskie | **diffy przed/po → wprowadzam po akceptacji** |
| K2.6 | Propozycja kompaktu `MEMORY.md` → cel <17 KB (1 linia/wpis, detale w topic-files; nic nie ginie — treść przenoszona, nie kasowana) | średnie (pamięć) | **diff przed/po → tylko po akceptacji**; inne sesje ostrzeżone wpisem |
| K2.7 | `dispatch_v2/dispatch_state/README.md` (ostrzeżenie o kolizji nazw) + nota o rozdwojeniu logów (`shadow_decisions` w `scripts/logs/`) w CODEMAP | zero | — |
| K2.8 | **Merge Fazy 2 do master za ACK** (doc-only) | niskie | `git diff master..` = tylko md; journal 15 min |

### FAZA 3 — porządki właściwe (każdy krok = osobny commit; wykonuję TYLKO pozycje zatwierdzone w §4)
| Krok | Co (ref) | Ryzyko | Weryfikacja |
|---|---|---|---|
| K3.1 | `git rm` 2 backup-stray tracked + `events.db` (R-07/D12/D13) + poszerzenie `.gitignore` (`*-wip-*`, `*-bak`, `*.db`, pycache-drafts) | niskie | grep 0 referencji przed; `git status`; pełna regresja (dotknięte drzewo kodu) |
| K3.2 | `git rm --cached` artefaktów danych: tomtom_poc (po WD-2), 31 jsonl/log w eod_drafts, `epaka_data/*` + `.gitignore` (R-06/R-07) | niskie — pliki ZOSTAJĄ na dysku, cron pisze dalej | `git status` przestaje pokazywać M; następny tick crona pisze bez błędu |
| K3.3 | Usunięcie clutteru: `.aider.chat.history.md`, draft `courier_resolver.n1-*` (R-10) | zero | lista przed kasacją |
| K3.4 | Sprzątnięcie `.bak` wg progu z WD-6 + `__pycache__` w eod_drafts (R-08/R-09) — **w żywym drzewie** (untracked) | niskie | lista plików przed; zostawione świeże wg progu |
| K3.5 | Usunięcie ~5 martwych `.py` (R-11) — per plik: `grep -rn` po dispatch_v2+panel+courier_api+systemd+cron = 0, `git log -1` | średnie | **pełna regresja po**; journalctl 15 min po merge (świeże tiki timerów) |
| K3.6 | Archiwizacja: `docs/*` (kwiecień)→`docs/archive/2026-04/`, `AUDIT_2026-*`→`docs/archive/`, stray handoffy z korzenia→archive; indeks + aktualizacja odnośników (grep po memory/CLAUDE.md) (R-12/R-22) | średnie (odnośniki) | grep starych ścieżek = 0 albo świadomie zostawione z notą |
| K3.7 | Konsolidacja `systemd/` w repo (R-13) — **pre-check:** `find /etc/systemd -type l -ilname '*dispatch_v2*'`; jeśli symlinki → krok modyfikowany (przenosiny+naprawa linku w 1 commicie, za osobnym ACK) | średnie | `systemctl cat` wybranych unitów przed/po identyczny; 0 broken symlinks |
| K3.8 | `systemctl disable` ~8 wystrzelonych one-shot timerów + decyzja o legacy `@reboot` (R-14) — po rekoncyliacji z shadow-jobs-registry (werdykty skonsumowane?) | średnie (systemd) | list-timers przed/po; **za ACK per C2** |
| K3.9 | Kasacja zmergowanych gałęzi git wg listy (R-18) | niskie | `git branch --merged master` = lista do zatwierdzenia |
| K3.10 | Raport końcowy + test 5 pytań (§5) + aktualizacja raportów audytu | zero | wynik w raporcie |

Co ~5 kroków: krótki raport postępu. Krok wymagający zmiany logiki → przerwany i dopisany do WD.

---

## 4. WYMAGA DECYZJI (nic z tego nie ruszam bez Twojego słowa)

| WD | Pytanie | Rekomendacja audytu |
|---|---|---|
| WD-1 | Tokeny TG w `ZIOMEK_MASTER_KB.md` (R-01): rotacja + usunięcie z pliku; czy też czyścić HISTORIĘ git (filter-repo = przepisanie SHA współdzielonego repo)? | rotacja+usunięcie TAK (sprint security); historii NIE ruszać teraz |
| WD-2 | Los PoC TomTom (R-06): wygasić cron? przekierować output poza repo? zostawić? | jeśli pomiar już niepotrzebny → wygasić; jeśli potrzebny → przekierować output poza repo |
| WD-3 | Routery aider: skasować `workspace/CLAUDE.md`? wyciąć ogon `dispatch_v2/CLAUDE.md`? usunąć sekcję ruflo z `/root/.claude/CLAUDE.md`? (3 osobne zgody — to Twoje pliki instrukcji) | TAK×3 (N5 sam `feedback_rules` oznaczał jako stale już 08.05) |
| WD-4 | Kanon flag: zatwierdzić zapis „3 światy" (silnik=flags.json po D3 / panel=flags.systemd.env / apka=conf+defaults) i poprawki N2/N9/N12 w MEMORY.md+REGULY_KANON+lookup? | TAK — jednoakapitowy kanon w REGULY_KANON, reszta = wskaźniki |
| WD-5 | Kompakt MEMORY.md <17 KB: zgoda na zasadę „1 linia/wpis, detal w topic-file" (diff zobaczysz przed wprowadzeniem)? | TAK — bez tego ~90% indeksu nie wchodzi do sesji |
| WD-6 | Próg retencji `.bak`: 14 dni (propozycja D)? | 14 dni; świeże zostają |
| WD-7 | Lista usunięć en bloc: D6, D7, D12, D13, ~5 martwych z R-11, pycache, ciężkie dumpy R-22 → do archiwum poza repo | zatwierdzić listę; wykonanie wg K3.x z dowodami |
| WD-8 | Artefakty danych: wystarczy `git rm --cached`+`.gitignore` (zostają w historii; `.git` NIE zmaleje) czy czyścić historię? | tylko `--cached` — higiena osiągnięta, zero ryzyka SHA |
| WD-9 | `dispatch_v2/dispatch_state/`: samo README-ostrzeżenie czy rename na `epaka_data_staging/` (dotyka epaka_fetcher/cron)? | README teraz; rename ew. później osobno |
| WD-10 | One-shot timery (8) disable + los legacy `@reboot` (`/root/gps_server.py`, `dispatch_control.py`, `fix_approvals.sh`)? | timery: disable po rekoncyliacji; @reboot: najpierw ustalić czy żywe (osobna weryfikacja) |
| WD-11 | Flagi outward panelu =1 (`COORDINATOR_*_LIVE`, `DISPATCH_PUSH_LIVE`) + `PANEL_ENVIRONMENT=staging` na żywym panelu — zamierzone? | odpowiedź potrzebna TYLKO do prawdy w dokumentacji flag |
| WD-12 | `dispatch-telegram` OFF od 26.06 — stan docelowy (konsola zamiast TG)? | jeśli tak → warstwa 10 w docs dostaje status „dormant/legacy" |
| WD-13 | `dispatch-cod-weekly` FAILED — zostaje w todo jako osobny temat (naprawa poza audytem)? | TAK (nie dotykam) |
| WD-14 | Kasacja zmergowanych gałęzi git — pokażę listę `--merged` do odhaczenia | zatwierdzić po liście |
| WD-15 | Nagłówki `ZIOMEK_{ARCHITECTURE,INVARIANTS,DoD}` DRAFT→„ZATWIERDZONE 01.07" — potwierdzasz zatwierdzenie? | TAK (spójne z CLAUDE.md l.8 i MEMORY) |
| WD-16 | R-20 (`schedule_utils` do pakietu) i R-21 (`monitoring`→`observability`) — odłożyć jako osobne sprinty pod pełnym #0? | ODŁOŻYĆ (dotykają importów silnika; zysk mały vs ryzyko) |

---

## 5. DEFINICJA UKOŃCZENIA (jak udowodnię na końcu)
- [ ] **Test nowej sesji:** 5 losowych pytań „gdzie jest obsługa X?" — odpowiedzi WYŁĄCZNIE z CLAUDE.md+CODEMAP.md, wynik w raporcie końcowym.
- [ ] Przykazanie #0, CLAUDE.md, MEMORY.md, TODO i kod spójne (N1–N14 zamknięte albo jawnie w WD).
- [ ] Zero martwego kodu/sierot poza listą WD.
- [ ] Pełna regresja zielona (≥4109/0) po każdym kroku dotykającym kodu; stan testów uczciwie w `04-TESTY.md`.
- [ ] Raporty `docs/audyt/` zaktualizowane; lista commitów + wykonane/odrzucone WD w podsumowaniu.

# FALA 1 / gc-observability — PLAN events.db (audyt 2.0 L13 pkt c)

**Tryb:** DOKUMENT (READ-ONLY recon + plan). ZERO wykonania. VACUUM/DELETE/PRAGMA-write
= koordynator, za ACK Adriana, off-peak. **Element datowany: ~2026-07-10.**
**Data:** 2026-07-02 ~07:00 UTC. Lane gc-observability (worktree wt-gc).

---

## 1. STAN ZASTANY (zmierzony READ-ONLY — sqlite3 SELECT/PRAGMA, zero zapisu)

Plik: `/root/.openclaw/workspace/dispatch_state/events.db`

| Metryka | Wartość |
|---|---|
| Rozmiar pliku | **31 633 408 B = 30,17 MiB** (page_size 4096 × page_count 7723) |
| `PRAGMA auto_vacuum` | **0 (NONE)** — skasowane strony NIE wracają do OS bez jawnego VACUUM |
| `PRAGMA journal_mode` | **wal** |
| `PRAGMA freelist_count` | **139 stron = 569 344 B ≈ 0,54 MiB** (wolne, nieodzyskane) |
| tabela `audit_log` | **80 666 wierszy**, span `created_at` **2026-04-11T11:37 → 2026-07-01T21:11 (81 dni)** |
| tabela `events` | 1 567 (pending 10 / processed 1 451 / failed 106), span 2026-05-27 → 2026-07-01 |
| tabela `processed_events` | 1 451, span 2026-06-30 → 2026-07-01 (okno 48h się rolluje) |

Wiek wierszy `audit_log` vs teraz (02.07 06:41 UTC):

| Próg | Liczba wierszy |
|---|---|
| starsze niż 90 dni | **0** |
| starsze niż 60 dni | 8 974 |
| starsze niż 30 dni | **42 773** |
| starsze niż 14 dni | 61 698 |

---

## 2. ⭐ KOREKTA L13: retencja audit_log JUŻ ISTNIEJE i DZIAŁA (nie „phantom")

L13/MASTER opisały events.db jako „fałszywą deklarację retencji". Ground-truth mówi
inaczej — **retencja jest zaimplementowana i żywa**:

- `event_bus.cleanup_audit_log(retention_days=90)` — **istnieje** (`event_bus.py:491`), kasuje `audit_log` starsze niż 90 dni, z pomijaniem peak-window (MP-#5).
- `event_bus.cleanup(retention_hours=48)` (events + processed_events) + `cleanup_broadcast(7d)` — też żywe.
- Runner: `event_bus_cleanup.py` → **`dispatch-event-bus-cleanup.timer` (daily 04:00 UTC, enabled)**. Ostatni bieg **2026-07-02 04:00:02** — log potwierdza `cleanup_audit_log: usunieto 0 audit_log entries (retention=90d)`.

**Dlaczego kasuje 0:** najstarszy wiersz ma 81 dni (< 90). Nic jeszcze nie przekroczyło progu 90d → runner poprawnie usuwa 0. To NIE jest zepsute — to poprawne „jeszcze nic do skasowania".

**Realny próg:** 2026-04-11 + 90 dni = **~2026-07-10**. Od tego dnia `cleanup_audit_log` zacznie codziennie usuwać ~1 dzień wierszy (~1000/dobę), utrzymując rolling okno ~90 dni. Tabela plateauuje na ~88-90 tys. wierszy — plik NIE urośnie znacząco powyżej dzisiejszych 30 MiB.

---

## 3. REALNE LUKI (co faktycznie do zamknięcia)

**L1 — Brak VACUUM (główna, ale NISKA pilność).**
`auto_vacuum=0` → strony zwolnione przez DELETE (dziś: events/processed_events co 48h; od ~10.07 także audit_log) trafiają na freelist i **nigdy nie wracają do OS**. Plik nie kurczy się, freelist rośnie przez kwartały. Dziś freelist = 0,54 MiB (znikome). Ryzyko dyskowe: **znikome** (L13: ~6 lat headroomu, plik plateauuje ~30 MiB). To hygiene/„na lata", nie pożar.

**L2 — Fałszywy komentarz w logrotate (l.130).**
`/etc/logrotate.d/dispatch-v2:130`: „audit_log >90d delete. Empirical: zero rows >30d, no-op." — **potrójnie błędny:** (a) retencja to 90d, nie 30d; (b) „zero rows >30d" — jest **42 773** wierszy >30d; (c) sugeruje że logrotate to obsługuje — obsługuje `event_bus_cleanup`. Komentarz mylący → do poprawy (doc-fix).

**L3 — Podwójny wpis w logu cleanup (kosmetyczny).**
`event_bus_cleanup.log` dubluje `DAILY_CLEANUP_DONE` (2× per bieg) — prawdopodobnie zdublowany handler loggera. Nie wpływa na retencję; do sprzątnięcia przy okazji.

---

## 4. PLAN (wykonanie = koordynator, za ACK, off-peak)

### KROK A — Weryfikacja że delete odpali ~2026-07-10 (bez zmiany kodu, DATOWANE)
Po 2026-07-10 sprawdzić:
```bash
grep "cleanup_audit_log" /root/.openclaw/workspace/scripts/logs/event_bus_cleanup.log | tail -5
```
Oczekiwane: `usunieto N` z **N>0**. Jeśli zostaje 0 po 10.07 — zbadać (timer 04:00 UTC = 06:00 Warsaw jest off-peak, nie powinien być skipowany). To najtańsza asekuracja daty.

### KROK B — Jednorazowy VACUUM (off-peak, ACK) — dopiero gdy JEST co odzyskać
Przed ~10.07 VACUUM odzyska tylko ~0,54 MiB (freelist) — **nie warto jeszcze**.
Sensowny po 10.07 (gdy audit_log zacznie churnować) LUB gdy freelist urośnie.
```bash
# 1. Backup online (WAL-safe), off-peak:
sqlite3 /root/.openclaw/workspace/dispatch_state/events.db \
    ".backup '/root/.openclaw/workspace/dispatch_state/events.db.bak-pre-vacuum-YYYYMMDD'"
# 2. VACUUM:
sqlite3 /root/.openclaw/workspace/dispatch_state/events.db "VACUUM;"
# 3. Weryfikacja:
sqlite3 .../events.db "PRAGMA freelist_count; PRAGMA page_count;"
```
- **Czas:** ~30 MiB → szacunkowo <2 s.
- **Lock:** VACUUM bierze EXCLUSIVE lock na czas trwania → writerzy events.db (dispatch-shadow,
  panel_watcher, state_machine, telegram_approver) zablokowani na te ~2 s. **Off-peak obowiązkowo**
  (np. 03:30 UTC, poza lunch/dinner peak — ta sama filozofia co MP-#5 skip w cleanup).
- **Miejsce:** VACUUM tworzy tymczasową kopię → do **~2× rozmiaru** (~60 MiB) w katalogu DB lub `SQLITE_TMPDIR`.
  Dostępne 68 GB → trywialne.
- **Backup:** obowiązkowy przed (komenda wyżej); rollback = przywrócić `.bak`.

### KROK C — Polityka auto_vacuum „na przyszłość" (decyzja + jednorazowe zastosowanie, ACK)
| Opcja | Mechanika | Ocena |
|---|---|---|
| **1 (rekomendowana)** | zostaw `auto_vacuum=0` + dodać **okresowy VACUUM** (np. miesięczny, off-peak, peak-aware) do istniejącego `event_bus_cleanup` LUB osobny rzadki timer | najprościej, bez narzutu na hot-path zapisu; plik i tak plateauuje |
| 2 | `PRAGMA auto_vacuum=INCREMENTAL` (wymaga 1× VACUUM by weszło) + okresowy `PRAGMA incremental_vacuum` | oddaje freelist bez pełnego rewrite, lżejszy lock; dobry kompromis |
| 3 | `PRAGMA auto_vacuum=FULL` (auto-reclaim co commit) | **NIE** — narzut per-commit na gorącej kolejce events (ciągłe zapisy) |

Rekomendacja: **Opcja 1** (miesięczny VACUUM off-peak) — wystarczająca przy plateau ~30 MiB.
⚠ Dodanie kroku VACUUM do `event_bus_cleanup.py` / zmiana `event_bus.py` = **POZA partycją
gc-observability** (pliki rdzenia event-bus). Należy do koordynatora/innego lane'u; tu tylko rekomendacja.

### KROK D — Poprawić fałszywy komentarz logrotate (doc-fix, ACK)
`/etc/logrotate.d/dispatch-v2:130` → zaktualizować na prawdę: „audit_log retention 90d
via event_bus_cleanup.py (dispatch-event-bus-cleanup.timer, daily 04:00 UTC); pierwsze
realne usunięcia ~2026-07-10; VACUUM ręczny/okresowy off-peak (auto_vacuum=0)."
⚠ Zapis do `/etc` = poza partycją (staging-only w tym lane). Do wykonania przez koordynatora.

---

## 5. PODSUMOWANIE DLA DECYZJI
- **events.db NIE jest pilnym pożarem.** Retencja audit_log żyje i zadziała ~10.07 sama.
  Dysk bezpieczny (plateau ~30 MiB). Pilność: **LOW**.
- **Data 10.07 = punkt weryfikacji** (KROK A), nie klif. Po niej: rozważ VACUUM (KROK B) +
  politykę auto_vacuum (KROK C) + doc-fix (KROK D).
- Wszystko powyżej = **ACK + off-peak + backup**. Kroki C/D dotykają plików spoza tego
  lane'u (event_bus*, /etc) → koordynator przydziela.

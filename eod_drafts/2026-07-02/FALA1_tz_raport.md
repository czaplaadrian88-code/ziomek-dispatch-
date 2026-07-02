# FALA 1 — lane TZ-consolidate — RAPORT (audyt 2.0, findingi C+D)

**Data:** 2026-07-02 · **Branch:** `fix/tz-consolidate` (worktree `/root/.openclaw/workspace/wt-tz`) · **Autor:** sesja lane TZ-consolidate
**Misja:** eliminacja fixed-offset Warsaw (`timezone(timedelta(hours=2))` / `WARSAW_OFFSET_HOURS` / stała `"+02:00"`) → `ZoneInfo("Europe/Warsaw")` (DST-safe CET/CEST). **2 datowane bomby uzbrajają się 25-26.10.2026** (koniec DST: zimą CET=+1, stały +2 kłamie o 1h).

---

## 1. STAN ZASTANY

- `ZoneInfo("Europe/Warsaw")` **działa na hoście** (probe: lato +2 / zima +1) mimo braku pip-pakietu `tzdata` — czyta systemową bazę stref; SILNIK już go używa wszędzie (panel_client/courier_resolver). Zamiana narzędzi na ZoneInfo = **zero nowego ryzyka**, zgodna z kanonem CLAUDE.md („Warsaw TZ zawsze via ZoneInfo") i rekomendacją audytu L08 §L2.
- Baseline regresji (nocny, ścieżka KANONICZNA): **3709 passed / 0 failed** / 23 skipped / 9 xfailed / 2 xpassed.
- Poprawny wzór DST istniał w repo: `tools/ontime_lib.py` (para CET/CEST via `warsaw_tz_for`).

---

## 2. ZMIANY (u źródła; jawne pliki)

| # | Plik | Było | Jest | Użycie / uwaga |
|---|---|---|---|---|
| 1 | `deploy_staging/scripts/gastro_assign.py` (STAGED kopia żywego, **POZA repo**) | `_WARSAW = timezone(timedelta(hours=2))` | `ZoneInfo("Europe/Warsaw")` | **BOMBA #1** — ścieżka `--time HH:MM` (l.152-160): zimą `now` liczony +2 → odbiór w najbliższej godzinie < `now` → guard „+1 dzień" → ~1410 min do panelu zamiast ~20. Zmiana = **1 linia** (+import) → minimalne ryzyko swapu. |
| 2 | `tools/shadow_outcome_enricher.py` | `WARSAW_OFFSET_HOURS = 2` | `WARSAW = ZoneInfo(...)` | **BOMBA #2 wg audytu — ale stała była MARTWA** (0 konsumentów, grep repo-wide; enricher liczy w UTC + różnice offset-ISO). Zmiana **byte-neutralna cały rok**, nie tylko lato. ⚠ ŻYWY timer `dispatch-shadow-enrichment` (oneshot 5 min). |
| 3 | `tools/freshness_shadow_monitor.py` | `WARSAW = timezone(timedelta(hours=2))` | `ZoneInfo(...)` | `_pt` (naive→Warsaw fallback), bucket dnia `astimezone(WARSAW)`, `now(WARSAW)`. Zima: poprawny dzień przy północy. |
| 4 | `tools/reassignment_shadow.py` | `WAR = timezone(timedelta(hours=2))` | `ZoneInfo(...)` | `tw()` używany WYŁĄCZNIE w różnicach (okno 3h) → **wynik offset-niezależny** (identyczny cały rok). |
| 5 | `tools/sequential_replay.py` | `WARSAW_OFFSET = "+02:00"` (+ **twin** `_per_hour` l.601 `[11:13])+2`) | `WARSAW = ZoneInfo(...)`; granice zmiany 10:00/22:00; `_per_hour` → `.astimezone(WARSAW).hour` | **DWA fixed-offsety w tym samym pliku** — naprawione RAZEM (kompletność bliźniaków). Naprawia też latentny wrap `hour>23`. |
| 6 | `tools/monitor_refloor_peak_2026_05_31.py` | inline `timezone(timedelta(hours=2))` (l.75) | stała `WARSAW = ZoneInfo(...)` + użycie | `_hhmm_warsaw` (display HH:MM w Telegram/raport). Zima: poprawny HH:MM. |
| 7 | `sprint2_analysis/_common.py` | `WARSAW_OFFSET = timedelta(hours=2)` | `WARSAW = ZoneInfo(...)`; `to_warsaw` | Konsumenci `override_patterns`/`propose_uptime_analysis` bucketują po `.hour` (peak) → zima poprawna klasyfikacja godzin. |
| + | `tests/test_tz_zoneinfo_consolidation.py` | — | NOWY, 14 testów | kill/mutacja/parytet/ratchet |

Każdy plik: `+ from zoneinfo import ZoneInfo`. **`py_compile` OK dla 7 plików + testu.**

---

## 3. DOWODY (liczby, nie deklaracje)

**Nowe testy: 14/14 PASS.**

**(a) ZIMOWY kill-test + MUTATION-CHECK (staged gastro, przypadek `--time 10:45`, `now`=15.12 10:30 CET):**
```
FIX (ZoneInfo Europe/Warsaw): 15   min   <- poprawnie
REVERTED (fixed +2)         : 1395 min   <- bomba (fałszywy +1 dzień)
```
→ rewers fixu przywraca bombę = strażnik behawioralny ma zęby (test `test_gastro_winter_mutation_reintroduces_bug`).

**(b) LETNI parytet (CEST=+2):**
- gastro: ZoneInfo == fixed +2 == **15 min** (identyczne).
- enricher **live dry-run, byte-parytet kanon↔worktree** (read-only): oba `scanned=1210 enriched=0 … outside_cutoff=1210` → **BYTE-IDENTICAL ✓**.
- per-plik konwertery: lato identyczne (parytet), zima poprawne (asercje w teście: freshness bucket dnia / reassignment różnica / _common `.hour` / monitor HH:MM / seq_replay granice+`_per_hour`).

**(c) Smoke:** `sequential_replay.py` (worktree, `PYTHONHASHSEED=0`) ładuje się — exit 0.

**(d) PEŁNA REGRESJA (z worktree):**
| Przebieg | passed | failed | uwaga |
|---|---|---|---|
| baseline nocny (KANON) | 3709 | 0 | ścieżka kanoniczna |
| **z moimi zmianami** (worktree) | **3700** | **23** | +14 nowych testów |
| bez moich zmian (`git stash -u`, worktree) | 3686 | 23 | **te same 23** |

→ **Moja delta = +14 passed, 0 NOWYCH faili.** 23 faili to **PRE-EXISTING artefakt worktree** w `tests/test_a2_selection_shadow.py` + `tests/test_courier_reliability.py`: oba hardkodują `Path(__file__).resolve().parents[2]` == `/root/.openclaw/workspace/scripts` — z worktree to `/root/.openclaw/workspace` → padają dla KAŻDEGO uruchomienia z worktree, niezależnie od moich zmian (dowód: identyczne 23 po `git stash -u`). **Rekoncyliacja: 3686 (worktree) + 23 (artefakt-ścieżki) = 3709 = baseline kanoniczny.** Po merge→kanon te 23 przechodzą; suma docelowa na kanonie ≈ 3709 + 14 = 3723.

---

## 4. DEPLOY ZA ACK (dokładne komendy — wykonuje koordynator, seryjnie, off-peak)

**A) 6 narzędzi repo (po merge `fix/tz-consolidate`→master):** biegają jako `python -m dispatch_v2.tools.*` (oneshoty/timery, `Type=oneshot`, świeży proces per tick — enricher `OnUnitActiveSec=5min`) LUB offline → **łapią nowy kod automatycznie na następnym ticku, ZERO restartu**.

**B) BOMBA #1 — podmiana ŻYWEGO `gastro_assign.py` (plik POZA repo):**
```bash
LIVE=/root/.openclaw/workspace/scripts/gastro_assign.py
cp "$LIVE" "$LIVE.bak-pre-tz-$(date +%Y%m%d-%H%M%S)"                 # backup
cp /root/.openclaw/workspace/scripts/dispatch_v2/deploy_staging/scripts/gastro_assign.py "$LIVE"   # (po merge)
/root/.openclaw/venvs/dispatch/bin/python -m py_compile "$LIVE"      # weryfikacja
```
**⚠ KOREKTA handoffu:** `gastro_assign` jest wywoływany jako **SUBPROCESS** (grep: `GASTRO_ASSIGN_PATH` + subprocess w `auto_koord.py` / `auto_assign_executor.py` / `telegram_approver.py`; **zero `import gastro_assign`**) → **NIE trzeba restartu `dispatch-telegram`**. Następne przypisanie odpala świeży proces = nowy kod od razu. (Handoff sugerował restart telegram „trzyma stary import" — to nietrafne dla tego pliku; unikamy zbędnego wrażliwego restartu.)

**Bezpieczeństwo daty:** wszystko behawioralnie neutralne do 25.10 (lato CEST=+2). Fix MUSI być live przed 25-26.10.

---

## 5. ROLLBACK
- **6 narzędzi + testy:** `git revert <hash commitu źródłowego>` (brak flag/stanu) → następny tick oneshotów wraca.
- **gastro (żywy):** `cp "$LIVE.bak-pre-tz-…" "$LIVE"` (natychmiast, następny subprocess = stary kod). Staged kopia w repo jest osobna od żywego pliku.
- Backup pracy (gdyby merge poszedł źle): `scratchpad/tz_tracked_changes.patch` + `scratchpad/untracked_backup/`.

---

## 6. POZA PARTYCJĄ (znalezione, NIE edytowane — do allowlisty ratcheta + follow-up)
- **`tools/drive_speed_overshoot_verdict.py:29`** `WARSAW = timezone(timedelta(hours=2))` — **ta sama klasa bomby**, poza moją partycją → w allowliście ratcheta; **rekomendacja: osobny lane fix→ZoneInfo przed 25.10.**
- **`tools/ontime_lib.py:45-46`** `timezone(timedelta(hours=1/2))` — **POPRAWNY wzór DST** (para CET/CEST w `warsaw_tz_for`), NIE bug → w allowliście z uzasadnieniem.
- **Finding D (atrybucja godzin +1h po DST):** w SAMYM enricherze stała była martwa → realne ryzyko (jeśli istnieje) leży w **konsumentach `drive_min_enriched.jsonl`** bucketujących po godzinie — poza moją partycją. **Follow-up: audyt konsumentów `drive_min_enriched.jsonl` pod kątem hour-bucketingu.**
- Ratchet allowlist (może się TYLKO kurczyć): `{tools/ontime_lib.py, tools/drive_speed_overshoot_verdict.py}`.

---

## 7. DoD
Fix u źródła ✔ · bliźniaki razem (seq_replay ×2) ✔ · py_compile 7 plików ✔ · testy behawioralne+mutacja+ratchet 14/14 ✔ · parytet letni (byte-identical) ✔ · pełna regresja 0 nowych faili (23 pre-existing udowodnione stash-em) ✔ · deploy-za-ACK + rollback gotowe ✔ · POZA PARTYCJĄ zaraportowane ✔. **DEPLOY = seryjny, za ACK (koordynator).**

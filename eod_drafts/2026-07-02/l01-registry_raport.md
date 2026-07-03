# L0.1 — rejestr flag KOMPLETNY + fingerprint-reconciliation (raport)

**Branch:** `fix/l01-registry` · **Data:** 2026-07-02/03 · **Tryb:** build-only (zero flip/restart/push)
**Partycja:** WYŁĄCZNIE `tools/flag_registry.py`, `tools/flag_fingerprint_check.py` + 2 pliki testów.
**Wznowienie** przerwanego pasa (poprzednik padł na limicie, praca niezacommitowana w worktree).

---

## 1. Co zastałem i co zweryfikowałem

Poprzednik zostawił (niezacommitowane): rozbudowę `flag_registry.py` (klasyfikacja rozjazdów
env-frozen: `SERVICE_SCOPED`/`KNOWN_DIVERGENCES`/`INTENTIONAL`, balanced-paren `_extract_paren_body`,
checker `completeness_gaps`) + NOWY `flag_fingerprint_check.py`. Oceniłem: kierunek dobry, ale
**dwie dziury** ujawnione dopiero po uruchomieniu na żywym systemie (patrz §3-§4). Dokończyłem
U ŹRÓDŁA, dodałem testy + mutację, przeszedłem pełną regresję.

## 2. Pokrycie rejestru — 74/28 → 438/127, BRAKI 19 → 0

| miara | KANON (przed) | worktree (po) |
|---|---|---|
| wierszy w rejestrze | 396 | **438** |
| flagi decyzyjne Z WIERSZEM | **28** | **127** |
| BRAKI POKRYCIA (decyzyjna/numeryczna ETAP4 bez wiersza) | **19** | **0** |
| ROZJAZDY (metryka #4, OTWARTE) | 6 (nieklasyfikowane) | **1** (known-open) |

Skok 28→127 (decyzyjne 23→102, numeryczne 5→25) = fix **balanced-paren**: naiwne `\((.*?)\)`
ucinało krotkę `ETAP4_DECISION_FLAGS` na pierwszym `)` w komentarzu → gubiło 79/102 flag decyzyjnych.
Skok 396→438 wierszy + domknięcie 19 braków = fix **skanera** (§3).

## 3. 6 rozjazdów metryki #4 — diagnoza + werdykt KAŻDEGO

Oryginalne 6 to env-frozen-subset (flaga w env JEDNEGO unitu, reszta liczy defaultem). Po klasyfikacji:

| flaga | werdykt | uzasadnienie |
|---|---|---|
| `CZASOWKA_MAX_EMIT_PER_TICK` | accepted-scoped | konsument w 1 serwisie (czasowka_scheduler.py:54), gałąź emisji biegnie tylko w ticku czasówki |
| `CZASOWKA_RETROACTIVE_HOURS` | accepted-scoped | okno triggera, tylko tick czasówki |
| `CZASOWKA_TELEGRAM_DRYRUN` | accepted-scoped | dry-run wysyłki, tylko tick czasówki |
| `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` | accepted-scoped | retiming committed, tylko plan-recheck (roadmapa: migracja do flags.json — poza partycją) |
| `ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH` | accepted-scoped | live-ETA refresh, tylko plan-recheck |
| `USE_V2_PARSER` | **known-open** ⛔ | GENUINE cross-service: `panel_client.py:93` moduł-level; panel_client importują shadow/czasowka/state_machine — env=1 tylko w watcher → inne serwisy parsują v1 gdy same wołają parser. Domknięcie = migracja do ETAP4 hot-reload + ACK (poza partycją L0.1). |

Metryka #4 (`ROZJAZDY (N)` parsowana przez entropy_dashboard) = **OTWARTE (open+known-open)**;
accepted-scoped/intentional NIE liczą się. Efekt: 6 nieklasyfikowanych → **1 known-open** (śledzony,
z planem domknięcia) + 11 accepted (z ownerem/why).

### 3a. BRAKI POKRYCIA (19) — fix U ŹRÓDŁA, nie tautologia

Completeness ujawnił 19 flag decyzyjnych ETAP4 BEZ wiersza. Diagnoza: `scan_common` (regex env-get)
je gubił, bo definiowane:
- **15 literałem** (`ENABLE_O2_CAPZ_RESEQ = False`, `AUTO_ASSIGN_MAX_PER_HOUR = 6`, …),
- **4 przez `os.environ.get`** (regex wymagał `_os.environ.get` — 34 defów w common.py używa formy bez podkreślenia).

Fix (w partycji tools): (a) broadening `_DEF_RE` → `(?:_os|os)\.environ\.get`; (b) nowy
`scan_literal_defaults(names)` — łapie literały TYLKO dla znanych flag ETAP4 (nie wciąga setek
stałych). **Świadomie NIE seedowałem `names` listą ETAP4** — completeness=0 ma być DOWODEM pełnego
skanu, nie tautologią. Test `test_completeness_no_gaps_live` = strażnik: nowa flaga zdefiniowana
sposobem, którego skaner nie widzi → gap>0 = FAIL.

## 4. fingerprint-reconciliation — 1. bieg NA ŻYWO (Z LICZBAMI)

`flag_fingerprint_check.py` łączy 4 źródła: flags.json ↔ FLAG_FINGERPRINT z logów per serwis ↔
Environment= drop-inów ↔ rejestr. Format fingerprintu zweryfikowany z realnym logiem
(`proc=shadow ENABLE_X=1 …`, 4 serwisy: shadow/plan-recheck/panel-watcher/czasowka).

**Naiwny „last line" był NIEDETERMINISTYCZNY i KŁAMAŁ.** Pierwszy bieg raportował **41 rozjazdów**
(38 JSON-DRIFT czasówki), a ręczna kontrola pokazała że flaga NIE dryfuje. Root: **czasowka
emituje ~22-40% fingerprintów = common.py DEFAULTY** (flags.json overrides NIE zaaplikowane;
39 flag =1/63 =0 = kod-defaulty, NIE puste). „Last line" trafiał losowo w cold-snapshot →
38 fałszywych per-flag drift.

Fix (w partycji tools): klasa **INTERMITTENT-COLD** — wholesale rozjazd (≥`COLD_DRIFT_MIN`=15 flag
naraz ≠ flags.json) collapse do 1 findingu z częstotliwością z recent-lines; per-flag JSON-DRIFT
tego proc pominięty gdy last=cold. Dodatkowo naprawiono **pułapkę domyślnego argumentu**
`load_flags_json(path=FLAGS_JSON)` (default bindowany w def-time → monkeypatch nieskuteczny) w OBU
tool-plikach (bliźniacza ścieżka).

**Wynik 1. biegu (deterministyczny, uczciwy): 5 rozjazdów zamiast 41:**

| klasa | ile | co |
|---|---|---|
| INTERMITTENT-COLD | 1 | **czasowka: 12/30 ostatnich emitów = common.py defaulty, flags.json overrides nie zaaplikowane** |
| COVERAGE-GAP | 3 | panel-watcher stale (start 07-02 11:46, przed dodaniem `ENABLE_O2_CAPZ_RESEQ`/`ENABLE_SLA_ANCHOR_UNIFIED`/`ENABLE_SLA_GATE_READY_ANCHOR`) — restart rekoncyliuje |
| JSON-DRIFT | 1 | panel-watcher `ENABLE_V328_POISON_ALERT` (benign stale: flags.json nowszy niż start procesu) |

### ⚠ ODKRYCIE DO ESKALACJI (poza partycją L0.1)
**`dispatch-czasowka.service` intermittentnie liczy złymi flagami** — ~22-40% ticków fingerprint =
common.py defaulty zamiast flags.json (shadow/plan-recheck/panel-watcher praktycznie nigdy: 0/79,
1/6313, 0/23). Objaw: flag-load w module czasówki part-time nie aplikuje flags.json (prawdopodobnie
ścieżka/CWD/wyścig przy `load_flags`). Skutek: czasówki mogą zapadać na domyślnych flagach.
**Naprawa = ścieżka ładowania flag w czasowka_scheduler/common.py — POZA partycją L0.1 (common.py
zakazany), wymaga osobnego tematu + protokół + ACK.** Tool to SUFACE'uje jako 1. klasę findingu.

## 5. Testy + mutacja ×2

- **`test_flag_registry_f3.py`**: +7 testów (os/_os regex, balanced-paren over comment-parens,
  literal scanner scoped, **completeness=0 live** [strażnik], no-unclassified-divergence [strażnik],
  open/accepted partition, metryka #4 = open-only). 12/12 PASS.
- **`test_flag_fingerprint_check.py`** (NOWY, 9 testów, IO HERMETYCZNE tmp_path + assert ANTY-PROD):
  `_fjson_bool`, `_drift_count`, JSON-DRIFT-single-benign, **INTERMITTENT-COLD collapse**, ENV-DEAD,
  COVERAGE-GAP, clean-system, render/jsonl, live-smoke. 9/9 PASS.
- **Mutacja ×2 (dowód że testy mają zęby):**
  - M1 (registry): literal-scanner ignoruje liczby → `test_scan_literal` + `test_completeness` **RED**; restore → GREEN.
  - M2 (fingerprint): `COLD_DRIFT_MIN` 15→1 → `test_json_drift_single_flag_benign` **RED**; restore → GREEN.
  - Pliki po restore bajt-w-bajt identyczne (diff pusty).

## 6. Regresja

- Moje 21 testów: **21/21 PASS** deterministycznie (izolowany bieg).
- Pełna suita przez worktree (pkgroot): **4046 passed / 23 „failed" / 23 skipped / 9 xfailed / 2 xpassed.**
- 23 „failed" = **SkipTest artefakty worktree** (`test_a2_selection_shadow` + `test_courier_reliability`
  szukają modułów pod hardcoded `/root/.openclaw/workspace/dispatch_v2/tools/…` — ścieżka nieistniejąca
  pod pkgroot; NIE importują moich plików). **IDENTYCZNE na KANONIE i worktree** (subset 23 failed/2 passed
  na obu) = zero delta od mojej zmiany. To dokładnie „+~23 artefakty worktree" z baseline zadania.
- **Regression delta mojej zmiany = 0.**

## 7. Pliki

- `tools/flag_registry.py` (M) — broadening `_DEF_RE`, `scan_literal_defaults`, seed defs literałem w `build_registry`, `load_flags_json` bez pułapki default-arg. (Klasyfikacja rozjazdów + completeness = poprzednik, zweryfikowane.)
- `tools/flag_fingerprint_check.py` (NOWY) — INTERMITTENT-COLD (`_recent_fingerprints`, `_drift_count`, `COLD_DRIFT_MIN`), `load_flags_json` bez pułapki.
- `tests/test_flag_registry_f3.py` (M) — +7 testów.
- `tests/test_flag_fingerprint_check.py` (NOWY) — 9 testów.

## 8. Za ACK / następne (poza partycją L0.1)
1. **ESKALACJA:** czasowka intermittent-cold flag-load (§4) — osobny temat, protokół + ACK.
2. `USE_V2_PARSER` known-open — migracja do ETAP4 hot-reload + ACK (parser = behavior-affecting).
3. Integracja `flag_fingerprint_check` jako strażnik systemd/timer (read-only) — po merge.
4. Metryka #4 entropy_dashboard: liczba spadła 6→1 (dashboard parsuje `ROZJAZDY (N)` = OTWARTE, bez zmian w dashboardzie).

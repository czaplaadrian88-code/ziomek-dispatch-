# B_plan — Z-P1-05 Faza A: Kanoniczna tożsamość kuriera (RAPORT PLANU)

Agent B (builder), Sprint 4. Faza 1 = plan (zero edycji worktree). Worktree: `/root/sprint4_wt/wt-identity/dispatch_v2` (branch `sprint4/z-p1-05-identity`, baza `c2bde58`). Pakiet `identity/` NIE istnieje (kanon ani worktree) — przestrzeń wolna (potwierdzone A1 §C-1).

Źródła przeczytane: A2_identity.md, A1_kolizje.md, karta Z-P1-05 (backlog l.238-249), `worker.resolve_cid`, `panel_roster._score/match_name_to_cid`, `courier_admin.add_new_courier/derive_alias`, `new_courier_pairing._resolve_cid_trusted/verify_courier_wired`, `courier_info.resolve_courier_query`, `daily_accounting/config.EXCLUDED_CIDS`. Recon read-only kształtów: courier_tiers (`_meta`+64 CID), whitelist CONDITIONAL, shift_ignored_names, grafik_full_names (int), kurier_ids (wszystkie wartości int, 121), courier_names ({cid:str→name}, 46), daily kurier_full_names ({alias→full_name str}, 55 live), courier_api.db (istnieje, `dispatch_state/courier_api.db`), rozjazd git-vs-live kurier_full_names (POTWIERDZONY: live +`Darek os`/+`Kacper Sz`, −`Dawid Kr` vs HEAD).

---

## 1. WRITE-SET (wszystkie NOWE — zgodne z zatwierdzoną listą; nic poza `identity/` + własne testy/fixtury/raport)

**Pakiet (8 plików):**
1. `dispatch_v2/identity/__init__.py`
2. `dispatch_v2/identity/normalize.py`
3. `dispatch_v2/identity/sources.py`
4. `dispatch_v2/identity/schema.py`
5. `dispatch_v2/identity/registry.py`
6. `dispatch_v2/identity/collisions.py`
7. `dispatch_v2/identity/report.py`
8. `dispatch_v2/identity/onboarding.py`

**Testy (3 pliki, nazwy zp105 unikalne):**
9. `tests/test_identity_registry_zp105.py`
10. `tests/test_identity_collisions_zp105.py`
11. `tests/test_identity_onboarding_zp105.py`

**Fixtury (ANONIMIZOWANE, `tests/fixtures/identity/`):**
- `kurier_ids.json`, `kurier_piny.json`, `courier_names.json`, `courier_tiers.json`, `grafik_full_names.json`, `shift_ignored_names.json`, `courier_whitelist_v1.json`
- `daily_kurier_full_names_git.json` + `daily_kurier_full_names_live.json` (para do rozjazdu git↔live)
- `excluded_cids.json` (odpowiednik EXCLUDED_CIDS jako jawny override do testów hermetycznych)

**Raport końcowy w repo:**
- `eod_drafts/2026-07-10/SPRINT4_ZP105_IDENTITY_RAPORT.md`

Nie tworzę root `conftest.py` ani nie dopisuję do `tests/conftest.py` (A1 §5 SZARE — styk z Sprint 3). Testy self-lokalizują fixtury przez `Path(__file__).parent/"fixtures"/"identity"` (C12e).

---

## 2. ARCHITEKTURA per plik (1-2 zdania)

- **`__init__.py`** — marker pakietu + re-export publicznego API (`norm`, `resolve_worker`, `resolve_panel_roster`, `CourierRecord`, `build_registry`, `Registry`, `run_collisions`). Zero I/O na imporcie (bez side-effectów).
- **`normalize.py`** — JEDNO źródło kontraktu `norm(s)=(s or "").strip().rstrip(".,;:").lower()` (BEZ składania diakrytyki) + DWIE parametryzowane, czyste strategie resolvera odtworzone 1:1: `resolve_worker(name, mapping, *, bare_key_strict=False)` (kolejność exact→exact-ci→score; score ×10 gdy `s_last.startswith(a_last)`, ×5 gdy odwrotnie, goły klucz-imię=1, remis=None/ambiguous; `bare_key_strict` odtwarza `_resolve_cid_trusted` przez odsianie kluczy jednowyrazowych) oraz `resolve_panel_roster(full_name, roster)` (pierwsze imię musi się zgadzać, prefiks nazwiska DWUKIERUNKOWO ×10/×10, goły first-name=1, remis=ambiguous). Zero logów/side-effectów (wersja pure; logowanie legacy pominięte świadomie).
- **`sources.py`** — `default_paths(state_root=None, repo_root=None)` licząca ścieżki LATE-BOUND (env `ZIOMEK_STATE_ROOT`/`ZIOMEK_REPO_ROOT` → fallback kanon; repo_root self-lokalizuje `Path(__file__).resolve().parents[1]`), plus 10 read-only loaderów, KAŻDY z jawnym argumentem `path` (żadnych ścieżek w defaultach sygnatur — C17). CID kanonicznie `str`; sqlite `courier_api.db` opcjonalny (brak pliku → `None` + adnotacja, nie wyjątek); `load_excluded_cids(source=None)` late-import `daily_accounting.config` lub jawny override.
- **`schema.py`** — `@dataclass CourierRecord`: `cid:str` (KLUCZ, niezmienny), `aliases:Dict[source,List[str]]` (source ∈ panel/gps/grafik/app/accounting), `full_name:Dict[source,str]`, `tier:Optional[str]`, `pin_present:bool`+`pin_last2:Optional[str]` (NIGDY pełny PIN), `active:bool`, `excluded:bool`, `is_coordinator:bool`, `added_at`. `validate_record()` + `to_public_dict()` (redaguje PIN do last2) do raportów.
- **`registry.py`** — `build_registry(bundle)` scala 10 źródeł w `{cid_str: CourierRecord}` (fail-open per źródło); `Registry.resolve(name, profile="worker"|"panel_roster", **kw)`, `by_cid(cid)`, `all_records()`. Resolve deleguje do strategii z `normalize.py` nad odpowiednim mappingiem (worker→kurier_ids `{alias:cid}`; panel_roster→`{cid:name}` z courier_names/tiers).
- **`collisions.py`** — czyste walidatory zwracające ustrukturyzowane findings: (a) znormalizowany alias→>1 CID; (b) zbiór GOŁYCH kluczy-imion (poison) WYLICZANY (jednowyrazowe klucze kurier_ids → dziś 8, nie hardcode); (c) rozjazd full-name cross-source z odsianiem skrót-vs-pełne (prefiks nazwiska = ten sam człowiek, NIE konflikt); (d) CID bez courier_names / bez tieru; (e) „duplikat PIN" (interpretacja: alias związany >1 PIN-em lub PIN→alias nierozwiązywalny do CID); (f) rozjazd git↔live daily kurier_full_names (przyjmuje DWA dicty). Bez subprocessu — czyste.
- **`report.py`** — CLI `python -m dispatch_v2.identity.report`: domyślnie raport braków (nazw/tierów) + kolizji (a-f) w tekście/`--json`; `--parity` importuje legacy `worker.resolve_cid` i `panel_roster.match_name_to_cid` przez pkgroot i porównuje ich WYNIKI z registry na wszystkich 121 aliasach + nazwiskach grafik_full_names (N zgodnych / lista rozjazdów). Read-only: monkeypatch `state.append_match_debug_log`→no-op (legacy pisze log przy ambiguous); dla (f) używa read-only `git show HEAD:daily_accounting/kurier_full_names.json` w podanym repo (fail-open gdy brak git). `--state-root`/`--repo-root` override (live run wskaże kanon).
- **`onboarding.py`** — CLI `onboard`/`offboard`. Onboard: waliduje kolizje PRZED zapisem (nowy alias vs registry + goły-klucz poison), potem KOMPONUJE `courier_admin.add_new_courier` (lazy import — NIE reimplementuje zapisu); **default `--dry-run`** = diff 5 plików (alias z `courier_admin.derive_alias`, PIN pokazany jako `<nowy-unikalny>`, wpis tiers=new, self-heal grafik_full_names); realny zapis wymaga `--apply` I env `IDENTITY_ONBOARD_ALLOW=1` — w tym sprincie NIGDY nie uruchamiam --apply. Offboard: generuje PLAN (wpis shift_ignored_names + EXCLUDED_CIDS + dezaktywacja) bez zapisu.

---

## 3. TESTY + FIXTURY (hermetyczne, tylko fixtury `tests/fixtures/identity/`; zero odczytu dispatch_state)

- **`test_identity_registry_zp105.py`** — (1) kontrakt `norm`: strip/rstrip kropki, diakrytyka NIE składana (`Paweł Ś…`≠`Pawel Sc`); (2) `resolve_worker` exact/exact-ci/score, goły klucz=1, remis=None; (3) `resolve_panel_roster` ×10/×10, remis=ambiguous; (4) **case ROZBIEŻNOŚCI ×5-vs-×10** — input, w którym worker i panel_roster dają RÓŻNY wynik (dowód że OBA profile zachowane 1:1, semantyka NIE zunifikowana); (5) `bare_key_strict` zmienia wynik dla gołego klucza; (6) `build_registry` z fixtur → rekord per CID, `by_cid`, `all_records`, cid jako `str`, aliasy wersjonowane per źródło, `is_coordinator`/`excluded`; (7) fail-open (brak sqlite/źródła → rekordy dalej budowane); (8) opcjonalny test parytetu importujący legacy (guard `try/except`→skip) na fixturach.
- **`test_identity_collisions_zp105.py`** — (a) alias→>1 CID wykryty; (b) poison bare-keys wyliczone z fixtury (nie hardcode); (c) Kuba/Jakub = konflikt zgłoszony, ale skrót `Jakub Ol` vs `Jakub Olchowski` odsiany; (d) braki courier_names/tier; (e) duplikat PIN; (f) rozjazd git↔live z pary fixtur.
- **`test_identity_onboarding_zp105.py`** — dry-run zwraca diff 5 plików bez zapisu (spy na `add_new_courier` NIE wołany); onboard odrzuca kolizję (alias istniejący / poison) PRZED kompozycją; guard `--apply` bez env → odmowa (asercja, realnie nie stosujemy); offboard zwraca plan (3 elementy) bez zapisu.

**Fixtury** reprodukują KLASY przypadków anonimowo (zmyślone nazwiska): goły-klucz-poison (kilka jednowyrazowych kluczy), Kuba/Jakub (podwójny alias, różny first-name string, ten sam CID), diakrytyka Ś vs ascii-skrót, brak w courier_names, brak tieru, duplikat PIN, multi-alias, `_meta` w tiers (ma być pomijany), wpis `coordinator`, para git/live z 3-kluczowym rozjazdem.

---

## 4. RYZYKA / ODSTĘPSTWA OD BRIEFU

- **R1 — parytet panel_roster bez sieci:** live panel_roster roster pochodzi z gastro (network). Do `--parity` i registry buduję `{cid:name}` z `courier_names.json` (read-only, bez sieci) i podaję jawnie do `match_name_to_cid(name, roster=...)`. Dowodzi parytetu ALGORYTMU scoringu (×10/×10 + remis), nie samego fetchu rosteru. Odnotowane w raporcie.
- **R2 — legacy ma side-effecty:** `worker.resolve_cid` przy remisie woła `state.append_match_debug_log` (zapis do state) + import `worker` tworzy logger piszący do `logs/`. W `--parity` monkeypatchuję log→no-op (gwarancja read-only). Testy hermetyczne domyślnie NIE importują legacy (opcjonalny test guarded-skip), zawsze podaję jawny dict (brak odczytu dispatch_state).
- **R3 — git↔live daily kurier_full_names:** „live" = plik z working-tree kanonu (ma niescommitowaną zmianę usera — A1 §C, NIE stage'uję), „git" = `git show HEAD:…` w kanonie (read-only subprocess w report.py). Rdzeń collisions bierze dwa dicty (hermetyczny); subprocess tylko w report.py, fail-open bez gita.
- **R4 — prowieniencja aliasów:** kurier_ids trzyma skróty panelu I pełne imiona grafiku razem; do 5 etykiet briefu dzielę je heurystyką (skrót nazwiska ≤2 znaki / goły → `panel`; pełne nazwisko / obecne w grafik_full_names → `grafik`). Etykieta `gps` zarezerwowana (courier_api = `app`; GPS trzyma cid, nie osobne nazwy). To klasyfikacja, nie zmiana danych; udokumentowana.
- **R5 — is_coordinator / excluded:** cid 26 NIE ma wpisu w courier_tiers → `is_coordinator = cid in {"26"} OR tier.coordinator truthy`. EXCLUDED_CIDS z `daily_accounting/config.py` (late-import) → normalizacja do `str`.
- **R6 — semantyka „duplikat PIN":** kurier_piny to `{pin:alias}` (klucze JSON z natury unikalne). „Duplikat" definiuję jako: alias związany >1 PIN-em ALBO PIN→alias nierozwiązywalny do CID. Udokumentowane; dziś 0 na żywo (spójne z A2).
- **R7 — ścieżki report.py:** defaulty self-lokalizują (repo_root z `Path(__file__)`), więc uruchomiony z worktree widziałby daily kurier_full_names = HEAD. Do RAPORTU (żywe liczby) uruchomię z jawnym `--state-root /…/dispatch_state --repo-root /…/scripts/dispatch_v2` (read-only), by złapać realny working-tree kanonu.
- **R8 — courier_api.db:** duży (30 MB), read-only `sqlite3` connect w trybie RO; wyciągam wyłącznie `courier_id`+`courier_name` (dla aliasów `app`). Nie modyfikuję, nie kopiuję. Brak/lock → pomiń z adnotacją (fail-open).

Brak odstępstw od zakazów: zero edycji istniejących plików, zero runtime/flag/systemctl, zero zapisu do dispatch_state/flags, PIN-y tylko last2, brak `--apply`, brak pushu.

---

## 5. CZEGO ŚWIADOMIE NIE ROBIĘ (Faza B)

- Nie wpinam registry w żaden istniejący moduł (courier_resolver, common, telegram_approver, worker, daily_accounting, panel) — pakiet czysto additywny, runtime go nie importuje.
- Nie unifikuję dwóch resolverów — zostawiam rozbieżność ×10/×5 (worker) vs ×10/×10 (panel_roster) 1:1 i ją dokumentuję (unifikacja = Faza B).
- Nie podmieniam 6 inline kopii `_norm`.
- Nie uruchamiam `--apply`; nie zapisuję dispatch_state/flags; nie backfilluję ani nie wycofuję legacy `courier_names.json`.
- Nie konsoliduję zdenormalizowanego `courier_name` w courier_api.db (5 tabel).
- Zero zmian CID i historycznych rozliczeń.

---

## 6. ŚRODOWISKO / DoD (Faza 2, po „GO")

- Testy: `PKG=/root/sprint4_wt/pkgroot_identity`; symlink dispatch_v2→worktree + flags.snapshot; `cd worktree/dispatch_v2 && ZIOMEK_SCRIPTS_ROOT=$PKG /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q -rf -p no:cacheprovider`. Cel: **0 failed, passed ≥ 4710 + nowe** (baseline 4710/24skip/10xfail).
- py_compile pakietu + `python -m dispatch_v2.identity.report` na żywych danych (read-only) do liczb w raporcie + `--parity` (oczekiwane 0 rozjazdów).
- Commity jawne ścieżki (`git add <pliki>`, NIGDY `-A`), stopka `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, wszystko zacommitowane przed końcem, BEZ push.
- Rollback = `git revert` commita (pakiet nieużywany przez runtime → zero wpływu).

**Czekam na „GO" lidera przed jakąkolwiek edycją.**

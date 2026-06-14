# HANDOFF → sesja ZIOMKA: rebase + naprawa `auton/ziomek-hygiene` (S3)

**Data:** 2026-06-13 · **Od:** sesja porządkowa (recovery sesji 115 padłej na OOM 14:32) · **Dla:** następna sesja Ziomka
**Decyzja Adriana:** „przekaż S3 sesji Ziomka do rebase, worktree zostaw".

---

## TL;DR
Gałąź `auton/ziomek-hygiene` (4 commity „hygiene" z-17/18/21/22) to ocalały produkt sesji-orchestratora **115**, która padła na OOM zanim ją zweryfikowała. **NIE jest zielona i NIE została wypchnięta.** Wymaga rebase na aktualny master + uzgodnienia refactoru scoringu r6 z testami. Worktree zostawiony nietknięty.

## Lokalizacja
- **Branch:** `auton/ziomek-hygiene`, tip `02902fae8dd5185e5c739679e445509d5f3e1447`
- **Worktree:** `/root/wt-ziomek-hyg` (ZOSTAWIONY — `git worktree list` go pokaże)
- **Origin:** NIE pushowany (świadomie wstrzymany — nie zaśmiecam origin czerwoną gałęzią)
- **merge-base z master:** `31a0d08` (= fix hermetyczny SLA-bypass)
- **Aktualny master:** `eca57f89` i wyżej — **19+ commitów do przodu** (118 dalej commituje monitory B3/B5 do dispatch_v2; sprawdź `git -C /root/.openclaw/workspace/scripts/dispatch_v2 rev-parse master` na bieżąco)

## 4 commity na gałęzi (od najstarszego)
| commit | typ | opis |
|---|---|---|
| `d513c46` | refactor **z-18** | serialize new-courier hard-skip reason jako label, nie `-1e9` w polu `score` |
| `fb46258` | refactor **z-21** | rozdzielenie martwego `r6_soft_penalty` (−3/min) od żywego (−8/min); `cap_override_cids` udokumentowane jako metadata-only |
| `7097aac` | docs **z-22** | oznaczenie `wave_scoring.py` jako DEAD (zostawione do referencji) |
| `02902fa` | docs **z-17** | dodanie kolumny „w kodzie" do katalogu 21-rules KB |

## Dlaczego wstrzymane — wynik weryfikacji (13.06)
Odpalony zestaw testów zmienionych przez S3 (venv `/root/.openclaw/venvs/dispatch/bin/python`):

```
cd /root/wt-ziomek-hyg
/root/.openclaw/venvs/dispatch/bin/python -m pytest -p no:cacheprovider -q -rfE \
  $(git diff --name-only master..auton/ziomek-hygiene | grep '^tests/.*\.py$' | while read f; do [ -f "$f" ] && echo "$f"; done)
```

- **Branch S3: 44 failed**, 116 passed, 3 skipped.
- **Baseline master (te same pliki, wersje z mastera): 1 failed** — i ten jeden to znany pre-existing `tests/test_reconcile_dry_run.py::script_run` (`[script-runner]` flake, już na liście follow-upów C w MEMORY.md).
- Padają **pliki, które S3 sam zmienił**, a które **na master przechodzą** — czyli regresja wprowadzona przez gałąź, nie szum środowiska:
  - `tests/test_scoring_c3.py` — `test_scoring_ignores_r6_penalty_when_flag_false`, `test_scoring_includes_r6_penalty_when_flag_true` ⟵ bezpośrednio refactor r6 (z-21)
  - `tests/test_v3273_wait_courier.py` — `test_wait_7_min_minus_15 / _10_min_minus_30 / _12_min_minus_40_andrei_case / _15_min_minus_55` ⟵ wartości kar (r6)
  - `tests/test_parser_health_layer3.py` — 7 faili (cooldown/motion-aware/health-contract)
  - `tests/test_v324b_czasowka_scheduler_v328_fix8.py::test_constants_module_level_defaults`

## Hipoteza root-cause (do potwierdzenia)
Dwie nakładające się przyczyny:
1. **Stara baza:** gałąź odbita od `31a0d08`, master odjechał 19+ commitów (118 FIX-B/E/monitory + sesja 113 cod_weekly). Część faili może wynikać z braku późniejszych fixów mastera.
2. **Refactor r6 (z-21) rozjechany z testami:** zmiana semantyki kary r6 (−3 vs −8/min) bez spójnej aktualizacji `test_scoring_c3` / `test_v3273_wait_courier`. To wina samej gałęzi.

## Rekomendowane kroki
1. `git -C /root/wt-ziomek-hyg fetch && git -C /root/wt-ziomek-hyg rebase origin/master` (lub lokalny `master` po sync) — rozwiąż konflikty (spodziewane w `scoring.py`, `wave_scoring.py`, `state_machine.py` + pliki testów).
2. Uzgodnij z-21: czy żywa kara r6 ma być −8/min (jak test oczekuje), a martwa −3/min tylko metadaną. Zaktualizuj `test_scoring_c3.py` + `test_v3273_wait_courier.py` **albo** popraw kod, aż oba będą spójne.
3. Sprawdź `test_parser_health_layer3` po rebase — jeśli dalej czerwone na świeżej bazie, to realny problem z-18 (serializacja state_machine), nie staleness.
4. Re-run pełnej (zmienionej) listy — cel: **0 faili poza znanym `test_reconcile_dry_run::script_run`**.
5. Dopiero wtedy: merge do master + push (świadomie, koordynując z żywymi sesjami — `python3 /root/session-coord.py`).

## Kontekst recovery 115
115 = sesja-orchestrator (start 12:59), 5 torów S1-S5. OOM 14:32 (build APK java + Chrome `openclaw-browser` przepełniły RAM). Ocalałe i już ZAŁATWIONE przez sesję porządkową:
- **S1** Panel `coordinator-console` → ✅ pushnięty (`9ac9707..50f9e80`)
- **S4** Papu-qual `auton/papu-quality` → ✅ pushnięty (nowa gałąź)
- **S5** Mailek `auton/mailek-features` → ✅ pushnięty (nowa gałąź)
- **S2** Apka+courier_api → ⛔ to żywa praca sesji 118 (FIX-D), nietknięte
- **S3** Ziomek-hyg → **TEN handoff**

Transkrypt 115 (resumowalny, gdyby trzeba kontekstu): `claude --resume db1fe406-15ea-4ed8-8c06-49f6f9a83e8c`

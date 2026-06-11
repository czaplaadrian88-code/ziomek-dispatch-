# Archiwum stash — werdykt SP-B2-VERDICTS (sesja B, 2026-06-11)

Oba stash-e zweryfikowane diffem vs HEAD (`f015f13`+) i ZDROPOWANE po
zarchiwizowaniu pełnych patchy w tym katalogu. Zero utraty informacji —
diffy obok, a treść merytoryczna jest w HEAD w nowszej formie.

## stash@{0} — "ISOLATE cudzy WIP (R6BREACH shadow + weekend recalib A1)…2026-06-06"
Plik: `stash0_isolate_r6breach_recalib_2026-06-06.diff` (476 linii)

| Składnik | Status w HEAD | Werdykt |
|---|---|---|
| `ENABLE_R6_BREACH_GUARD_SHADOW` (common/dispatch_pipeline/shadow_dispatcher) | JEST (common.py:2225, dispatch_pipeline.py:4421, shadow_dispatcher.py:768) | redundantny |
| Weekend recalib **A1** (sobota 11-12=1.50, doc-curve 17-18=1.30/1.35; sunday peak) | ZASTĄPIONY **wariantem B** (commit `c2005ab`): 15-17=1.55, 17-19=1.25 ("doc-curve przestrzeliwała +0.5/+0.36") + komentarz w common.py:303 | superseded — NIE wyciągać |
| Testy recalib (test_v326/v327_traffic_multiplier) | HEAD ma własne testy wariantu B (inne wartości oczekiwane) | superseded |
| `eod_drafts/2026-06-01/v4_carry_check.py` — rotation-aware `_shadow_files()` (.1) + helpery `_open/_arg` | NIE BYŁO w HEAD | **URATOWANY** — `git checkout stash@{0} -- <plik>`, commit razem z tym README |

## stash@{1} — "WIP on master: 3b9654e FAIL-03 K2 shadow faza 1…"
Plik: `stash1_fail03_k2_wip.diff` (164 linie)

Cała treść = wczesna wersja R6_BREACH_GUARD_SHADOW (te same 3 pliki co w
stash@{0}, podzbiór). Wszystko w HEAD w wersji finalnej. **Redundantny w 100%.**

## Wykonane operacje
1. `git stash show -p` → diffy do tego katalogu (pełna odtwarzalność).
2. `git checkout stash@{0} -- eod_drafts/2026-06-01/v4_carry_check.py` (jedyny unikalny artefakt; py_compile OK).
3. `git stash drop` ×2 (po commicie archiwum).

Odtworzenie czegokolwiek: `git apply eod_drafts/2026-06-11/stash_archive/<plik>.diff` (na commit sprzed `c2005ab` dla recalib A1).

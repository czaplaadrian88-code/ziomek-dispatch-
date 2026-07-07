# S27-C — Conditional-ETA: fix HTML-escape u źródła + karta wpięcia

> **Autor:** Sprint 27-C · **Zamknięty (kod):** 2026-07-07 ~20:20 UTC (22:20 Warsaw, wt) · **Worktree:** `s27c-eta-html-escape` commit **`b3e91da`** (na `master@d808808`). **ZERO flip / ZERO merge / ZERO restart / ZERO Telegram.** Merge + flip = 27-A za osobnym ACK Adriana.
> **Oś:** OBIETNICA (przewidywana ETA dostawy). **feasibility_v2 / R6 / selekcja NIETKNIĘTE** (SOFT nie osłabia HARD — ETAP 2).

## 1. Co i po co (jedną linią)
`calib_maps.eta_cell_residual_correct` robił **surowy** lookup nazwy restauracji w mapie korekt (`rmap.get(str(restaurant))`), a generator mapy (`tools/eta_cell_residual_build.py:114`) buduje klucze przez **`_html.unescape(...)`**. Nazwa restauracji z panelu jest **HTML-escaped** (`Sweet Fit &amp; Eat`, `Restauracja Kumar&#039;s`) → lookup chybiał → **warstwa RESTAURACJI korekty ETA nie stosowała się** dla restauracji z encjami. Fix: ten sam `html.unescape` u konsumenta = parytet z generatorem, **u źródła** (asymetria klucza generator↔konsument).

## 2. Root cause + dlaczego TU (ETAP 1 — źródło nie objaw)
- **Generator** (`eta_cell_residual_build.py:114`): `recs.append((..., _html.unescape(r.get("restaurant") or "")))` → klucze `restaurants` są **odescape'owane**, bez `.lower()`/`.strip()` (zachowana wielkość liter).
- **Konsument** (`calib_maps.py:247` przed fixem): `rentry = rmap.get(str(restaurant))` — surowa escaped nazwa → miss.
- **Dowód empiryczny na mapie żywej** (`dispatch_state/eta_cell_residual_map.json`, 52 klucze): 2 klucze z surowym `&` (`Sweet Fit & Eat`, `Sushi Rany Julek & Pizza Majstry`), **0 z `&amp;`** → mapa definitywnie odescape'owana.
- **Fix U ŹRÓDŁA vs objaw:** źródłem rozjazdu jest niespójność normalizacji klucza generator↔konsument. Naprawiamy w **konsumencie** (`calib_maps`), bo (a) generator to `tools/` (kanon klucza — nie ruszamy), (b) **globalny unescape `result.restaurant` w parserze byłby REGRESJĄ** — bliźniak `prep_bias_for` (`calib_maps.py:182`) i JEGO generator (`restaurant_prep_bias.py:150`) obie używają `strip().lower()` **BEZ** unescape → są symetryczne (escaped↔escaped). Odescape'owanie globalne rozjechałoby prep_bias. Więc fix jest lokalny do lookupu tej jednej mapy. (wzorzec #8 / C8 — sprawdzone grepem konsumentów).

## 3. Mapa kompletności (ETAP 3) — bliźniaki
| Miejsce | Klasa | Dotknięte? |
|---|---|---|
| `calib_maps.eta_cell_residual_correct` lookup (l.247→256) | konsument mapy z kluczem-restauracją | ✅ FIX (`html.unescape`) |
| `calib_maps.prep_bias_for` lookup (l.182) | **bliźniak** — inna mapa (`restaurant_prep_bias.json`) | **N-D** — generator (`restaurant_prep_bias.py:150`) i konsument OBA `strip().lower()` bez unescape → **symetryczne, zgodne** (brak asymetrii = brak buga). Zweryfikowane grepem obu. |
| caller `shadow_dispatcher.py:560` (jedyny konsument `eta_cell_residual_correct`) | wejście `restaurant=result.restaurant` | N-D (fix w choke-poincie funkcji obejmuje callera 1:1) |
| generator `eta_cell_residual_build.py:114` | kanon klucza (`tools/` = poza zakresem 27) | N-D (kanon — konsument dostraja się DO niego) |

## 4. Dowód empiryczny — POZYTYWNY wpływ (ETAP 5)
**Okno `logs/shadow_decisions.jsonl` (712 decyzji z polem restaurant, bieg 07-07):** **89 decyzji odzyskuje warstwę restauracji** po unescape (`raw∉mapa ∧ unescape(raw)∈mapa`), 0 przypadków odwrotnych (raw trafia / unescape chybia) = **zero regresji**. 3 restauracje z encjami:

| restauracja (escaped, jak na żywo) | klucz mapy (unescaped) | decyzji | wkład warstwy = w·resid |
|---|---|---|---|
| `Sushi Rany Julek &amp; Pizza Majstry` | `Sushi Rany Julek & Pizza Majstry` | 50 | **+0,78 min** (resid +0,81, w 0,961, n 617) |
| `Sweet Fit &amp; Eat` | `Sweet Fit & Eat` | 20 | **−1,11 min** (resid −1,20, w 0,921, n 291) |
| `Restauracja Kumar&#039;s` | `Restauracja Kumar's` | 19 | **−3,52 min** (resid −3,85, w 0,915, n 268) |

`Kumar&#039;s` (apostrof-encja) dowodzi, że fix MUSI być `html.unescape` — nie naiwny `.replace("&amp;","&")` (który zostawiłby `&#039;`). Wkład `Kumar's` = **−3,52 min** to materialna korekta na obietnicy, którą shadow dotąd GUBIŁ dla 19 decyzji.

## 5. Test + mutation-probe (ETAP 4 — dowody nie deklaracje)
- **Test** `tests/test_eta_cell_residual_w05.py::test_restaurant_layer_html_escaped_name` (NOWY) — parytet generator↔konsument **end-to-end** przez REALNY generator: źródło z encją → klucz odescape'owany → lookup escaped nazwą trafia. Sprawdza też `&#039;` (apostrof) + regresję czystej nazwy (fail-soft). **9/9 zielone** w tym pliku.
- **Flaga ON≠OFF (serializacja):** istniejący `test_flag_effect_on_serialized_record` (bez zmian) — `eta_cell_correction_flag` True/False, `eta_cell_corrected_min` liczony zawsze.
- **Mutation-probe (C14):** revert fixu (`html.unescape`→`str`) → test **PADA** (`assert 25.2 > 25.2` — warstwa chybia); restore → git diff pusty. **Test jest zabójczy** (nie fałszywie-zielony).
- **PEŁNA regresja Ziomka:** `pytest tests/` przez pkgroot-symlink (dispatch_v2→worktree, wzorzec conftest l.37-41) = **4430 passed, 0 failed, 0 error** (27 skip, 8 xfail, 2 xpass; 119 s). Zero faili — pkgroot dał czysty root bez artefaktów worktree; 0 fail/error w obszarze calib/eta.

## 6. KARTA WPIĘCIA W OBIETNICĘ (flip `ENABLE_ETA_CELL_RESIDUAL_CORRECTION` — 27-A za ACK)
**Mechanizm flipu:** HOT (flags.json, `decision_flag`), OFF→shadow(dziś)→ON. Konsument `shadow_dispatcher:560` liczy korektę ZAWSZE (shadow), flaga = stan aktywnego zastosowania do obietnicy. Procedura: runbook **§10**.

**⚠ KLUCZOWY NIUANS METODYCZNY (C9/C11 — instrument mierzył z chybioną warstwą):** dotychczasowa liczba **+5,14% MAE [CI 4,06–6,23]** (advisory Tura-2, shadow od 07-07 06:47) była liczona **PRZED tym fixem** → dla ~89/712 decyzji (3 restauracje) `eta_cell_corrected_min` NIE zawierał warstwy restauracji (cell-only). **Karta dowodowa +5,14% MUSI być RE-ZEBRANA na świeżym oknie 2d PO wmergowaniu fixu do shadow** — inaczej flip walidujemy liczbą sprzed poprawki instrumentu. Warstwa restauracji dodaje ~+1,5pp OOS (docstring `calib_maps` l.215); dla `Kumar's` przesunięcie −3,52 min/decyzję jest materialne.

**Sekwencja do ACK (bramki):**
1. [ ] **MERGE `b3e91da` → master + restart `dispatch-shadow`** (ACK) → shadow liczy korektę z warstwą restauracji dla WSZYSTKICH (fix jest niezależny od flagi — poprawia trafność mapy w shadow).
2. [ ] **Okno 2d** (do ~09.07) → re-pomiar karty: MAE(corrected) vs MAE(raw) na hold-out, **CI nieobejmujące 0, breach bez wzrostu, kierunek bias ku 0.** Cel: potwierdzić ~+5,14% (lub zaktualizować liczbą po-fixową).
3. [ ] **Flip `ENABLE_ETA_CELL_RESIDUAL_CORRECTION=True`** (HOT, off-peak) + ACK + monitor 1h (§10c).

**Bramki spełnione dziś:** fix u źródła ✓ · test ON≠OFF + mutation-kill ✓ · regresja 4430/0 ✓ · empiryczny dowód odzysku (89/712, 0 regres) ✓. **Brakuje:** merge (ACK) + karta +5,14% na świeżym oknie po-fixowym + ACK flipa.

## 7. Rollback
- **Fix (unescape):** niezależny od flagi, poprawia tylko trafność mapy w shadow. Rollback = `git revert b3e91da` (jeśli kiedyś zmergowany) — ale to czysty parytet z generatorem, brak powodu.
- **Flip conditional-ETA:** HOT `=False` → ≤1 tick, surowa obietnica wraca (§10d).

---
**Powiązane:** `PAS0_FLIPMASTER_RUNBOOK.md §10` · `calib_maps.py` (`eta_cell_residual_correct`) · `tools/eta_cell_residual_build.py:114` (kanon klucza) · [[ziomek-advisory-tura1-tura2-exec-2026-07-07]] (ETA warunkowe +5,14%) · [[ziomek-change-protocol]] C9/C11/C14 (instrument mierzył proxy z chybioną warstwą).

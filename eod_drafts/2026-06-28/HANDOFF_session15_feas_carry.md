# HANDOFF → sesja 15: feas_carry (#1) — DECYZJA + nowy fakt GPS (od sesji 18, 2026-06-28)

Masz to już w kolejce (poz. 1). To dokłada **decyzję Adriana** + **nowy fakt z pomiaru #5** + caveat at-192. Robota przez Przykazanie #0.

## Stan
- `ENABLE_FEAS_CARRY_READMIT = True` **LIVE** w `flags.json` (commit `e72139e`, doc `c174522` od 27.06 22:18 UTC; build sesji 19/27 — **C1: skoordynuj, nie zaskakuj**).
- Audyt runda 1 (`tasks/wvdued2fx.output` finding #8): replay który to podparł liczył „pozytywny wpływ" **w przestrzeni predykcji, bez join do `delivered_at`**. Realny join: breach 8%, predykcja śmieć (max 10188 min). Akcja **DODAJE breach na targecie 491/515 (med 3,8 min)**, „Pareto" = tautologia.
- Audyt runda 2 (`wfey3x75j.output`): te 3,8 min jest **w granicy niepewności fizycznej ~3 min** (delivered_at = prawda-przyciskowa) → szkoda NIEpewna, ale **korzyść = 0 (nieudowodniona)**.

## DECYZJA (Adrian 28.06)
1. **Konserwatywnie: rollback hot** — `flags.json` `ENABLE_FEAS_CARRY_READMIT=false` (bez restartu, dispatch-shadow czyta hot). Powód: rozluźnia twardą bramkę R6 (re-admit do cap 40) **bez udowodnionej korzyści**. Rollback = flaga `false` / `git revert` / `.bak`.
2. **Pomiar realny ODROCZONY** — „zrobić jak wszyscy będą jeździć z GPS" (Adrian). Dopiero przy pełnym pokryciu auto_geofence będzie czym zmierzyć korzyść fizycznie. Wpisane w rejestr STATUS KALIBRACJI.

## Nowy fakt z #5 (pomiar 28.06 — ważne dla „jak zmierzyć")
GPS-ground-truth **ISTNIEJE, ale nie jest spięte z per-order dostawą**:
- `dispatch_state/courier_ground_truth.json` = 422 wpisy, **source: auto_geofence 177 / manual 243 / manual_button 2**. Czyli ~42% statusów kuriera jest GPS-auto (apkowi kurierzy je generują — Adrian słusznie to widzi).
- ALE to statusy KURIERA (`last_status_code`), nie potwierdzenie per-zlecenie. Per-order `delivered_at` w orders_state to **goły timestamp bez pola source** → nie wiadomo per dostawa czy GPS czy klik. Stąd audytowe „0/377 auto_geofence delivered_at".
- **Wniosek dla pomiaru:** „pomiar realny" = spiąć istniejący auto_geofence (GPS-przyjazd) z per-order `delivered_at` → walidacja button-time vs GPS. Gdy GPS u wszystkich (apka) → auto_geofence pokrywa dostawy → realny breach mierzalny FIZYCZNIE. To MNIEJ niż „budować GPS od zera" — dane są, brak joinu.

## at-192 (monitor dziś 21:00 UTC)
Jak jest — **skłamie tak samo** (predicted-space, bez join). Albo (a) popraw jego join do `delivered_at` przed 21:00 (i tak button-truth ±3 min), albo (b) potraktuj jego werdykt jako proxy/void. NIE traktuj „CLEAN" z niego jako dowodu.

## Gdzie udokumentowane
Rejestr `shadow-jobs-registry.md` → STATUS KALIBRACJI (linia feas_carry). Raporty: `tasks/wvdued2fx.output` (#8) + `tasks/wfey3x75j.output` (caveaty). Protokół: sekcja ORACLE-CAVEATS (delivered_at = button-truth).

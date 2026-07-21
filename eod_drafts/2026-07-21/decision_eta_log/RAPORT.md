# Decision-time ETA log — raport source-only

## Werdykt

SOURCE COMPLETE / LIVE HOLD. Kanoniczny rerun D5 ma `n=0`, bo najwcześniejsza
predykcja była 164,63 min po decyzji. Kod dodaje brakujące as-of logowanie, ale
`ENABLE_DECISION_ETA_LOG` pozostaje default OFF i nie został dodany/flippnięty w
produkcyjnym `flags.json`. Zero deployu, restartu i mutacji runtime.

## Zakres i zachowanie

Jeden fail-safe writer zapisuje append-only `decision_eta.v1`: timestamp decyzji,
order/CID, finalny wynik, wybranego i ocenioną pulę, per-leg pickup/delivery ETA,
wersję/status modelu i kalibratora oraz źródło pozycji. Nie zapisuje nazw,
adresów ani koordynatów. Wpięcia: główna selekcja po finalnym `PipelineResult`,
czasówka po finalnym werdykcie, reassignment i global resweep po finalnej
alokacji, plan dopiero po udanym `save_plan`. Pełna mapa: `COMPLETENESS_MAP.tsv`.

Reader `tools/decision_eta_coverage.py` liczy dzienny join do unikalnych
`shadow_decisions.event_id`; denominator zero = HOLD, invalid record lub coverage
<100% = FAIL. Rotacja: daily, 30 kopii, `maxsize 100M`.

## Bezpieczeństwo i rollback

OFF kończy ścieżkę przed budową rekordu/I/O. ON nie uczestniczy w feasibility,
scoringu, selekcji ani renderze. Każdy błąd append/provenance jest połknięty,
zwiększa procesowy licznik i emituje WARNING. Rollback po przyszłym ACK: hot
`ENABLE_DECISION_ETA_LOG=false`; plik pozostaje dowodem append-only. Nie ma
migracji danych.

## Testy i bramki

Wyniki końcowe są w `EVIDENCE.md`. Kanoniczna pełna suita venv jest HOLD w tym
sandboxie (`Permission denied` przy wykonaniu `/root/.openclaw/venvs/dispatch/bin/python`).
CTO ma uruchomić ją hermetycznie komendą zapisaną w evidence przed merge/deploy.

Model/effort: `sol` / `xhigh` — wieloprocesowa granica decyzyjna, prywatność,
retencja i fail-safe wymagają integracyjnej weryfikacji. Dokładny wariant modelu
nieatestowany przez dostępny interfejs.

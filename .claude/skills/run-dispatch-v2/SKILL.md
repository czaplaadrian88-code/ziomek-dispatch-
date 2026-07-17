---
name: run-dispatch-v2
description: Uruchom, przetestuj i zdiagnozuj Ziomka (dispatch_v2). Użyj, gdy trzeba sprawdzić stan usług, odczytać werdykt nocnego strażnika regresji, znaleźć przecieki testów do produkcji, zobaczyć efektywne flagi silnika, zebrać lub uruchomić suitę pytest.
---

# run-dispatch-v2

Ziomek to **nie jest aplikacja z oknem** — to 16 usług systemd + narzędzia CLI
(`python -m dispatch_v2.tools.*`) + suita 5184 testów, pracujące na **żywych**
zamówieniach i kurierach. Nie ma czego sklikać ani sfotografować. Sterujesz nim
przez `.claude/skills/run-dispatch-v2/driver.sh`.

Ścieżki poniżej są względne wobec `dispatch_v2/`.

⚠ **To jest produkcja.** Driver dzieli komendy na read-only (wolne) i takie,
które mogą zapisać do żywego stanu (bramka ACK). Bramka jest celowym tarciem —
nie obchodź jej.

## Prerequisites

Nic do instalowania — środowisko istnieje. Jedyny poprawny python:

```bash
/root/.openclaw/venvs/dispatch/bin/python   # systemowy python3 NIE ma ortools → fałszywe faile
```

## Run (ścieżka agenta) — zacznij stąd

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2/.claude/skills/run-dispatch-v2
./driver.sh health
```

Zwraca trzy rzeczy naraz: stan usług, werdykt nocnego strażnika i przecieki
testów do produkcji. Zweryfikowane wyjście (2026-07-17):

```
dispatch-shadow          active     enabled
dispatch-telegram        inactive   disabled     ← ZAMIERZONE, nie awaria
dispatch-night-guard     failed     disabled     ← ALARM, nie crash
  2026-07-17T03:20:05+02:00  ALERT
  pytest: 1 failed, 5155 passed, 24 skipped, 8 xfailed in 288.47s
  CONFIRMED-FAIL: tests/test_hermetic_guard_zp207.py::test_subprocess_inherits_guard_blocks_live_write
  SUITE-CONTRACT ZLAMANY (manifest v5)
```

Komendy read-only (bez ACK):

| komenda | co daje |
|---|---|
| `./driver.sh health` | services + guard + litter razem |
| `./driver.sh services` | stan usług systemd |
| `./driver.sh guard` | **werdykt strażnika — journal go NIE zawiera** |
| `./driver.sh litter` | kandydaci na przeciek testów do produkcji (~30-70 s) |
| `./driver.sh flags` | flagi silnika z `flags.json` (280 kluczy) |
| `./driver.sh collect` | `5184 tests collected in 2.85s`, bez wykonania |

## Bramki ACK

Te komendy **mogą zapisać do żywego `dispatch_state`** i są zablokowane:

```bash
./driver.sh test         # → BLAD: wymaga jawnego ACK wlasciciela
./driver.sh guard-run    # → j.w.
```

Po uzyskaniu zgody właściciela — i tylko wtedy:

```bash
ZIOMEK_DRIVER_ACK=1 ./driver.sh test tests/test_flag_doc_coverage.py
```

**Dlaczego bramka:** suita zawiera testy, które **celowo** próbują pisać do
żywego stanu (`test_hermetic_guard_zp207`), a guard subprocesów jest FAIL-OPEN
(`conftest.py:50`) — przy jego awarii zapis **ląduje w produkcji**. Tak powstał
`hermetic_subproc_probe_zp207.tmp` (15.07 13:05), który leży tam do dziś.

## Run (ścieżka człowieka)

Usługi chodzą same. `systemctl status dispatch-shadow` pokaże stan, ale
**nie pokaże werdyktu strażnika** — patrz Gotchas.

## Gotchas — rzeczy, których nie da się zgadnąć

1. **`dispatch-telegram inactive` to stan ZAMIERZONY.** Kanał wyciszony
   świadomie. NIE naprawiaj jako awarii. → `memory/telegram-notifications-mute-2026-06-26`

2. **`dispatch-night-guard failed` to ALARM, nie crash.** `exit 1` jest
   zaprojektowanym sygnałem („confirmed-fail / suita ucięta / entropia rośnie").
   Strażnik działa i pisze werdykt co tick.

3. **Werdyktu strażnika NIE MA w journalu.** `journalctl` pokazuje tylko
   „Failed with result 'exit-code'" — stderr ma 0 linii. Werdykt jest wyłącznie
   w `dispatch_state/night_guard_history.jsonl`. Dlatego istnieje `./driver.sh guard`.

4. **Alarm strażnika idzie w wyciszony kanał.** `OnFailure` → `dispatch-telegram`
   → nikt nie słucha. Do tego `dispatch-cod-weekly` failuje przewlekle, więc
   `systemctl --failed` ma stałego rezydenta → alarm fatigue. Cztery noce ALERTU
   (14-17.07) przeszły niezauważone dokładnie tak.

5. **Wykrywanie przecieków po NAZWIE pliku kłamie.** `liveness_probe_state.json`
   brzmi jak sonda testowa, a pisze go `observability/liveness_probe.py`
   (produkcja). Odwrotnie: produkcja **konstruuje** nazwy (`f"{log}.{n}.gz"`,
   `path + ".lock"`), więc literalnie występują tylko w testach → filtr po nazwie
   zgłasza 9,5 MB rotowany log jako „śmieć". Driver używa **proweniencji
   pisarza** + wyklucza nazwy konstruowane. To i tak HINT — potwierdź ręcznie
   przed usunięciem czegokolwiek.

6. **Test, który zostawił śmieć, nie może już nigdy przejść.**
   `test_subprocess_inherits_guard_blocks_live_write` kończy się
   `assert not os.path.exists(probe)`. Sonda leży w produkcji od 15.07 → asercja
   pada zawsze, **niezależnie od tego, czy guard działa**. Alarm jest nie do
   ugaszenia bez cleanupu (a cleanup wymaga ACK).

7. **`dispatch_v2/dispatch_state/` w repo NIE jest żywym stanem** — to dane
   epaki. Żywy stan: `/root/.openclaw/workspace/dispatch_state/`.

8. **Flagi mają 3 światy** (ADR-004): silnik `flags.json`, panel
   `flags.systemd.env` + drop-iny, apka `courier_api/config.py`.
   `systemctl show -p Environment` **nie pokazuje** wartości z `EnvironmentFile`.
   `./driver.sh flags` czyta tylko świat silnika.

## Troubleshooting

| objaw | przyczyna → fix |
|---|---|
| pytest: `ModuleNotFoundError: ortools` | użyto systemowego `python3` → użyj `/root/.openclaw/venvs/dispatch/bin/python` |
| `RuntimeError: HERMETIC-GUARD` w teście | test jest nieizolowany → napraw TEST (`tmp_path`/monkeypatch), **nigdy nie osłabiaj guarda** |
| `./driver.sh test` → `BLAD: wymaga jawnego ACK` | działa poprawnie — zdobądź zgodę, potem `ZIOMEK_DRIVER_ACK=1` |
| `./driver.sh litter` trwa ~70 s | grepuje repo per plik; to koszt proweniencji zamiast zgadywania po nazwie |
| strażnik `failed`, a nie wiadomo czemu | `./driver.sh guard` (journal nie ma stderr) |

## Zakres

Driver **nie** restartuje usług, nie flipuje flag, nie deployuje i nie dotyka
`dispatch-telegram`. Te operacje wymagają protokołu z `/root/.codex/AGENTS.md`
(Przykazanie #0, ETAP 0-7) i osobnego ACK właściciela — nie skrótu przez skill.

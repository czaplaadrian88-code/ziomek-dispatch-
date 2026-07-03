# ADR-004: Flagi = trzy rozdzielne światy (silnik / panel / apka)

Status: obowiązuje (silnik: po migracji D3 2026-07-02 → flags.json; panel/apka: env/drop-iny; stan efektywny-z-procesu = doktryna od 2026-06-26 wzorzec #9)

## Kontekst
Stan flag jest rozproszony i mylący — dokumenty niosą trzy sprzeczne zapisy („drop-iny NIE flags.json" vs „flags.json" vs „17 kluczy w flags.json"). Metodyczna mina: `systemctl show -p Environment` NIE renderuje `EnvironmentFile=`, więc weryfikacja flag panelu tym poleceniem daje fałszywe OFF (44 realne flagi siedzą w `flags.systemd.env`). Ten sam kod bywa uruchamiany pod RÓŻNYM serwisem niż się zakłada (tick pod `dispatch-shadow`, recanon pod `dispatch-panel-watcher`/`dispatch-plan-recheck`) — różny env per proces.

## Decyzja
Trzy nośniki flag, rozdzielne: SILNIK = `flags.json` (hot-reload przez `C.flag()`; po D3 02.07 17 kluczy env→flags.json, stare env martwe `.bak-pre-d3-ab`). PANEL = `EnvironmentFile=…/flags.systemd.env` (44 flagi) + 3 inline `.conf` + `DEFAULT_FLAGS` w `app/core/flags.py`. APKA = drop-iny `.conf` + defaulty `courier_api/config.py`. Stan EFEKTYWNY czyta się z PROCESU: ustal serwis (`grep -rln run_X /etc/systemd`) → jego `Environment=`+drop-iny+linia `FLAG_FINGERPRINT` z logu tego procesu — NIGDY z `os.environ.get(...)` modułu ani samego flags.json.

## Konsekwencje
- Wolno: zmienić flagę silnika przez flags.json (hot-reload, zero restart).
- Nie wolno: wnioskować stanu flagi z env-default modułu; czytać flag panelu przez `systemctl show -p Environment` (fałszywe OFF); zakładać jeden nośnik dla wszystkich trzech powierzchni.
- Reguła „stan flag = drop-iny, NIE flags.json" (z `/root/CLAUDE.md`/`MEMORY.md`/`ZIOMEK_REGULY_KANON`) jest NIEAKTUALNA dla silnika po D3 — prawdziwa tylko dla panelu/apki.
- Dopisek (do potwierdzenia Adrianowi, WD-11): flagi outward panelu `COORDINATOR_*_LIVE`/`DISPATCH_PUSH_LIVE` =1 i `PANEL_ENVIRONMENT=staging` na żywym panelu — zamierzone czy relikt testowy? Prawda w docs zależy od tej odpowiedzi.

## Źródła
`docs/audyt/02-NIEZGODNOSCI.md` §2 (3 światy, spot-check 12 flag, N2/N9/N11/N12) + §7-8; `memory/ziomek-change-protocol.md` wzorzec #9 + „Dual/multi-service flag-reality"; `ZIOMEK_ARCHITECTURE.md` §5 (stan flag EFEKTYWNY).

# Lane „alerty-danowe" — raport (FALA-2 napraw audytu Ziomka, sprint tmux 11)

**Branch:** `fix/alerty-danowe` (od HEAD `60084fa`) · **Data:** 2026-07-02 · **Rola:** KOD+TESTY, ZERO deployu.
**Finding 2.0:** motyw #1 „alerty PROCESOWE nie DANOWE, pokrycie ciszy ≈ 0%" (`MASTER_synteza.md` §2.B / pkt 1).

---

## 1. CO ZBUDOWANE

Nowy moduł **`observability/data_alerts.py`** — monitor DANOWY, edge-triggered, 5 sygnałów czytających ŻYWY stan read-only. Uzupełnia lukę: systemd `OnFailure` łapie padnięcie procesu, ale NIE łapie „proces żyje, dane chore" (zamrożony ledger, burza sentineli, pusta pula, martwy fetch grafiku, stare pozycje GPS).

| # | Sygnał | Źródło (read-only) | Próg domyślny (env-override) | Bramka czasu |
|---|---|---|---|---|
| 1 | **sentinel-rate** | `shadow_decisions` (kanon `ledger_io`) | `DATA_ALERTS_SENTINEL_RATE_PCT=15.0`, `MIN_SAMPLE=20`, okno 30 min | — |
| 2 | **empty-pool** | `shadow_decisions.pool_feasible_count` | `DATA_ALERTS_EMPTY_POOL_PCT=40.0` | godz. pracy 09-23 Warsaw |
| 3 | **stale-grafik** | `dispatch_state/schedule_today.json:fetched_at` | `DATA_ALERTS_GRAFIK_STALE_H=6.0` | godz. pracy |
| 4 | **stale-pozycje GPS** | `dispatch_state/courier_last_pos.json` (ts per kurier) | `DATA_ALERTS_GPS_STALE_MIN=20`, `GPS_STALE_FRAC_PCT=50`, `GPS_MIN_FLEET=4` | godz. pracy |
| 5 | **ledger-stall** | najświeższy ts w ogonie `shadow_decisions` | `DATA_ALERTS_STALL_MIN=30` | PEAK 11-14 / 17-20 Warsaw |

**Konstrukcja (protokół #0 + C12/C13):**
- Ledger WYŁĄCZNIE kanonem rotation-aware `tools.ledger_io.iter_shadow_decisions` (naiwny odczyt gubi ~29% okna po rotacji). `max_bytes` = odczyt tylko ogona (tani tick).
- Ewaluatory to **czyste funkcje** (dane in → `Signal` out, zero I/O) → testowalne behawioralnie + mutation. Warstwa `collect()` ładuje z dysku.
- Flaga MASTER **`ENABLE_DATA_ALERTS`** przez `common.flag(...)` (hot-reload flags.json), **default OFF w kodzie** → OFF = no-op exit 0. Telegram za DRUGĄ flagą **`DATA_ALERTS_TELEGRAM`** (default OFF). Default (master ON, telegram OFF) = log do `scripts/logs/data_alerts.log` + stan edge-trigger w `dispatch_state/data_alerts_state.json` (atomowy temp+fsync+`os.replace`).
- Czas: `zoneinfo.ZoneInfo("Europe/Warsaw")` — NIGDY fixed-offset (ratchet TZ kanonu / bomby DST 25-26.10).
- Edge-trigger: alert tylko na krawędzi not→firing; wciąż-firing w cooldownie (`COOLDOWN_MIN=60`) NIE dubluje; po cooldownie re-emisja; firing→not = recovery (log RECOVER).

**Staged systemd (NIE zainstalowane):** `deploy_staging/etc/systemd/system/dispatch-data-alerts.{service,timer}` — oneshot co 5 min (`OnCalendar=*:0/5`, świadomie NIE `OnUnitActiveSec` — odkotwiczany przez daemon-reload), `OnFailure` = standard dispatch-*, profil lekki (MemoryMax 300M). Master-gate OFF w kodzie → dopóki flaga OFF w flags.json, oneshot to no-op.

---

## 2. PROGI — UZASADNIENIE (pomiar 30.06-02.07 na żywym ledgerze)

- **sentinel-rate 15%:** bazowy szum `pos_source=None` ~4% (186/500 to `no_gps` = LEGALNA polityka równego traktowania, ŚWIADOMIE POMINIĘTA — alarmowanie łamałoby KANON Adrian 29.06; `pre_shift` też pominięty). Coord-poison (0,0)/`v328_fail_causes` dziś = 0. Próg 15% łapie wyraźny SKOK (feed GPS padł → wszyscy do fikcji), nie normalny ogon.
- **empty-pool 40%:** baseline `pool=0` ~11% (55/500) — legalny ogon always-propose pod scarcity. Próg 40% = wyczerpanie floty / awaria feasibility, nie normalny szum.
- **stale-grafik 6h:** grafik fetchowany kilka razy/dobę (dziś `fetched_at` 12:22, świeży). 6h ciszy W GODZINACH PRACY = feed martwy. Nocą pominięte (grafik legalnie nietknięty).
- **stale-GPS 20 min / 50% floty:** pozycja >20 min = nieaktualna; alarm dopiero gdy >50% floty (`min_fleet=4`) — pojedyncze stare last-pos to norma, połowa floty = degradacja ingestu.
- **ledger-stall 30 min (peak):** mediana odstępu decyzji w peaku ~2 min, ale niski wolumen (~230 zam./d) daje LEGALNY ogon do ~35 min w cichym peaku. Próg 30 min łapie MARTWY silnik (cisza godzinami), nie karze naturalnej ciszy. ⚠ zob. §3 — to jedyny sygnał z realnym FP-tail.

---

## 3. SMOKE NA ŻYWYCH DANYCH (read-only, dry-run + backtest)

**Stan teraz (02.07 ~12:40 UTC, godz. 14 Warsaw, poza peakiem):** wszystkie 5 sygnałów **NIE firing** — system zdrowy.
```
sentinel_rate  12.5%  (n=8, <MIN_SAMPLE)   thr 15%   ok
empty_pool     12.5%  (n=8)                thr 40%   ok
stale_grafik   0.1h                        thr 6h    ok
stale_gps      20.0%  (2/10 stare)         thr 50%   ok
ledger_stall   3.0min (n=1)                thr 30min ok (poza peakiem → suppressed)
```

**Backtest edge-alertów, okno 30 min krok 5 min (jak timer), ostatnie 2 dni (467 decyzji):**
- sentinel_rate: **0** edge-alertów
- empty_pool: **0**
- ledger_stall: **1** edge-alert (deduped). Przyczyna = **realna** 35,2-min luka w peaku 07-02 10:39→11:14 (pojedynczy cichy poranek; top peak-gapy: 35,2 / 20,4 / 20,0 min, mediana 2,1 min, n=353). To NIE fałszywka procesowa — to autentycznie cichy peak; edge+cooldown daje 1 alert/epizod. Próg 30 min = kompromis: martwy silnik (cisza godzinami) łapany natychmiast, naturalny 35-min ogon strzela raz. Env-tunable, gdyby Adrian wolał ciszej → `DATA_ALERTS_STALL_MIN=40`.

**Master-flag OFF (stan flags.json dziś):** `--run` = potwierdzony no-op (emitted=[], zero zapisu, exit 0).

---

## 4. TESTY (`tests/test_data_alerts.py` — 26, behawioralne + mutation)

- 5 ewaluatorów: polaryzacja (firing above / not-firing below), **brzeg progu strict `>`** (dokładnie na 15% → NIE firing, 20% → firing), bramki czasu (empty-pool/grafik/GPS poza pracą suppressed; stall poza peakiem suppressed), min-sample/min-fleet guardy, `no_gps`/`pre_shift` ≠ sentinel (KANON).
- edge-trigger: krawędź emituje raz → w cooldownie NIE dubluje → po cooldownie re-emisja → recovery flag.
- flaga ON≠OFF: `run(enabled=False)`=no-op bez zapisu vs `run(enabled=True)` przetwarza+pisze; `enabled=None`→`common.flag` default **OFF** (dowód „default OFF w kodzie"); telegram gated (OFF→brak wysyłki, ON→wywołane).
- atomowy zapis (roundtrip + brak `.tmp`).
- **MUTATION-CHECK ×2 (C13):** fizyczna mutacja źródła — (a) polaryzacja `rate > threshold_pct`→`<` wywraca sentinel firing True→False; (b) próg `gap_min > threshold_min`→`+1e9` dezaktywuje stall. Oba potwierdzone: zmutowany moduł daje ODWROTNY wynik na tych samych danych → behawioralne asercje są load-bearing (osobno zweryfikowane, że mutant faktycznie łamie asercję).

Samo-lokalizacja modułu: `Path(__file__).parents[1]/observability/data_alerts.py` (C12(e) — NIGDY hardcode worktree; conftest pinuje kanon, gdzie modułu nie ma), sprzątanie `sys.modules` w try/finally.

---

## 5. DEPLOY-ZA-ACK (NIE wykonane; gotowe do koordynatora + ACK Adriana)

**(a) flags.json** — dodać OBIE flagi (default zachowawczy OFF; obie HOT, bez restartu):
```json
"ENABLE_DATA_ALERTS": false,
"DATA_ALERTS_TELEGRAM": false
```
⚠ `ENABLE_DATA_ALERTS` zaczyna się od `ENABLE_` → `test_flag_doc_coverage` wymaga wpisu w `ZIOMEK_LOGIC_REFERENCE.md` W TYM SAMYM commicie (inaczej new_drift → FAIL). `DATA_ALERTS_TELEGRAM` (nie-ENABLE) jest zwolniony z gate'u, ale dokumentujemy dla higieny.

**(b) doc-snippet do `dispatch_v2/ZIOMEK_LOGIC_REFERENCE.md`** (wklej w sekcję flag observability):
```
### ENABLE_DATA_ALERTS (observability, default OFF)
Master-gate monitora DANOWEGO `observability/data_alerts.py` (audyt 2.0 motyw #1,
pokrycie ciszy). OFF = moduł no-op. ON = 5 sygnałów edge-triggered (sentinel-rate,
empty-pool, stale-grafik, stale-GPS, ledger-stall) → log `scripts/logs/data_alerts.log`
+ stan `dispatch_state/data_alerts_state.json`. To NIE flaga decyzyjna (zero wpływu na
feasibility/scoring/selekcję) — tylko obserwowalność.
### DATA_ALERTS_TELEGRAM (observability, default OFF)
Druga bramka: gdy ON (i ENABLE_DATA_ALERTS ON), alerty danowe idą też na Telegram
(send_admin_alert, priority=high). OFF = tylko log+stan.
```

**(c) staged systemd → instalacja (off-peak, za ACK):**
```
cp -v deploy_staging/etc/systemd/system/dispatch-data-alerts.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dispatch-data-alerts.timer
systemctl list-timers --all | grep data-alerts     # 1 next-elapse
```
Sekwencja bezpieczna, bo master-flag OFF → oneshot no-op aż do flipu flagi.

**(d) rejestracja w `cron_health` (opcjonalnie, spójne z watchdog-close):** dodać `dispatch-data-alerts.service` do progu staleości (cadence 5 min → `thr≈1.0h`) w `_DEFAULT_STALE_THRESHOLDS_H` + `--sync-thresholds`.

**Kolejność flipu (rekomendacja):** najpierw instalacja timera + `ENABLE_DATA_ALERTS=true` (log-only, telegram OFF) → 1-2 dni obserwacji `data_alerts.log` (potwierdź brak spamu, edge działa) → dopiero potem `DATA_ALERTS_TELEGRAM=true`.

---

## 6. ROLLBACK
- Flip: `ENABLE_DATA_ALERTS=false` w flags.json (HOT, natychmiast no-op).
- Timer: `systemctl disable --now dispatch-data-alerts.timer` + `rm /etc/systemd/system/dispatch-data-alerts.{service,timer}` + `daemon-reload`.
- Kod: `git revert` commitów brancha. Stan `data_alerts_state.json` jest inertny bez timera (można usunąć).

## 7. RYZYKA / ZASTRZEŻENIA
- **ledger-stall FP-tail:** przy niskim wolumenie (~230 zam./d) cichy peak potrafi legalnie milczeć 35 min → 1 edge-alert/epizod. Świadomy kompromis (martwy silnik łapany natychmiast); tunowalny env. NIE traktować pierwszego strzału jako pewnej awarii — to nudge „sprawdź silnik".
- **sentinel definicja:** celowo WĄSKA (poison/v328/None/unknown), `no_gps`/`pre_shift` wykluczone (KANON). Jeśli kiedyś dojdzie nowe źródło fikcji, dopisać do `_SENTINEL_POS_SOURCES` (env/stała) — NIE rozszerzać o polityki równego traktowania.
- **stale-GPS źródło = `courier_last_pos.json`** (store last-known-pos, fallback). Sygnał łapie SKOK odsetka stale, nie absolutny poziom — nie mylić z „ilu kurierów bez GPS".
- Progi to defaults z 3-dniowego pomiaru — po tygodniu żywego log-only warto zweryfikować rozkłady (szczególnie sentinel/empty-pool w pełnym peaku, gdzie n≫MIN_SAMPLE).

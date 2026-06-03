# dispatch-retro-learning — dzienna OFFLINE pętla uczenia Ziomka

**Status w repo: ZBUDOWANE, NIE WŁĄCZONE.** Pliki leżą w `dispatch_v2/systemd/`.
Instalacja do systemd + włączenie = osobna decyzja Adriana (komendy niżej).

## Co to robi

Raz dziennie (oneshot przez timer) uruchamia sekwencyjnie 4 narzędzia
read-only/offline, które przeliczają trendy uczenia z istniejących logów.
Cel: **akumulacja trendu dzień po dniu** do świadomej decyzji o flipie
soft-score niezawodności (A2) i kalibracji bufora ETA (R6) na żywo.

Kolejność jest WAŻNA — feed przed konsumentami:

| # | ExecStart | Czyta | Produkuje |
|---|-----------|-------|-----------|
| 1 | `tools/retro_learning.py --json-only` | backfill_decisions_outcomes_v1.jsonl | `dispatch_state/retro_conclusions.json` |
| 2 | `tools/courier_reliability.py --json-only` | backfill_decisions_outcomes_v1.jsonl | `dispatch_state/courier_reliability.json` |
| 3 | `tools/eta_calibration_shadow.py` | retro_conclusions.json + backfill | dopisuje `dispatch_state/eta_calibration_shadow.jsonl` |
| 4 | `tools/a2_selection_shadow.py --max-lines 200000` | courier_reliability.json + shadow_decisions.jsonl(.1) | dopisuje `dispatch_state/a2_selection_shadow.jsonl` |

Krok 3 zależy od artefaktu kroku 1; krok 4 zależy od artefaktu kroku 2.
`oneshot` wykonuje `ExecStart=` sekwencyjnie i **jeśli któryś krok padnie
(exit != 0), kolejne się NIE wykonają** i unit kończy `failed`. To celowe —
chcemy wiedzieć, że łańcuch się zerwał (drop-in odpala alert Telegram).

> Ścieżki artefaktów to **`/root/.openclaw/workspace/dispatch_state/`**
> (NIE `dispatch_v2/dispatch_state/`). Wszystkie 4 narzędzia mają ten katalog
> zaszyty na stałe.

## Bezpieczeństwo — to jest OFFLINE / READ-ONLY

- Zero wpływu na żywy dispatch. Narzędzia czytają tylko istniejące logi
  (`backfill_decisions_outcomes_v1.jsonl`, `shadow_decisions.jsonl`) i
  zapisują WYŁĄCZNIE do plików trendów w `dispatch_state/`.
- Nie dotykają Telegrama, panelu, OSRM, stanu zleceń ani flag.
- Świadomie poza hot-path: kod shadow w gorącej ścieżce już raz wywalił
  produkcję (V3.27.4 NameError) — tu liczymy offline z logów. Zgodne z Z2/Z3.

## Harmonogram

`OnCalendar=*-*-* 04:30:00 UTC` → **04:30 UTC = 06:30 Warsaw**, poza peakiem
(peak 11:00–14:00 Warsaw). `Persistent=true` — jeśli serwer był wyłączony o
04:30, job dogoni przy najbliższym starcie.

## Jak ZAINSTALOWAĆ I WŁĄCZYĆ (komendy dla Adriana)

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2/systemd

# 1) Skopiuj unity do systemd
sudo cp dispatch-retro-learning.service /etc/systemd/system/
sudo cp dispatch-retro-learning.timer   /etc/systemd/system/

# 2) (opcjonalnie, ale zalecane) drop-in alertu Telegram on-failure
#    — analogiczny do innych dispatch-*.service
sudo mkdir -p /etc/systemd/system/dispatch-retro-learning.service.d
sudo cp dispatch-retro-learning.service.d/telegram-onfailure.conf \
        /etc/systemd/system/dispatch-retro-learning.service.d/telegram-onfailure.conf

# 3) Przeładuj systemd i włącz timer (od razu uzbraja harmonogram)
sudo systemctl daemon-reload
sudo systemctl enable --now dispatch-retro-learning.timer
```

### Opcjonalnie: jednorazowy ręczny przebieg po instalacji (test)

```bash
# Odpala serwis natychmiast (jak gdyby strzelił timer), nie czekając na 04:30
sudo systemctl start dispatch-retro-learning.service
```

## Jak SPRAWDZIĆ

```bash
# Czy timer uzbrojony i kiedy następny strzał
systemctl list-timers | grep retro

# Status serwisu (ostatni wynik: success/failed)
systemctl status dispatch-retro-learning.service

# Logi przebiegów (plik append) — pełne raporty 4 narzędzi
journalctl -u dispatch-retro-learning            # z journala (jeśli używasz)
tail -n 200 /root/.openclaw/workspace/scripts/logs/retro_learning.log

# Czy trendy się akumulują (rosnąca liczba linii dzień po dniu)
wc -l /root/.openclaw/workspace/dispatch_state/eta_calibration_shadow.jsonl
wc -l /root/.openclaw/workspace/dispatch_state/a2_selection_shadow.jsonl
ls -la /root/.openclaw/workspace/dispatch_state/retro_conclusions.json \
       /root/.openclaw/workspace/dispatch_state/courier_reliability.json
```

## Jak WYŁĄCZYĆ / ODINSTALOWAĆ

```bash
# Stop + wyłączenie harmonogramu (timer)
sudo systemctl disable --now dispatch-retro-learning.timer

# Pełna deinstalacja (opcjonalnie)
sudo rm -f /etc/systemd/system/dispatch-retro-learning.timer
sudo rm -f /etc/systemd/system/dispatch-retro-learning.service
sudo rm -rf /etc/systemd/system/dispatch-retro-learning.service.d
sudo systemctl daemon-reload
```

## Walidacja wykonana przy budowie (2026-06-03)

- `systemd-analyze verify` na `.service` i `.timer` → exit 0 (jedyne ostrzeżenie
  dotyczy NIEpowiązanego, już zainstalowanego `papu-observability.service`).
- 4 `ExecStart` wskazują istniejące pliki `tools/*.py`; wszystkie odpalane przez
  `/root/.openclaw/venvs/dispatch/bin/python`.
- Ręczny przebieg pełnej sekwencji 4 kroków: **CHAIN_RC=0**, wszystkie 4 artefakty
  w `dispatch_state/` odświeżone, nowy wiersz trendu dopisany do obu plików `.jsonl`.
  (Przy okazji naprawiono błąd `a2_selection_shadow.py`: `main()` rozpakowywał
  3-krotkę z `load_reliability` jako 2-krotkę → `ValueError`; to blokowało krok 4.)

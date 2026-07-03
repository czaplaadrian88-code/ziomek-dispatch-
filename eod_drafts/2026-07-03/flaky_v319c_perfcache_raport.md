# Flaky `test_v319c_sub_a::script_run` — root cause: mtime-cache planów (PERF_LAZY), NIE pre-existing

**Sesja:** tmux 14 (rano 03.07) · **Zadanie 3 handoffu** (+ 2. okaz `test_bug1_invalidate…` z regresji zad. 2).

## TL;DR
Flaka **urodziła się z flipem `ENABLE_PERF_LAZY_MEMBERS` 03.07 ~00:25** — etykieta „pre-existing"
w handoffie była względem diffu bug4-lane (po flipie), nie względem flipu. Root cause = **fałszywe
założenie projektowe cache'a planów**: „os.replace bumpuje mtime → cache sam się unieważnia".
Dwa zapisy w tym samym ticku zegara jądra dają **identyczny `st_mtime_ns`** (a `st_size` bywa równy,
bo ciała testów różnią się tylko treścią oidów tej samej długości) → czytelnik dostawał stan
**sprzed** zapisu. To dziura poprawności także dla produ (write→read-back w tym samym procesie
i ticku), nie tylko testów.

## Dowód (ON≠OFF, izolowane biegi skryptu)
| wariant | FAIL |
|---|---|
| przed fixem, PERF_LAZY=**ON** (żywe flags.json) | **4/30** (~13% ≈ „1/7" z handoffu) |
| przed fixem, PERF_LAZY=**OFF** (kopia flags z false) | **0/30** |
| **po fixie**, PERF_LAZY=**ON** | **0/30** |
| po fixie, 3× izolowany pytest `test_v319c_sub_a` | 3/3 PASS |

Script-runnery dziedziczyły ŻYWY flip, bo `ENABLE_PERF_LAZY_MEMBERS` nie był na żadnej liście
strip conftestu (nie-ETAP4, nie-infra) — stąd „flake od nocy".

## Fix u źródła (3 warstwy, commit — patrz git)
1. **`plan_manager._write_raw`** — write-through: każdy zapis czyści in-process read-cache
   (in-process ZAWSZE świeże, niezależnie od rozdzielczości zegara).
2. **Klucz cache = `(st_mtime_ns, st_size, st_ino)`** — atomic write przez `os.replace` = nowy
   inode przy każdym zapisie → cross-proces unieważnia się nawet przy zderzeniu mtime+size.
   Komentarz projektowy cache'a skorygowany (fałszywe założenie udokumentowane).
3. **`common.TEST_ISOLATED_INFRA_FLAGS += ENABLE_PERF_LAZY_MEMBERS`** — determinizm suity:
   testy nie dziedziczą żywego killswitcha (sterują jawnie monkeypatchem stałej); obejmuje
   in-process (`_isolate_flags_json`) i script-runnery (`_stripped_flags_copy`) — jedno źródło.

## Testy (NOWE `tests/test_plan_cache_write_through.py`, 6) + mutacja
- write-through czyści cache (deterministyczny strażnik chokepointu) · rapid 300× same-size
  write→read zawsze świeże · wzorzec `_wipe`→recreate (dokładnie scenariusz v319c) · parytet
  ON↔OFF treści odczytu · inwariant klucza st_ino (wymuszony identyczny mtime → klucz RÓŻNY) ·
  flaga na liście strip.
- **Mutacja M1** (usunięty write-through) → strażnik chokepointu **RED** (rapid/wipe zostają
  GREEN — kryje je warstwa st_ino, zgodnie z projektem warstw); restore → 6/6 GREEN.

## Zasięg live / deploy
- Zero restartów wykonanych. Oneshoty (czasówka, plan-recheck) łapią kod od następnego ticku;
  **shadow/panel-watcher biegną starym kodem do najbliższego restartu** — dziura jest ms-owa
  i istnieje od 00:25, dograna zostanie przy pierwszym planowym restarcie (nie eskaluję przed
  peakiem). Wpływ na pomiar perf (zad. 1): brak — ciepła ścieżka odczytu identyczna.
- Rollback: `git revert` / `plan_manager.py.bak-pre-perfcache-writethrough-2026-07-03`.

## Status 2. okazu (`test_bug1_invalidate_plan_on_bag_change_2026_06_05::script_run`)
Padł 1× w pełnej suicie (zad. 2, run 1), 3/3 izolowany PASS, w run 2 i po fixie zielony.
Ten sam mechanizm (moduł używa plan_manager + tmp-redirect + szybkie zapisy) — fix pokrywa.
Obserwacja: jeśli wróci w kolejnych regresjach → osobna diagnoza (nie zakładać tej samej przyczyny).

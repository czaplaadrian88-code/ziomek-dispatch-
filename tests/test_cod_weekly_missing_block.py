"""F2.1d COD Weekly — brak bloku tygodnia (finding B audytu 2.0, 2026-07-02).

Repro GROUND-TRUTH: `dispatch-cod-weekly` padał exit 1 co poniedziałek, gdy w
arkuszu 'Wynagrodzenia Gastro' nie było bloku docelowego tygodnia (find_target
zwraca 0 kandydatów). Testy:
  - stara ścieżka: NoTargetColumnError → exit 1 + AKTIONABLE alert (nie goły traceback)
  - struktura niejednoznaczna (Ambiguous) NIGDY nie auto-tworzy
  - auto-create ZA FLAGĄ: ON tworzy blok i dopisuje COD; OFF nie tworzy
  - dry-run: pokazuje CO by utworzył, NIC nie zapisuje
  - build_week_block_plan / ensure_week_block (single + split-month)
  - MUTATION-CHECK (C13): zmutowana detekcja braku bloku → test to wychwytuje

⚠ Ten plik CELOWO DZIAŁA w venv dispatch (bez gspread) — wstrzykuje fałszywe
`gspread` + `google.oauth2.service_account` do sys.modules TYLKO na czas importu
sheet_writer/run_weekly, po czym je sprząta, żeby `importorskip('gspread')` w
pozostałych testach cod_weekly dalej poprawnie SKIPOWAŁ. Produkcyjnie moduł
biega pod venv sheets (prawdziwy gspread).

Run:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest \\
        tests/test_cod_weekly_missing_block.py -q
"""
import importlib.util
import sys
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

_SCRIPTS = "/root/.openclaw/workspace/scripts"
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# Repo, w którym LEŻY ten test (kanon lub dowolny worktree — samo-lokalizacja;
# NIGDY nie hardkoduj ścieżki worktree: po merge+`git worktree remove` martwa
# ścieżka wywala kolekcję i zatruwa sys.modules dla starych testów cod_weekly).
# Harness (conftest) importuje `dispatch_v2` z KANONU, więc 3 testowane pliki
# ładujemy WPROST z tego repo pod nazwami dispatch_v2.cod_weekly.*, a po
# imporcie przywracamy sys.modules (zero wycieku do reszty regresji).
_WT = str(Path(__file__).resolve().parents[1])

# --- Wstrzyknięcie fałszywych zależności Sheets (dispatch-venv nie ma gspread)
_INJECTED = [
    n for n in ("gspread", "google.oauth2", "google.oauth2.service_account")
    if n not in sys.modules
]
if "gspread" in _INJECTED:
    _g = types.ModuleType("gspread")
    _g.authorize = lambda *a, **k: MagicMock()
    sys.modules["gspread"] = _g
if "google.oauth2" in _INJECTED:
    sys.modules["google.oauth2"] = types.ModuleType("google.oauth2")
if "google.oauth2.service_account" in _INJECTED:
    _sa = types.ModuleType("google.oauth2.service_account")

    class _Creds:
        @staticmethod
        def from_service_account_file(*a, **k):
            return MagicMock()

    _sa.Credentials = _Creds
    sys.modules["google.oauth2.service_account"] = _sa

# Kanon package (dostarcza __path__ dla aliases/week_calculator/panel_scraper)
import dispatch_v2.cod_weekly  # noqa: E402

_OVERRIDE = [
    "dispatch_v2.cod_weekly.config",
    "dispatch_v2.cod_weekly.sheet_writer",
    "dispatch_v2.cod_weekly.run_weekly",
]
_SAVED_MODS = {k: sys.modules.get(k) for k in _OVERRIDE}


def _load_wt(qual, relpath):
    spec = importlib.util.spec_from_file_location(qual, f"{_WT}/{relpath}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qual] = mod
    spec.loader.exec_module(mod)
    return mod


# try/finally: sprzątanie sys.modules MUSI się wykonać także gdy load padnie —
# inaczej częściowo zarejestrowany (pusty) moduł zatruwa import dla POZOSTAŁYCH
# testów cod_weekly w tej samej kolekcji (klasa awarii z FALA1 02.07).
try:
    _cfg = _load_wt("dispatch_v2.cod_weekly.config", "cod_weekly/config.py")
    sw = _load_wt("dispatch_v2.cod_weekly.sheet_writer", "cod_weekly/sheet_writer.py")
    rw = _load_wt("dispatch_v2.cod_weekly.run_weekly", "cod_weekly/run_weekly.py")
    NoTargetColumnError = sw.NoTargetColumnError
    AmbiguousTargetError = sw.AmbiguousTargetError
    PartialSplitBlockError = sw.PartialSplitBlockError
    col_idx_to_letter = sw.col_idx_to_letter
finally:
    # --- Przywróć sys.modules (kanon) + zdejmij fake gspread, by importorskip
    #     w pozostałych testach cod_weekly dalej poprawnie SKIPOWAŁ.
    for _k, _v in _SAVED_MODS.items():
        if _v is None:
            sys.modules.pop(_k, None)
        else:
            sys.modules[_k] = _v
    for _name in _INJECTED:
        sys.modules.pop(_name, None)
    if "google.oauth2" in _INJECTED:
        try:
            import google as _google_pkg
            if getattr(_google_pkg, "oauth2", None) is not None:
                delattr(_google_pkg, "oauth2")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# Tydzień docelowy = 2026-06-01..06-07 (jeden z REALNIE przepadłych tygodni;
# payday 10-06-2026, zakres '01-07.06.2026').
TARGET_START = date(2026, 6, 1)
TARGET_END = date(2026, 6, 7)
TARGET_PAYDAY = "10-06-2026"
TARGET_RANGE = "01-07.06.2026"

# Split-month: 2026-04-27..05-03 (payday 06-05-2026; 2 segmenty).
SPLIT_START = date(2026, 4, 27)
SPLIT_END = date(2026, 5, 3)


def _grid_other_block_only(ws=None):
    """Grid z blokiem INNEGO tygodnia (25-31.05.2026, payday 03-06-2026) @ AQ.

    Dla tygodnia docelowego 01-07.06 → brak dopasowania payday ORAZ zakresu →
    find_target_resilient rzuci NoTargetColumnError (realna detekcja braku bloku).
    """
    n = 46
    row1 = [""] * n
    row2 = [""] * n
    row2[42] = "COD - Transport"   # AQ
    row2[43] = "Korekty"
    row2[44] = "Wypłata"
    row2[45] = "Saldo do przen."
    row1[42] = "Tydzień 25-31.05.2026"
    row1[43] = "wypłata z dn."
    row1[44] = "03-06-2026"
    row1[45] = "25-31.05.2026"
    return {
        "ws": ws if ws is not None else MagicMock(),
        "row1": row1,
        "row2": row2,
        "restaurants": [(3, "Arsenał Panteon"), (5, "Toriko")],
    }


def _install_write_path(monkeypatch, grid):
    """Zamockuj downstream cmd_write (poza find_target/ensure_week_block —
    TE zostają REALNE). Zwraca (alerts, writes) do asercji."""
    alerts = []
    writes = []
    monkeypatch.setattr(rw, "fetch_sheet_grid", lambda: grid)
    monkeypatch.setattr(rw, "_try_alert", lambda t: (alerts.append(t), True)[1])
    monkeypatch.setattr(
        rw, "_refresh_mapping",
        lambda: {"Arsenał Panteon": 14, "Toriko": 231},
    )
    monkeypatch.setattr(
        rw, "validate_column_empty_ratio",
        lambda *a, **k: {
            "ok": True, "empty_count": 2, "total": 2,
            "ratio": 1.0, "filled_sample": [],
        },
    )

    def _scrape(restaurants, mapping, targets, opener=None):
        results = [
            {"row": r, "rest": nm, "cod_per_segment": [10.0], "had_error": False}
            for r, nm in restaurants
        ]
        return results, []

    monkeypatch.setattr(rw, "_scrape_all", _scrape)

    def _write(ws, col_letter, row_to_value, dry_run=False):
        writes.append((col_letter, dict(row_to_value)))
        return {
            "dry_run": dry_run,
            "written_rows": [
                (f"{col_letter}{r}", v) for r, v in row_to_value.items()
            ],
            "skipped_filled": [],
            "skipped_errors": [],
        }

    monkeypatch.setattr(rw, "write_cod_column_skip_filled", _write)
    return alerts, writes


# ---------------------------------------------------------------------------
# 1. Flag OFF (default) — brak bloku → exit 1 + AKTIONABLE alert (nie traceback)
# ---------------------------------------------------------------------------
def test_missing_block_flag_off_exit1_actionable(monkeypatch):
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_BLOCK", raising=False)
    ws = MagicMock()
    grid = _grid_other_block_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(TARGET_START, TARGET_END)

    assert rc == 1, "brak bloku bez auto-create → exit 1 ZOSTAJE (OnFailure)"
    assert writes == [], "przy braku bloku żaden zapis COD nie może pójść"
    ws.batch_update.assert_not_called()  # auto-create OFF → nic nie tworzy
    assert alerts, "musi pójść alert"
    msg = alerts[0]
    # AKTIONABLE (nie goły 'Target column fail'): pełna instrukcja + backfill
    assert "Akcja Rafał" in msg
    assert TARGET_PAYDAY in msg           # payday co dodać
    assert TARGET_RANGE in msg            # zakres tygodnia co dodać
    assert "COD - Transport" in msg       # nagłówki row2
    assert "--week 2026-06-01:2026-06-07 --write" in msg  # komenda backfillu


# ---------------------------------------------------------------------------
# 2. Flag ON — auto-create tworzy blok i dopisuje COD (exit 0)
# ---------------------------------------------------------------------------
def test_missing_block_autocreate_on_creates_and_writes(monkeypatch):
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_DRY_RUN", raising=False)
    ws = MagicMock()
    grid = _grid_other_block_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(TARGET_START, TARGET_END)

    assert rc == 0, "auto-create ON → blok utworzony → zapis idzie → exit 0"
    ws.batch_update.assert_called_once()  # ensure_week_block zapisał 1× blok
    # nowy blok ląduje w kol AU (za istniejącym @AQ, 4-kol blok od idx 46)
    assert col_idx_to_letter(46) == "AU"
    assert len(writes) == 1 and writes[0][0] == "AU", (
        f"COD zapisany do nowo utworzonej kolumny AU, got {writes}"
    )
    # batch_update dostał payday + zakres we właściwych komórkach
    args, kwargs = ws.batch_update.call_args
    updates = args[0]
    flat = str(updates)
    assert TARGET_PAYDAY in flat and TARGET_RANGE in flat
    assert "COD - Transport" in flat


# ---------------------------------------------------------------------------
# 3. Flag ON + DRY-RUN — pokazuje plan, NIC nie zapisuje (exit 1)
# ---------------------------------------------------------------------------
def test_missing_block_autocreate_dry_run_no_write(monkeypatch):
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_DRY_RUN", "1")
    ws = MagicMock()
    grid = _grid_other_block_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(TARGET_START, TARGET_END)

    assert rc == 1, "dry-run nie tworzy → nadal exit 1"
    ws.batch_update.assert_not_called()   # NIC nie zapisano do arkusza
    assert writes == [], "dry-run nie dopisuje COD"
    assert alerts and "DRY-RUN" in alerts[0]
    assert TARGET_PAYDAY in alerts[0] and TARGET_RANGE in alerts[0]


# ---------------------------------------------------------------------------
# 4. Struktura NIEJEDNOZNACZNA (Ambiguous) NIGDY nie auto-tworzy
# ---------------------------------------------------------------------------
def test_ambiguous_never_autocreates_even_with_flag_on(monkeypatch):
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    ws = MagicMock()
    grid = _grid_other_block_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    def _raise_ambiguous(*a, **k):
        raise AmbiguousTargetError("2 kandydatów dla single-segment tygodnia: ['BR','BV']")

    # find_target_cod_columns_resilient woła find_target_cod_columns (globals rw)
    monkeypatch.setattr(rw, "find_target_cod_columns", _raise_ambiguous)

    rc = rw.cmd_write(TARGET_START, TARGET_END)

    assert rc == 1
    ws.batch_update.assert_not_called()   # Ambiguous → NIGDY auto-create
    assert writes == []
    assert alerts and "Akcja Rafał" in alerts[0]  # nadal aktionable


# ---------------------------------------------------------------------------
# 5. build_week_block_plan — single-segment poprawny plan
# ---------------------------------------------------------------------------
def test_build_week_block_plan_single():
    grid = _grid_other_block_only()
    plan = sw.build_week_block_plan(
        grid["row1"], grid["row2"], TARGET_START, TARGET_END,
    )
    assert plan["payday"] == TARGET_PAYDAY
    assert len(plan["blocks"]) == 1
    b = plan["blocks"][0]
    assert b["cod_col_letter"] == "AU"           # append za blokiem @AQ
    assert b["range_a1"] == "AU1:AX2"            # 4 kolumny × 2 wiersze
    assert b["week_range"] == TARGET_RANGE
    assert b["row2_cells"][0] == "COD - Transport"
    assert b["row1_cells"][2] == TARGET_PAYDAY   # pos+2 = payday
    assert b["row1_cells"][3] == TARGET_RANGE    # pos+3 = zakres
    # new_row1 ma payday/zakres tam gdzie detekcja ich szuka
    assert plan["new_row1"][48] == TARGET_PAYDAY  # AU idx=46, +2 = 48
    assert plan["new_row1"][49] == TARGET_RANGE
    assert plan["new_row2"][46] == "COD - Transport"


# ---------------------------------------------------------------------------
# 6. ensure_week_block — dry_run vs real (zapis TYLKO gdy real)
# ---------------------------------------------------------------------------
def test_ensure_week_block_dry_run_vs_real():
    grid = _grid_other_block_only()

    ws_dry = MagicMock()
    plan_dry = sw.ensure_week_block(
        ws_dry, grid["row1"], grid["row2"], TARGET_START, TARGET_END,
        dry_run=True,
    )
    assert plan_dry["dry_run"] is True and plan_dry["created"] is False
    ws_dry.batch_update.assert_not_called()

    ws_real = MagicMock()
    plan_real = sw.ensure_week_block(
        ws_real, grid["row1"], grid["row2"], TARGET_START, TARGET_END,
        dry_run=False,
    )
    assert plan_real["created"] is True
    ws_real.batch_update.assert_called_once()
    # NIE dotyka danych COD — kolumna wartości (pos+0) w cellach jest nagłówkiem,
    # a wartości nie ma w updates (tylko row1+row2)
    args, _ = ws_real.batch_update.call_args
    assert args[0][0]["range"] == "AU1:AX2"
    assert args[0][0]["values"] == [
        plan_real["blocks"][0]["row1_cells"],
        plan_real["blocks"][0]["row2_cells"],
    ]


# ---------------------------------------------------------------------------
# 7. build_week_block_plan — split-month → 2 bloki, ten sam payday, różne zakresy
# ---------------------------------------------------------------------------
def test_build_week_block_plan_split_month():
    grid = _grid_other_block_only()
    plan = sw.build_week_block_plan(
        grid["row1"], grid["row2"], SPLIT_START, SPLIT_END,
    )
    assert len(plan["blocks"]) == 2, "tydzień krosujący miesiąc = 2 bloki"
    b1, b2 = plan["blocks"]
    assert b1["payday"] == b2["payday"] == "06-05-2026"     # wspólny payday
    assert b1["week_range"] != b2["week_range"]             # różne zakresy
    assert b1["cod_col_idx"] != b2["cod_col_idx"]           # różne kolumny
    assert b2["cod_col_idx"] - b1["cod_col_idx"] == sw.BLOCK_WIDTH
    assert b1["week_range"] == "27-30.04.2026"
    assert b2["week_range"] == "01-03.05.2026"


# ---------------------------------------------------------------------------
# 8. MUTATION-CHECK (C13) — zmutowana detekcja braku bloku jest wychwytywana
# ---------------------------------------------------------------------------
def test_mutation_missing_block_detection(monkeypatch):
    """Dowód że test faktycznie testuje detekcję braku bloku, nie przechodzi
    pusto: mutujemy warunek `if not candidates: raise NoTargetColumnError`
    (usuwamy raise → detekcja zepsuta). Pod mutacją zachowanie MUSI się zmienić
    — aktionable ścieżka 'brak bloku' znika. Test to wychwytuje → PASS."""
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_BLOCK", raising=False)

    # (A) REALNA detekcja → aktionable 'Akcja Rafał'
    grid_a = _grid_other_block_only(MagicMock())
    alerts_a, writes_a = _install_write_path(monkeypatch, grid_a)
    rc_a = rw.cmd_write(TARGET_START, TARGET_END)
    real_actionable = bool(alerts_a) and "Akcja Rafał" in alerts_a[0]
    assert rc_a == 1 and real_actionable, "REALNA detekcja daje aktionable exit 1"

    # (B) ZMUTOWANA detekcja: find_target zwraca [] zamiast raise NoTargetColumnError
    def _mutated_find_target(*a, **k):
        return []  # candidates empty, ale NIE rzuca — detekcja braku bloku zepsuta

    monkeypatch.setattr(rw, "find_target_cod_columns", _mutated_find_target)
    grid_b = _grid_other_block_only(MagicMock())
    alerts_b, writes_b = _install_write_path(monkeypatch, grid_b)
    rw.cmd_write(TARGET_START, TARGET_END)
    mutated_actionable = bool(alerts_b) and "Akcja Rafał" in alerts_b[0]

    # Mutacja MUSI być obserwowalna: aktionable-'brak bloku' znika pod mutacją.
    assert real_actionable and not mutated_actionable, (
        "mutation-check: zmiana warunku detekcji NIE zmieniła zachowania — "
        "test byłby ślepy"
    )


# ===========================================================================
# PARTIAL-SPLIT (2026-07-08) — rozbity tydzień z 1 z 2 bloków.
# Root cause cotygodniowego FAILA na tygodniach krosujących miesiąc: dawniej
# `len(candidates)!=2` → AmbiguousTargetError → exit 1 (NIGDY nie auto-create).
# GROUND-TRUTH 06.07: tydzień 29.06-05.07, payday 08-07-2026, w arkuszu tylko
# blok segmentu 29-30.06 (kol DD), brak 01-05.07.
# ===========================================================================
# Tydzień docelowy = REALNIE przepadły 2026-06-29..07-05 (payday 08-07-2026).
PS_START = date(2026, 6, 29)
PS_END = date(2026, 7, 5)
PS_PAYDAY = "08-07-2026"
PS_SEG1_RANGE = "29-30.06.2026"   # segment czerwcowy — OBECNY w arkuszu
PS_SEG2_RANGE = "01-05.07.2026"   # segment lipcowy — BRAKUJĄCY


def _grid_partial_split_seg1_only(ws=None):
    """Grid z blokiem TYLKO segmentu czerwcowego (29-30.06.2026, payday
    08-07-2026) @ AQ (idx 42). Segment lipcowy (01-05.07.2026) BRAKUJE.

    → find_target (segments=2, candidates=['AQ']) rzuca PartialSplitBlockError,
    NIE AmbiguousTargetError.
    """
    n = 46
    row1 = [""] * n
    row2 = [""] * n
    row2[42] = "COD - Transport"   # AQ
    row2[43] = "Korekty"
    row2[44] = "Wypłata"
    row2[45] = "Saldo do przen."
    row1[42] = "Tydzień 29-30.06.2026"
    row1[43] = "wypłata z dn."
    row1[44] = PS_PAYDAY             # pos+2 payday (klucz primary)
    row1[45] = PS_SEG1_RANGE         # pos+3 zakres
    return {
        "ws": ws if ws is not None else MagicMock(),
        "row1": row1,
        "row2": row2,
        "restaurants": [(3, "Arsenał Panteon"), (5, "Toriko")],
    }


# ---------------------------------------------------------------------------
# PS1. Detekcja: find_target rzuca PartialSplitBlockError (NIE Ambiguous)
# ---------------------------------------------------------------------------
def test_partial_split_detected_as_partial_not_ambiguous():
    grid = _grid_partial_split_seg1_only()
    try:
        sw.find_target_cod_columns(grid["row1"], grid["row2"], PS_START, PS_END)
        assert False, "powinno rzucić PartialSplitBlockError"
    except PartialSplitBlockError as e:
        # znaleziony segment czerwcowy @ AQ; brakuje lipcowego
        assert len(e.found) == 1 and e.found[0]["col_letter"] == "AQ"
        assert len(e.missing_segments) == 1
        m = e.missing_segments[0]
        assert m["start"] == date(2026, 7, 1) and m["end"] == PS_END
    except AmbiguousTargetError:
        assert False, "REGRESJA: partial split znów leci jako Ambiguous → exit 1"


# ---------------------------------------------------------------------------
# PS2. Flag ON — auto-create TYLKO brakującego segmentu + zapis obu (exit 0)
# ---------------------------------------------------------------------------
def test_partial_split_autocreate_creates_missing_only_and_writes(monkeypatch):
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_DRY_RUN", raising=False)
    ws = MagicMock()
    grid = _grid_partial_split_seg1_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(PS_START, PS_END)

    assert rc == 0, "partial split + auto-create → blok brakujący utworzony → exit 0"
    # ensure_week_block utworzył DOKŁADNIE 1 blok (brakujący lipcowy), NIE 2.
    ws.batch_update.assert_called_once()
    args, _ = ws.batch_update.call_args
    updates = args[0]
    assert len(updates) == 1, f"tylko 1 brakujący blok tworzony, got {len(updates)}"
    flat = str(updates)
    assert PS_SEG2_RANGE in flat, "utworzony blok ma zakres brakującego segmentu"
    assert PS_SEG1_RANGE not in flat, (
        "NIE duplikuje istniejącego segmentu czerwcowego"
    )
    assert PS_PAYDAY in flat
    # nowy blok ląduje w AU (za istniejącym @AQ, 4-kol blok od idx 46)
    assert col_idx_to_letter(46) == "AU"
    # zapis COD idzie do OBU segmentów: AQ (istniejący) + AU (nowy)
    cols = sorted(c for c, _ in writes)
    assert cols == ["AQ", "AU"], f"COD zapisany do obu segmentów, got {cols}"


# ---------------------------------------------------------------------------
# PS3. Flag OFF — actionable nazywający TYLKO brakujący segment, exit 1, brak zapisu
# ---------------------------------------------------------------------------
def test_partial_split_flag_off_actionable_no_write(monkeypatch):
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_BLOCK", raising=False)
    ws = MagicMock()
    grid = _grid_partial_split_seg1_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(PS_START, PS_END)

    assert rc == 1, "partial split bez auto-create → exit 1 (OnFailure/staleness)"
    assert writes == [], "brak zapisu COD gdy brakuje bloku"
    ws.batch_update.assert_not_called()  # auto-create OFF → nic nie tworzy
    assert alerts, "musi pójść actionable alert"
    msg = alerts[0]
    assert "Akcja Rafał" in msg
    assert PS_SEG2_RANGE in msg, "actionable nazywa brakujący segment lipcowy"
    assert PS_PAYDAY in msg
    # actionable mówi o TYLKO brakującym bloku (nie duplikować istniejącego)
    assert "nie duplikuj" in msg.lower()


# ---------------------------------------------------------------------------
# PS4. Flag ON + DRY-RUN — plan brakującego bloku, NIC nie zapisano (exit 1)
# ---------------------------------------------------------------------------
def test_partial_split_autocreate_dry_run_no_write(monkeypatch):
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_DRY_RUN", "1")
    ws = MagicMock()
    grid = _grid_partial_split_seg1_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(PS_START, PS_END)

    assert rc == 1, "dry-run nie tworzy → exit 1"
    ws.batch_update.assert_not_called()
    assert writes == []
    assert alerts and "DRY-RUN" in alerts[0]
    assert PS_SEG2_RANGE in alerts[0], "dry-run pokazuje brakujący segment"


# ---------------------------------------------------------------------------
# PS5. Genuine ambiguity (2 bloki dla tego samego miesiąca) NADAL Ambiguous
#      → exit 1, NIGDY auto-create (partial-fix nie osłabił detekcji błędu)
# ---------------------------------------------------------------------------
def test_partial_split_true_ambiguity_stays_ambiguous(monkeypatch):
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    ws = MagicMock()
    grid = _grid_partial_split_seg1_only(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    # find_target zwraca genuine Ambiguous (np. duplikat bloku) — cmd_write
    # MUSI to trzymać jako błąd (exit 1, nie auto-create), nie mylić z partial.
    def _raise_ambiguous(*a, **k):
        raise AmbiguousTargetError("2 bloki dla miesiąca 7: DD i DH")

    monkeypatch.setattr(rw, "find_target_cod_columns", _raise_ambiguous)
    rc = rw.cmd_write(PS_START, PS_END)

    assert rc == 1
    ws.batch_update.assert_not_called()   # Ambiguous → NIGDY auto-create
    assert writes == []


# ---------------------------------------------------------------------------
# PS6. MUTATION-CHECK — partial-split flaga ON faktycznie zmienia zachowanie
#      (ON tworzy+zapisuje exit 0; OFF actionable exit 1). Dowód że test nie ślepy.
# ---------------------------------------------------------------------------
def test_partial_split_mutation_flag_on_vs_off(monkeypatch):
    # OFF → exit 1, brak zapisu
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_BLOCK", raising=False)
    ws_off = MagicMock()
    grid_off = _grid_partial_split_seg1_only(ws_off)
    alerts_off, writes_off = _install_write_path(monkeypatch, grid_off)
    rc_off = rw.cmd_write(PS_START, PS_END)

    # ON → exit 0, zapis do obu
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    ws_on = MagicMock()
    grid_on = _grid_partial_split_seg1_only(ws_on)
    alerts_on, writes_on = _install_write_path(monkeypatch, grid_on)
    rc_on = rw.cmd_write(PS_START, PS_END)

    assert rc_off == 1 and writes_off == [], "OFF: actionable exit 1"
    assert rc_on == 0 and len(writes_on) == 2, "ON: auto-create + zapis obu, exit 0"
    assert (rc_off, bool(writes_off)) != (rc_on, bool(writes_on)), (
        "mutation-check: flaga partial-split ON≠OFF nie zmienia zachowania"
    )


# ---------------------------------------------------------------------------
# PS7. TWIN find_target_column_auto — partial wykryty po ZAKRESIE (payday pusty)
#      Bliźniacza ścieżka fallback (E5): istniejący blok bez ręcznej daty wypłaty,
#      ale z zakresem → primary NoTargetColumnError → auto-detect → partial.
# ---------------------------------------------------------------------------
def _grid_partial_split_seg1_range_no_payday(ws=None):
    n = 46
    row1 = [""] * n
    row2 = [""] * n
    row2[42] = "COD - Transport"
    row2[43] = "Korekty"
    row2[44] = "Wypłata"
    row2[45] = "Saldo do przen."
    row1[42] = "Tydzień 29-30.06.2026"
    row1[43] = "wypłata z dn."
    row1[44] = ""                    # payday PUSTY → primary nie matchuje
    row1[45] = PS_SEG1_RANGE         # ale zakres jest → auto-detect matchuje
    return {
        "ws": ws if ws is not None else MagicMock(),
        "row1": row1,
        "row2": row2,
        "restaurants": [(3, "Arsenał Panteon"), (5, "Toriko")],
    }


def test_partial_split_via_autodetect_range_match():
    grid = _grid_partial_split_seg1_range_no_payday()
    # primary rzuca NoTargetColumnError (brak payday-match)...
    try:
        sw.find_target_cod_columns(grid["row1"], grid["row2"], PS_START, PS_END)
        assert False, "primary powinien rzucić NoTargetColumnError (payday pusty)"
    except PartialSplitBlockError:
        assert False, "primary NIE powinien zaatrybuować (payday pusty)"
    except NoTargetColumnError:
        pass
    # ...a auto-detect (po zakresie) wykrywa partial: seg1 obecny, seg2 brak
    try:
        sw.find_target_column_auto(grid["row1"], grid["row2"], PS_START, PS_END)
        assert False, "auto-detect powinien rzucić PartialSplitBlockError"
    except PartialSplitBlockError as e:
        assert len(e.found) == 1 and e.found[0]["col_letter"] == "AQ"
        assert len(e.missing_segments) == 1
        assert e.missing_segments[0]["start"] == date(2026, 7, 1)


def test_partial_split_via_autodetect_end_to_end_autocreate(monkeypatch):
    """cmd_write przez resilient → auto-detect → partial → auto-create seg2 → zapis."""
    monkeypatch.setenv("COD_WEEKLY_AUTOCREATE_BLOCK", "1")
    monkeypatch.delenv("COD_WEEKLY_AUTOCREATE_DRY_RUN", raising=False)
    ws = MagicMock()
    grid = _grid_partial_split_seg1_range_no_payday(ws)
    alerts, writes = _install_write_path(monkeypatch, grid)

    rc = rw.cmd_write(PS_START, PS_END)

    assert rc == 0, "auto-detect partial + auto-create → exit 0"
    ws.batch_update.assert_called_once()
    args, _ = ws.batch_update.call_args
    assert len(args[0]) == 1, "tylko brakujący blok (seg2) tworzony"
    cols = sorted(c for c, _ in writes)
    assert cols == ["AQ", "AU"], f"zapis do obu segmentów, got {cols}"

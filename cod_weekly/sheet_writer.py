"""Auto-detekcja kolumny docelowej + walidacje + (KROK 4) batch write.

Kanoniczny klucz bloku = dokładny ZAKRES DAT w row1 pos+3. Data wypłaty w
row1 pos+2 jest filtrem wtórnym: chroni przed użyciem bloku z inną ważną datą,
ale sama nie identyfikuje segmentu (oba segmenty split-month mają ten sam
payday, a historyczne błędne bloki mogą go powtarzać).

Reguła wypłaty: środa (+3 dni) po niedzieli tygodnia.
"""
import calendar
import logging
import re
from datetime import date, timedelta
from typing import List, Tuple, Dict

import gspread
from google.oauth2.service_account import Credentials

from dispatch_v2.cod_weekly.config import (
    SERVICE_ACCOUNT_PATH,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
    ROW_START,
    SEARCH_BLOCK_START_COL,
    BLOCK_WIDTH,
    BLOCK_ROW2_HEADERS,
)
from dispatch_v2.cod_weekly.aliases import SHEET_SKIP_PREFIXES
from dispatch_v2.cod_weekly.week_calculator import format_week_for_header

log = logging.getLogger("cod_weekly.sheet")

# Payday cell format w row1 pos+2 ("Wypłata"): DD-MM-YYYY
PAYDAY_RE = re.compile(r"^\s*(\d{2})-(\d{2})-(\d{4})\s*$")

SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]


class NoTargetColumnError(Exception):
    pass


class AmbiguousTargetError(Exception):
    pass


class PartialSplitBlockError(NoTargetColumnError):
    """Rozbity tydzień z NIEPEŁNYM pokryciem bloków: część segmentów ma blok
    w arkuszu, część NIE.

    To NIE jest niejednoznaczność struktury (Ambiguous) — to brakujący blok
    KONKRETNEGO segmentu, jednoznacznie rozwiązywalny: albo auto-create bloku
    TYLKO brakującego segmentu (istniejący nietknięty → zero duplikatów), albo
    aktionable instrukcja nazywająca dokładnie brakujący okres. Podklasa
    NoTargetColumnError (semantycznie „brak bloku"), ale niesie dodatkowo:
      - `found`            — lista target-dictów segmentów JUŻ obecnych w arkuszu,
      - `missing_segments` — lista {start, end} segmentów bez bloku.

    Root-cause fix 2026-07-08 (partial-split): wcześniej `len(candidates)!=2`
    dla rozbitego tygodnia leciało AmbiguousTargetError → cmd_write traktował
    to jako strukturę niejednoznaczną (NIGDY nie auto-create) → cotygodniowy
    exit 1 na tygodniach krosujących miesiąc z 1 z 2 bloków (np. 06.07:
    payday=08-07-2026, segments=2, candidates=['DD'] → „Oczekiwano 2, znaleziono 1").
    """

    def __init__(self, found, missing_segments, message=None):
        self.found = found
        self.missing_segments = missing_segments
        if message is None:
            miss = ", ".join(
                format_week_for_header(s["start"], s["end"])
                for s in missing_segments
            )
            have = ", ".join(t["col_letter"] for t in found) or "—"
            message = (
                f"Rozbity tydzień: {len(found)}/"
                f"{len(found) + len(missing_segments)} segmentów ma blok "
                f"(kolumny {have}); brakuje bloku dla: {miss}"
            )
        super().__init__(message)


def col_idx_to_letter(n_zero_based: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA', 54 → 'BC'."""
    n = n_zero_based + 1
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _norm_header(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-]+", " ", s)
    return s


def split_week_by_month(start: date, end: date) -> List[Dict]:
    """1 segment (cały miesiąc) lub 2 segmenty (krosuje miesiąc)."""
    if start.month == end.month and start.year == end.year:
        return [{"start": start, "end": end}]
    last_of_start = date(
        start.year, start.month,
        calendar.monthrange(start.year, start.month)[1],
    )
    first_of_end = date(end.year, end.month, 1)
    return [
        {"start": start, "end": last_of_start},
        {"start": first_of_end, "end": end},
    ]


def compute_payday(week_start: date, week_end: date) -> date:
    """Środa (+3 dni) po niedzieli tygodnia.

    Gdy segment jest częścią rozbitego tygodnia, używamy pełnej niedzieli
    tygodnia (week_start..+6 dni), nie końca segmentu.
    """
    sunday = week_start + timedelta(days=(6 - week_start.weekday()))
    if sunday.weekday() != 6:
        sunday = week_end if week_end.weekday() == 6 else week_start + timedelta(days=6)
    return sunday + timedelta(days=3)


def find_target_cod_columns(
    row1: list,
    row2: list,
    target_week_start: date,
    target_week_end: date,
) -> List[Dict]:
    """Zwróć kolumnę każdego segmentu, deterministycznie po exact-range.

    Kontrakt wyboru:
      1. row2 anchor musi być ``COD - Transport``;
      2. row1 pos+3 musi dokładnie odpowiadać zakresowi segmentu (po normalizacji
         białych znaków i separatora); to jest kanoniczny klucz właściciela;
      3. payday jest filtrem wtórnym: pusty/nieparsowalny nie odbiera trafienia
         exact-range (zgodność z E5), ale inna poprawna data je dyskwalifikuje;
      4. dwa trafienia tego samego exact-range pozostają twardą ambiguity;
      5. bloki pasujące wyłącznie payday są logowane i ignorowane.

    Wynik zachowuje porządek chronologiczny segmentów. Brak części zakresów
    zwraca ``PartialSplitBlockError`` z już znalezionymi segmentami, dzięki
    czemu auto-create nadal tworzy tylko brakujące bloki.
    """
    segments = split_week_by_month(target_week_start, target_week_end)
    payday = compute_payday(target_week_start, target_week_end)
    payday_str = payday.strftime("%d-%m-%Y")

    max_scan = min(len(row1), len(row2))
    start_col = SEARCH_BLOCK_START_COL - 1  # 0-based
    expected = [
        (
            seg,
            format_week_for_header(seg["start"], seg["end"]),
            _norm_header(format_week_for_header(seg["start"], seg["end"])),
        )
        for seg in segments
    ]
    candidates_by_range = {range_norm: [] for _, _, range_norm in expected}
    payday_only = []

    for i in range(start_col, max_scan - 3):
        r2 = row2[i] if i < len(row2) else ""
        if _norm_header(r2) != "cod transport":
            continue

        range_cell = (row1[i + 3] if i + 3 < len(row1) else "").strip()
        range_norm = _norm_header(range_cell)
        payday_cell = (row1[i + 2] if i + 2 < len(row1) else "").strip()

        if range_norm not in candidates_by_range:
            if payday_cell == payday_str:
                payday_only.append((i, range_cell))
            continue

        existing_payday = _parse_payday_cell(payday_cell)
        if existing_payday is not None and existing_payday != payday:
            log.warning(
                f"find_target: kol {col_idx_to_letter(i)} ma exact-range "
                f"{range_cell!r}, ale payday={payday_cell!r} != oczekiwany "
                f"{payday_str} — pomijam (możliwy rozjazd bloku)."
            )
            continue
        candidates_by_range[range_norm].append(i)

    if payday_only:
        ignored = [
            f"{col_idx_to_letter(i)}(range={range_cell!r})"
            for i, range_cell in payday_only
        ]
        log.warning(
            f"find_target: ignoruję payday-only dla payday={payday_str}: "
            f"{ignored}; żaden z tych bloków nie ma exact-range segmentu."
        )

    found = []
    missing_segments = []
    for seg, expected_range, range_norm in expected:
        candidates = candidates_by_range[range_norm]
        log.info(
            f"find_target: segment {seg['start']}..{seg['end']} "
            f"exact-range={expected_range!r} payday={payday_str} → "
            f"kandydaci={[col_idx_to_letter(c) for c in candidates]}"
        )
        if not candidates:
            missing_segments.append({"start": seg["start"], "end": seg["end"]})
            continue
        if len(candidates) > 1:
            raise AmbiguousTargetError(
                f"{len(candidates)} kolumn z exact-range {expected_range!r}: "
                f"{[col_idx_to_letter(c) for c in candidates]}"
            )
        ci = candidates[0]
        found.append(
            {
                "col_idx": ci,
                "col_letter": col_idx_to_letter(ci),
                "segment_start": seg["start"],
                "segment_end": seg["end"],
                "payday": payday,
            }
        )

    if missing_segments and not found:
        missing_ranges = ", ".join(
            format_week_for_header(s["start"], s["end"])
            for s in missing_segments
        )
        ignored_cols = [col_idx_to_letter(i) for i, _ in payday_only]
        ignored_note = (
            f" Payday-only z obcym zakresem (pominięte): {ignored_cols}."
            if ignored_cols else ""
        )
        raise NoTargetColumnError(
            f"Brak bloku z exact-range: {missing_ranges} "
            f"(oczekiwany payday {payday_str}).{ignored_note}"
        )
    if missing_segments:
        raise PartialSplitBlockError(found, missing_segments)
    return found


def _parse_payday_cell(s: str):
    """Parse 'DD-MM-YYYY' z komórki payday (row1 pos+2) → date albo None."""
    m = PAYDAY_RE.match(s or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def find_target_column_auto(
    row1: list,
    row2: list,
    target_week_start: date,
    target_week_end: date,
) -> List[Dict]:
    """Kompatybilny alias kanonicznego selektora range-first.

    Nazwa pozostaje dla istniejących importerów i testów E5, ale nie utrzymuje
    drugiej polityki wyboru. Każda ścieżka korzysta z tego samego kontraktu:
    exact-range jako owner, payday jako filtr wtórny.
    """
    return find_target_cod_columns(
        row1, row2, target_week_start, target_week_end,
    )


def _open_worksheet():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=SCOPES_RW)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)


def fetch_sheet_grid() -> Dict:
    """Zwraca {ws, row1, row2, restaurants: [(row_idx_1based, name)]}."""
    ws = _open_worksheet()
    batch = ws.batch_get(["1:2", "A:A"])
    rows_12 = batch[0] if len(batch) > 0 else []
    row1 = rows_12[0] if len(rows_12) > 0 else []
    row2 = rows_12[1] if len(rows_12) > 1 else []
    col_a_raw = batch[1] if len(batch) > 1 else []
    restaurants = []
    for idx0, row in enumerate(col_a_raw):
        row_idx = idx0 + 1
        if row_idx < ROW_START:
            continue
        val = (row[0] if row else "").strip()
        if not val:
            continue
        if val.startswith(SHEET_SKIP_PREFIXES):
            continue
        restaurants.append((row_idx, val))
    return {"ws": ws, "row1": row1, "row2": row2, "restaurants": restaurants}


def write_cod_column_skip_filled(
    ws,
    col_letter: str,
    row_to_value: Dict[int, float],
    dry_run: bool = False,
) -> Dict:
    """Batch write float do col_letter. SKIP wiersze które już mają niepustą wartość.

    row_to_value: {row_1based: cod_float}
    Returns: {written_rows, skipped_filled, skipped_errors}
    Wykonuje MAX 2 API calls (1× batch_get, 1× batch_update).
    """
    if not row_to_value:
        return {"written_rows": [], "skipped_filled": [], "skipped_errors": []}
    rows_sorted = sorted(row_to_value.keys())
    lo = rows_sorted[0]
    hi = rows_sorted[-1]
    current = ws.batch_get([f"{col_letter}{lo}:{col_letter}{hi}"])
    rows = current[0] if current else []
    skipped_filled = []
    updates = []
    for row in rows_sorted:
        idx0 = row - lo
        existing = ""
        if idx0 < len(rows):
            cell = rows[idx0]
            existing = (cell[0] if cell else "").strip()
        if existing:
            skipped_filled.append({"row": row, "existing": existing})
            continue
        updates.append(
            {"range": f"{col_letter}{row}", "values": [[row_to_value[row]]]}
        )
    if updates and not dry_run:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return {
        "dry_run": dry_run,
        "written_rows": [(u["range"], u["values"][0][0]) for u in updates],
        "skipped_filled": skipped_filled,
        "skipped_errors": [],
    }


def validate_column_empty_ratio(
    ws,
    col_letter: str,
    row_indices: List[int],
    threshold: float = 0.8,
) -> Dict:
    """Sprawdza że kolumna col_letter jest pusta dla ≥threshold% wierszy restauracji."""
    if not row_indices:
        return {"ok": True, "empty_count": 0, "total": 0, "ratio": 1.0, "filled_sample": []}
    lo = min(row_indices)
    hi = max(row_indices)
    range_str = f"{col_letter}{lo}:{col_letter}{hi}"
    vals_raw = ws.batch_get([range_str])
    vals = vals_raw[0] if vals_raw else []
    row_set = set(row_indices)
    empty_count = 0
    filled_sample = []
    for idx0, row in enumerate(vals):
        row_idx = lo + idx0
        if row_idx not in row_set:
            continue
        v = (row[0] if row else "").strip()
        if v == "":
            empty_count += 1
        else:
            if len(filled_sample) < 5:
                filled_sample.append((row_idx, v))
    # Wiersze poza zakresem vals — traktujemy jako puste (brak danych = pusta komórka)
    covered = 0
    for idx0, row in enumerate(vals):
        row_idx = lo + idx0
        if row_idx in row_set:
            covered += 1
    uncovered = len(row_set) - covered
    empty_count += uncovered
    total = len(row_indices)
    ratio = empty_count / total if total > 0 else 1.0
    return {
        "ok": ratio >= threshold,
        "empty_count": empty_count,
        "total": total,
        "ratio": ratio,
        "filled_sample": filled_sample,
    }


def _last_used_col(row1: list, row2: list) -> int:
    """Najbardziej wysunięty w prawo indeks 0-based z niepustą komórką w row1/row2.

    -1 gdy oba puste. Użyte do policzenia miejsca DOPISANIA nowego bloku — nowy
    blok ląduje za CAŁĄ dotychczasową treścią, więc content-based detekcja go
    znajdzie i nie koliduje z istniejącymi (ewentualne gapy 4/5 kolumn nieistotne).
    """
    last = -1
    for row in (row1, row2):
        for i, v in enumerate(row):
            if (v or "").strip():
                last = max(last, i)
    return last


def build_week_block_plan(
    row1: list,
    row2: list,
    target_week_start: date,
    target_week_end: date,
    segments_override: List[Dict] = None,
) -> Dict:
    """Policz (BEZ zapisu) strukturę bloku/bloków tygodnia do dopisania na końcu.

    `segments_override` (partial-split 2026-07-08): gdy podane, tworzymy bloki
    TYLKO dla tych segmentów (np. brakujący 1 z 2 przy rozbitym tygodniu, gdzie
    drugi już jest w arkuszu). Payday liczony ZAWSZE z pełnego tygodnia (wspólny
    dla obu segmentów). Domyślnie None → wszystkie segmenty (zachowanie sprzed).

    Zwraca plan {payday, segments, append_at_col, blocks:[...], new_row1,
    new_row2}. `new_row1/new_row2` = kopie z wpisanymi komórkami nowego bloku
    (dry-run → podgląd; realny zapis → retry find_target bez 2. round-tripu API).

    Layout bloku (BLOCK_WIDTH=4, wzór arkusza 'Wynagrodzenia Gastro'):
      pos+0  row2='COD - Transport'  ← kolumna wartości COD (zostaje PUSTA)
      pos+1  row2='Korekty'
      pos+2  row2='Wypłata'          row1=payday DD-MM-YYYY  ← klucz payday-match
      pos+3  row2='Saldo do przen.'  row1=zakres DD-DD.MM.YYYY ← klucz range-match

    Split-month → 2 bloki (ten sam payday, różne zakresy). Wołane WYŁĄCZNIE gdy
    find_target (payday + range) już NIC nie znalazł → dopisanie kompletnego
    bloku nie duplikuje istniejącego kompletnego bloku.
    """
    segments = (
        segments_override
        if segments_override is not None
        else split_week_by_month(target_week_start, target_week_end)
    )
    payday = compute_payday(target_week_start, target_week_end)
    payday_str = payday.strftime("%d-%m-%Y")

    append_at = max(_last_used_col(row1, row2) + 1, SEARCH_BLOCK_START_COL - 1)

    max_len = max(len(row1), len(row2))
    new_row1 = list(row1) + [""] * (max_len - len(row1))
    new_row2 = list(row2) + [""] * (max_len - len(row2))

    blocks = []
    col = append_at
    for seg in segments:
        week_range = format_week_for_header(seg["start"], seg["end"])
        row1_cells = [f"Tydzień {week_range}", "wypłata z dn.", payday_str, week_range]
        row2_cells = list(BLOCK_ROW2_HEADERS)
        need = col + BLOCK_WIDTH
        if len(new_row1) < need:
            new_row1 += [""] * (need - len(new_row1))
        if len(new_row2) < need:
            new_row2 += [""] * (need - len(new_row2))
        for off in range(BLOCK_WIDTH):
            new_row1[col + off] = row1_cells[off]
            new_row2[col + off] = row2_cells[off]
        cod_col_letter = col_idx_to_letter(col)
        end_letter = col_idx_to_letter(col + BLOCK_WIDTH - 1)
        blocks.append({
            "segment_start": seg["start"],
            "segment_end": seg["end"],
            "week_range": week_range,
            "payday": payday_str,
            "cod_col_idx": col,
            "cod_col_letter": cod_col_letter,
            "range_a1": f"{cod_col_letter}1:{end_letter}2",
            "row1_cells": row1_cells,
            "row2_cells": row2_cells,
        })
        col += BLOCK_WIDTH

    return {
        "payday": payday_str,
        "segments": [{"start": s["start"], "end": s["end"]} for s in segments],
        "append_at_col": col_idx_to_letter(append_at),
        "blocks": blocks,
        "new_row1": new_row1,
        "new_row2": new_row2,
    }


# Ile kolumn zapasu dodać ponad minimum przy rozszerzaniu siatki, żeby nie
# resize'ować arkusza co tydzień (blok ≈ 4-5 kol → ~5 tygodni zapasu).
GRID_COL_BUFFER = 24


def _a1_end_col(range_a1: str) -> int:
    """Numer (1-based) najdalszej kolumny zakresu A1: 'DL1:DO2' → 119, 'A1:D2' → 4.

    Własny parser zamiast gspread.utils — w środowisku testów silnika gspread jest
    atrapą bez `.utils`, a to jedyna potrzebna operacja.
    """
    end = range_a1.split(":")[-1]
    letters = "".join(ch for ch in end if ch.isalpha()).upper()
    num = 0
    for ch in letters:
        num = num * 26 + (ord(ch) - ord("A") + 1)
    return num


def _ensure_grid_capacity(ws, plan: Dict) -> Dict:
    """Rozszerz siatkę arkusza, jeśli blok tygodnia wyszedłby poza jej kolumny.

    Root cause FAILA 2026-07-13: blok dopisywany za ostatnią używaną kolumną
    wychodził poza sufit siatki (Max columns: 115) → APIError 'exceeds grid
    limits' → exit 1 w każdy poniedziałek. Blok jest pozycjonowany poprawnie —
    brakowało tylko miejsca w siatce, więc dokładamy kolumny (puste, bez danych).

    Zwraca {expanded, from_cols, to_cols, needed}. Bezpieczne dla mocków: gdy
    ws.col_count nie jest int (test bez ustawionej siatki), NIE rusza siatki.
    """
    needed = 0
    for b in plan.get("blocks", []):
        rng = b.get("range_a1")
        if rng:
            needed = max(needed, _a1_end_col(rng))
    current = getattr(ws, "col_count", None)
    if not isinstance(current, int) or needed <= current:
        return {"expanded": False, "from_cols": current, "to_cols": current, "needed": needed}
    add = needed - current + GRID_COL_BUFFER
    ws.add_cols(add)
    log.info(
        f"ensure_week_block: siatka {current} kol < potrzebne {needed} → "
        f"add_cols(+{add}) → {current + add} kol (bufor {GRID_COL_BUFFER})"
    )
    return {"expanded": True, "from_cols": current, "to_cols": current + add, "needed": needed}


def ensure_week_block(
    ws,
    row1: list,
    row2: list,
    target_week_start: date,
    target_week_end: date,
    dry_run: bool = False,
    segments_override: List[Dict] = None,
) -> Dict:
    """Dopisz brakujący blok tygodnia (nagłówki row2 + payday/zakres row1).

    NIE dotyka danych COD — kolumny wartości (pos+0) zostają PUSTE, więc
    empty_check przejdzie i normalny zapis wypełni je świeżo. Zapis do arkusza
    TYLKO gdy dry_run=False (1 batch_update, po jednym zakresie A1 na segment).
    Zwraca ten sam plan co build_week_block_plan + {dry_run, created}.

    `segments_override` (partial-split): tworzy TYLKO wskazane segmenty (bloki
    brakujące), nie duplikując tych już obecnych w arkuszu.
    """
    plan = build_week_block_plan(
        row1, row2, target_week_start, target_week_end,
        segments_override=segments_override,
    )
    plan["dry_run"] = dry_run
    if dry_run:
        plan["created"] = False
        return plan
    updates = [
        {"range": b["range_a1"], "values": [b["row1_cells"], b["row2_cells"]]}
        for b in plan["blocks"]
    ]
    plan["grid_expand"] = _ensure_grid_capacity(ws, plan)
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    plan["created"] = True
    return plan

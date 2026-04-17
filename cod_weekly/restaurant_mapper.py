"""Mapping restauracja (arkusz) → company_id (panel) — v2.

Strategia match (w kolejności):
  A. ALIAS_MAP override (explicit, dla literówek/skrótów/multi-company)
  B. Strict equality po normalize()
  C. Token-subset: wszystkie tokeny arkusza ⊆ tokeny panelu (unique match)
  D. Starts-with po normalize (unique match)
  E. UNMATCHED

Fuzzy/Levenshtein CELOWO wyłączony — był źródłem cichego błędu
"350 stopni" → "_500 stopni" (id 28 zamiast 114).

Value w mapping może być int (single company) lub list[int] (multi-company sum).
"""
import argparse
import html as html_lib
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.panel_client import login
from dispatch_v2.cod_weekly.config import (
    PANEL_DROPDOWN_URL,
    MAPPING_PATH,
    WARSAW,
    SPREADSHEET_ID,
    WORKSHEET_NAME,
    SERVICE_ACCOUNT_PATH,
    ROW_START,
)
from dispatch_v2.cod_weekly.aliases import ALIAS_MAP, SHEET_SKIP_PREFIXES

log = logging.getLogger("cod_weekly.mapper")

OPTION_RE = re.compile(r'<option[^>]*value=["\'](\d+)["\'][^>]*>([^<]+)</option>')
SELECT_RE = re.compile(
    r'<select[^>]*name=["\']company["\'][^>]*>(.*?)</select>',
    re.DOTALL | re.I,
)

PANEL_PREFIXES_TO_STRIP = ("restauracja ",)
SHEET_SUFFIXES_TO_STRIP = (
    " sp. c.",
    " sp.c.",
    " sp. z o.o.",
    " sp z o o",
    " sp zoo",
)
PANEL_SUFFIXES_TO_STRIP = (" nieaktywne", " restauracja")
INACTIVE_MARKER = "NIEAKTYWNE"


def normalize(s: str) -> str:
    """Normalizacja do porównania: HTML decode, diakrytyki, underscore, prefix/suffix, nawiasy, dedup słów."""
    s = html_lib.unescape(s)                                 # &#039; → '
    s = s.strip().lstrip("_").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = s.replace("ł", "l").replace("Ł", "l")                # NFKD nie rozkłada L-with-stroke
    for prefix in PANEL_PREFIXES_TO_STRIP:
        if s.startswith(prefix):
            s = s[len(prefix):]
    for suffix in PANEL_SUFFIXES_TO_STRIP + SHEET_SUFFIXES_TO_STRIP:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)                   # strip trailing " (...)"
    s = s.replace(" & ", " and ")
    s = re.sub(r"\s+", " ", s).strip()
    parts = s.split()
    deduped = []
    for p in parts:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return " ".join(deduped)


def scrape_panel_dropdown() -> dict:
    """{panel_name_original: company_id} z <select name="company">."""
    opener, _, _ = login()
    res = opener.open(PANEL_DROPDOWN_URL, timeout=20)
    html = res.read().decode("utf-8", errors="replace")
    m = SELECT_RE.search(html)
    if not m:
        raise RuntimeError("Brak <select name=company> w HTML panelu")
    out = {}
    for cid, name in OPTION_RE.findall(m.group(1)):
        out[html_lib.unescape(name.strip())] = int(cid)
    if len(out) < 50:
        raise RuntimeError(f"Dropdown zwrócił {len(out)} opcji (<50) — podejrzane")
    return out


def build_panel_index(panel_dict: dict) -> dict:
    """{normalized_name: original_name} — preferuj aktywną wersję gdy duplikat po normalize."""
    norm_to_orig = {}
    for pname in panel_dict:
        key = normalize(pname)
        existing = norm_to_orig.get(key)
        if existing is None:
            norm_to_orig[key] = pname
            continue
        if INACTIVE_MARKER in existing and INACTIVE_MARKER not in pname:
            norm_to_orig[key] = pname
    return norm_to_orig


def fetch_sheet_restaurants() -> list:
    """[(row_idx_1based, name)] z kolumny A arkusza od ROW_START, bez agregatów."""
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(SPREADSHEET_ID).worksheet(WORKSHEET_NAME)
    col_a = ws.col_values(1)
    out = []
    for idx, val in enumerate(col_a, start=1):
        if idx < ROW_START:
            continue
        v = val.strip()
        if not v:
            continue
        if v.startswith(SHEET_SKIP_PREFIXES):
            continue
        out.append((idx, v))
    return out


def _resolve_alias(sheet_orig: str, panel_dict: dict):
    """Returns (company_id or list[int]) or None."""
    if sheet_orig not in ALIAS_MAP:
        return None
    target = ALIAS_MAP[sheet_orig]
    panel_norm_to_orig = build_panel_index(panel_dict)
    def lookup(panel_name: str):
        key = normalize(panel_name)
        if key in panel_norm_to_orig:
            return panel_dict[panel_norm_to_orig[key]]
        if panel_name in panel_dict:
            return panel_dict[panel_name]
        raise KeyError(f"ALIAS_MAP cel '{panel_name}' nie znaleziony w panelu")
    if isinstance(target, list):
        return [lookup(p) for p in target]
    return lookup(target)


def _strict_match(sheet_norm: str, panel_norm_to_orig: dict):
    return panel_norm_to_orig.get(sheet_norm)


def _token_subset_match(sheet_norm: str, panel_norm_to_orig: dict):
    sheet_tokens = set(sheet_norm.split())
    if not sheet_tokens:
        return None
    candidates = [
        pn for pn in panel_norm_to_orig
        if sheet_tokens.issubset(set(pn.split()))
    ]
    if len(candidates) == 1:
        return panel_norm_to_orig[candidates[0]]
    return None


def _starts_with_match(sheet_norm: str, panel_norm_to_orig: dict):
    candidates = [pn for pn in panel_norm_to_orig if pn.startswith(sheet_norm)]
    if len(candidates) == 1:
        return panel_norm_to_orig[candidates[0]]
    return None


def match_restaurants(sheet_names, panel_dropdown):
    """Returns dict:
        mapping: {sheet_name_original: company_id | list[int]}
        unmatched: [sheet_name]
        method_per_entry: {sheet_name: "alias"|"strict"|"token"|"startswith"}
        unused_panel: [panel_name]
    """
    panel_norm_to_orig = build_panel_index(panel_dropdown)
    mapping = {}
    unmatched = []
    method = {}
    used_panel_names = set()
    for _, sname in sheet_names:
        alias_res = _resolve_alias(sname, panel_dropdown)
        if alias_res is not None:
            mapping[sname] = alias_res
            method[sname] = "alias"
            target = ALIAS_MAP[sname]
            for t in (target if isinstance(target, list) else [target]):
                used_panel_names.add(t)
            continue
        key = normalize(sname)
        panel_orig = _strict_match(key, panel_norm_to_orig)
        if panel_orig is not None:
            mapping[sname] = panel_dropdown[panel_orig]
            method[sname] = "strict"
            used_panel_names.add(panel_orig)
            continue
        panel_orig = _token_subset_match(key, panel_norm_to_orig)
        if panel_orig is not None:
            mapping[sname] = panel_dropdown[panel_orig]
            method[sname] = "token"
            used_panel_names.add(panel_orig)
            continue
        panel_orig = _starts_with_match(key, panel_norm_to_orig)
        if panel_orig is not None:
            mapping[sname] = panel_dropdown[panel_orig]
            method[sname] = "startswith"
            used_panel_names.add(panel_orig)
            continue
        unmatched.append(sname)
    unused_panel = [p for p in panel_dropdown if p not in used_panel_names]
    return {
        "mapping": mapping,
        "unmatched": unmatched,
        "method_per_entry": method,
        "unused_panel": unused_panel,
    }


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def build_and_save() -> dict:
    log.info("Scraping panel dropdown...")
    panel = scrape_panel_dropdown()
    log.info(f"Panel: {len(panel)} options")
    log.info("Fetching arkusz column A...")
    rows = fetch_sheet_restaurants()
    log.info(f"Arkusz: {len(rows)} niepustych restauracji (bez agregatów)")
    res = match_restaurants(rows, panel)
    method_counts = {}
    for m in res["method_per_entry"].values():
        method_counts[m] = method_counts.get(m, 0) + 1
    payload = {
        "mapping": res["mapping"],
        "unmatched_sheet": res["unmatched"],
        "unused_panel": res["unused_panel"],
        "method_per_entry": res["method_per_entry"],
        "generated_at": datetime.now(WARSAW).isoformat(),
        "source": "panel_dropdown_scrape_v2",
        "counts": {
            "sheet_total": len(rows),
            "panel_total": len(panel),
            "matched": len(res["mapping"]),
            "unmatched_sheet": len(res["unmatched"]),
            "unused_panel": len(res["unused_panel"]),
            "by_method": method_counts,
        },
    }
    atomic_write_json(MAPPING_PATH, payload)
    log.info(f"Zapisano → {MAPPING_PATH}")
    return payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="Scrape + match + zapis JSON")
    ap.add_argument("--dry-run", action="store_true", help="Match bez zapisu, pełny wydruk")
    args = ap.parse_args()
    if not (args.build or args.dry_run):
        ap.print_help()
        return 2
    panel = scrape_panel_dropdown()
    rows = fetch_sheet_restaurants()
    res = match_restaurants(rows, panel)
    method_counts = {}
    for m in res["method_per_entry"].values():
        method_counts[m] = method_counts.get(m, 0) + 1
    summary = {
        "counts": {
            "sheet_total": len(rows),
            "panel_total": len(panel),
            "matched": len(res["mapping"]),
            "unmatched": len(res["unmatched"]),
            "unused_panel": len(res["unused_panel"]),
            "by_method": method_counts,
        },
        "unmatched_sheet": res["unmatched"],
        "unused_panel": sorted(res["unused_panel"]),
        "mapping": res["mapping"],
        "method_per_entry": res["method_per_entry"],
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        payload = build_and_save()
        print(json.dumps(payload["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

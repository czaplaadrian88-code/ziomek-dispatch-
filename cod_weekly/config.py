"""F2.1d config — stałe, ścieżki, layout arkusza."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = ZoneInfo("UTC")

SPREADSHEET_ID = "1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8"
WORKSHEET_NAME = "Wynagrodzenia Gastro"
WORKSHEET_GID = 1014036023

SERVICE_ACCOUNT_PATH = "/root/.openclaw/workspace/scripts/service_account.json"
MAPPING_PATH = Path(
    "/root/.openclaw/workspace/scripts/dispatch_v2/restaurant_company_mapping.json"
)

PANEL_BASE = "https://www.gastro.nadajesz.pl"
PANEL_DROPDOWN_URL = f"{PANEL_BASE}/admin2017/orders/zlecenia"
PANEL_ORDERS_URL = f"{PANEL_BASE}/admin2017/orders/zlecenia"

# E1 dropdown scrape: strona /admin2017/orders/zlecenia jest ciężka (renderuje
# pełną listę zleceń) → ~18-25s odpowiedzi. Stary hardkod timeout=20 trafiał
# w krawędź → intermittent TimeoutError. 60s + retry daje zapas (run off-peak).
PANEL_DROPDOWN_TIMEOUT_SEC = 60
PANEL_DROPDOWN_RETRIES = 3

FUZZY_CUTOFF = 0.85
ROW_START = 3            # pierwsza restauracja w kolumnie A
COL_A_IDX = 0            # kolumna A (nazwa restauracji)
SEARCH_BLOCK_START_COL = 43   # AQ — start iteracji bloków tygodniowych
BLOCK_WIDTH = 4

# Row 2 nagłówki bloku tygodniowego (od lewej). Detekcja bloku w sheet_writer
# opiera się WYŁĄCZNIE o pierwszą komórkę (row2[i] ≈ "cod transport"); pozostałe
# trzy zachowują konwencję arkusza. Auto-create (niżej) wpisuje ten sam layout.
BLOCK_ROW2_HEADERS = ("COD - Transport", "Korekty", "Wypłata", "Saldo do przen.")


def _env_flag(name: str, default: bool = False) -> bool:
    """Odczyt flagi z ENV w CZASIE WYWOŁANIA (nie na import).

    Wzorzec zgodny z resztą stacku Ziomka: stan flag = drop-iny systemd
    (`Environment=NAME=1`), NIE flags.json. Czytanie per-wywołanie pozwala
    testom ustawić os.environ oraz daje natychmiastowy efekt po dodaniu
    drop-inu bez zmian w kodzie.
    """
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def autocreate_block_enabled() -> bool:
    """Czy wolno AUTOMATYCZNIE utworzyć brakujący blok tygodnia w arkuszu.

    Default OFF — produkcyjny flip WYŁĄCZNIE przez drop-in systemd:
        [Service]
        Environment=COD_WEEKLY_AUTOCREATE_BLOCK=1
    Uzupełnij `COD_WEEKLY_AUTOCREATE_DRY_RUN=1` aby najpierw zobaczyć CO by
    utworzył (log + alert), NIC nie zapisując.
    """
    return _env_flag("COD_WEEKLY_AUTOCREATE_BLOCK", False)


def autocreate_block_dry_run() -> bool:
    """Tryb podglądu auto-create: policz i pokaż strukturę, ale NIE zapisuj."""
    return _env_flag("COD_WEEKLY_AUTOCREATE_DRY_RUN", False)

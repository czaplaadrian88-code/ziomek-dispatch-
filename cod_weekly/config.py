"""F2.1d config — stałe, ścieżki, layout arkusza."""
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

FUZZY_CUTOFF = 0.85
ROW_START = 3            # pierwsza restauracja w kolumnie A
COL_A_IDX = 0            # kolumna A (nazwa restauracji)
SEARCH_BLOCK_START_COL = 43   # AQ — start iteracji bloków tygodniowych
BLOCK_WIDTH = 4

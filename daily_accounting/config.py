"""Daily Accounting Module — configuration."""

SPREADSHEET_ID = "1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8"
GID_OBLICZENIA = 1072741048
SHEET_NAME = "Obliczenia"

EXCLUDED_CIDS = {
    21,   # Adrian — owner account, not courier
    23,   # Rutcom (tech account)
    26,   # Koordynator (virtual czasówki holder)
    61,   # Krystian — permanent OFF od 22.04.2026
    207,  # Marek — inactive
    284,  # Mateusz L — inactive
    354,  # Filip P — inactive
    426,  # Mykyta K — inactive (Adrian: "Mykyta nie pracuje")
    476,  # Antoni Tr — inactive
    498,  # Kamil Dr — inactive
}

BAR_ELJOT_COMPANY_ID = 27

MIN_FREE_ROWS_ALERT = 30

PANEL_RETRY_ATTEMPTS = 2
PANEL_RETRY_BACKOFF_SEC = 30

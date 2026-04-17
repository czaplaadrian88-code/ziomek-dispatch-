"""Explicit aliases — override strict match dla literówek, skrótów, multi-company."""

# sheet_name (original, jak w arkuszu) → panel_name (original, jak w <option>) lub list[panel_name]
# Używane PRZED strict/token match jako explicit override.
ALIAS_MAP = {
    "Bankoo": "Baanko",                       # literówka w arkuszu
    "350 stopni": "_350 Stopni KILIŃSKIEGO",  # fuzzy by się pomylił z "_500 stopni"
    "500 stopni 500 stopni": "_500 stopni",   # duplikat w arkuszu
    "Trzy po trzy MIC": "Trzy Po Trzy Mickiewicza",
    "Trzy po trzy SIEN": "Trzy Po Trzy Sienkiewicza",
    "Mama Thai Bistro i Miejska Miska": ["Mama Thai Bistro", "Miejska Miska"],  # MULTI: suma 2 company
    "Good Boy": "Goodboy",                    # spacja vs bez spacji
}

# Wiersze arkusza do pominięcia (agregaty, nie restauracje)
SHEET_SKIP_PREFIXES = ("Suma ", "suma ")

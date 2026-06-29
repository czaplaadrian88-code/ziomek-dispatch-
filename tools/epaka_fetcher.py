#!/usr/bin/env python3
"""
Epaka fetcher dla Ziomka — pobiera przesyłki (CSV) i prowizje (do wypłaty od-do) z panelu epaka.pl.

Logowanie:
  1) re-użycie zapisanej sesji (cookie jar) — jeśli żywa, bez captchy;
  2) fallback: login login+hasło + OCR captchy w pętli (captcha odświeża się co próbę).
Prowizje liczone filtrem "Do wypłaty w okresie od-do" = data[Prowizja][data_nalezna_od/do] + status_id=1802.

Użycie:
  python3 epaka_fetcher.py --od 2026-06-22 --do 2026-06-29 [--out KATALOG]
"""
import os, sys, re, subprocess, tempfile, argparse, json, datetime
from http.cookiejar import MozillaCookieJar
import requests

SECRETS = os.environ.get("EPAKA_SECRETS", "/root/.claude/projects/-root/.secrets/epaka_panel.env")
COOKIES = os.environ.get("EPAKA_COOKIES", "/root/.claude/projects/-root/.secrets/epaka_cookies.txt")
BASE = "https://www.epaka.pl"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
LOGIN_PAGE = BASE + "/panel/pracownik/login/panel"
LOGIN_POST = BASE + "/panel/pracownik/login"
CAPTCHA = BASE + "/kontakt/captcha_image"


def load_creds():
    d = {}
    with open(SECRETS) as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip()
    return d["EPAKA_LOGIN"], d["EPAKA_HASLO"]


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    jar = MozillaCookieJar(COOKIES)
    if os.path.exists(COOKIES):
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    s.cookies = jar
    return s


def save_cookies(s):
    try:
        os.makedirs(os.path.dirname(COOKIES), exist_ok=True)
        jar = s.cookies if isinstance(s.cookies, MozillaCookieJar) else MozillaCookieJar(COOKIES)
        if not isinstance(s.cookies, MozillaCookieJar):
            for c in s.cookies:
                jar.set_cookie(c)
        jar.filename = COOKIES
        jar.save(ignore_discard=True, ignore_expires=True)
        os.chmod(COOKIES, 0o600)
    except Exception as e:
        print(f"[warn] nie zapisano cookies: {e}", file=sys.stderr)


def is_logged_in(s):
    try:
        r = s.get(BASE + "/panel", timeout=25, allow_redirects=True)
        return "pracownik/login" not in r.url and "captcha_image" not in r.text
    except Exception:
        return False


def solve_captcha(img_bytes):
    """convert (preprocessing) + tesseract; zwraca litery (może być pusty/zły)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(img_bytes); raw = f.name
    pre = raw + ".png"
    best = ""
    for args in (
        ["-colorspace", "Gray", "-resize", "400%", "-auto-threshold", "Otsu"],
        ["-colorspace", "Gray", "-resize", "300%", "-threshold", "55%"],
        ["-colorspace", "Gray", "-resize", "300%", "-median", "2", "-threshold", "60%"],
    ):
        try:
            subprocess.run(["convert", raw, *args, pre], check=True, capture_output=True)
            out = subprocess.run(
                ["tesseract", pre, "stdout", "--psm", "8", "-c",
                 "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"],
                capture_output=True, text=True).stdout
            letters = re.sub(r"[^A-Za-z]", "", out)
            if len(letters) == 3:  # captcha epaki = 3 znaki
                best = letters; break
            if len(letters) > len(best):
                best = letters
        except Exception:
            continue
    for p in (raw, pre):
        try: os.remove(p)
        except Exception: pass
    return best


def login(s, max_tries=20):
    user, pwd = load_creds()
    for i in range(1, max_tries + 1):
        try:
            s.get(LOGIN_PAGE, timeout=25)  # świeża captcha w sesji
            img = s.get(CAPTCHA, timeout=25, headers={"Referer": LOGIN_PAGE}).content
            kod = solve_captcha(img)
            if len(kod) != 3:
                continue
            r = s.post(LOGIN_POST, timeout=30, allow_redirects=False, headers={"Referer": LOGIN_PAGE},
                       data={"_method": "POST", "data[Pracownik][redirect]": "/panel",
                             "data[Pracownik][login]": user, "data[Pracownik][haslo]": pwd,
                             "data[Pracownik][kod]": kod})
            if r.status_code == 302 and "/panel" in r.headers.get("Location", ""):
                save_cookies(s)
                print(f"[ok] zalogowano (próba {i}, captcha={kod})")
                return True
        except Exception as e:
            print(f"[warn] próba {i}: {e}", file=sys.stderr)
    return False


def ensure_login(s):
    if is_logged_in(s):
        print("[ok] sesja żywa — bez logowania")
        return True
    print("[..] sesja wygasła — loguję (OCR captchy)")
    return login(s)


def fetch_shipments(s, od, do, outdir):
    s.post(BASE + "/panel/zamowienie", timeout=30, allow_redirects=True,
           data={"_method": "POST", "data[Zamowienie][data_od]": od, "data[Zamowienie][data_do]": do})
    r = s.get(BASE + "/panel/zamowienie/csv", timeout=120)
    text = r.content.decode("cp1250", errors="replace")
    path = os.path.join(outdir, f"epaka_zamowienia_{od}_{do}.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\r", "\n"))
    rows = max(0, text.replace("\r", "\n").strip().count("\n"))
    return path, rows


def fetch_prowizje(s, od, do, outdir, status_id="1802"):
    # FILTR "Do wypłaty w okresie od-do" = data_nalezna_od/do (+ status Do wypłaty)
    r = s.post(BASE + "/panel/prowizja", timeout=30, allow_redirects=True,
               data={"_method": "POST", "data[Prowizja][data_nalezna_od]": od,
                     "data[Prowizja][data_nalezna_do]": do, "data[Prowizja][status_id]": status_id})
    html = r.text
    def money(label):
        m = re.search(re.escape(label) + r"[^0-9\-]*([0-9\.\s]+,[0-9]{2})", html)
        return m.group(1).replace(" ", "").replace(".", "").replace(",", ".") if m else None
    summary = {
        "suma_prowizji": money("Suma prowizji dla wybranych parametrów"),
        "suma_oczekujacych": money("Suma prowizji oczekujących"),
        "suma_do_wyplaty": money("Suma prowizji do wypłaty"),
    }
    # wiersze tabeli: Data utworzenia | Opis | Kwota | Status | Data do wypłaty
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        tds = [re.sub(r"<[^>]+>", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
        tds = [re.sub(r"\s+", " ", t) for t in tds if t]
        if len(tds) >= 4 and re.search(r"\d{4}-\d{2}-\d{2}", tds[0]) and "zł" in " ".join(tds):
            rows.append(tds)
    path = os.path.join(outdir, f"epaka_prowizje_dowyplaty_{od}_{do}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"filtr": {"data_nalezna_od": od, "data_nalezna_do": do, "status_id": status_id},
                   "podsumowanie": summary, "liczba_wpisow": len(rows), "wpisy": rows}, f, ensure_ascii=False, indent=1)
    return path, summary, len(rows)


def main():
    ap = argparse.ArgumentParser()
    _t=datetime.date.today()
    ap.add_argument("--od", default=str(_t-datetime.timedelta(days=30)), help="data od YYYY-MM-DD (domyślnie -30 dni)")
    ap.add_argument("--do", default=str(_t), help="data do YYYY-MM-DD (domyślnie dziś)")
    ap.add_argument("--out", default="/root/.openclaw/workspace/scripts/dispatch_v2/dispatch_state/epaka_data")
    ap.add_argument("--skip-prowizje", action="store_true")
    ap.add_argument("--skip-przesylki", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    s = new_session()
    if not ensure_login(s):
        print("[ERR] LOGOWANIE NIEUDANE (OCR captchy nie trafił po wielu próbach). "
              "Re-seed sesji ręcznie albo popraw OCR.", file=sys.stderr)
        sys.exit(2)
    if not a.skip_przesylki:
        p, n = fetch_shipments(s, a.od, a.do, a.out)
        print(f"[ok] przesyłki: {n} rekordów -> {p}")
    if not a.skip_prowizje:
        p, summ, n = fetch_prowizje(s, a.od, a.do, a.out)
        print(f"[ok] prowizje do wypłaty {a.od}..{a.do}: do_wyplaty={summ.get('suma_do_wyplaty')} zł, "
              f"{n} wpisów -> {p}")


if __name__ == "__main__":
    main()

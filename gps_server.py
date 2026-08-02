"""gps_server — minimal PWA GPS receiver dla kurierów (F1.5, stdlib only).

Architektura (po flagach A):
- Pisze do /root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json
- Osobny plik od legacy `gps_positions.json` (pisany przez /root/gps_server.py)
- Format: {courier_id: {lat, lon, accuracy, timestamp_utc, source, name}}
- courier_resolver._load_gps_positions() merge'uje PWA (primary) + legacy (fallback)

Endpoints:
    GET  /         — HTML PWA page (PIN input + watchPosition)
    POST /gps      — JSON {pin, lat, lon, accuracy} → write
    GET  /ping     — health check

Auth:
    PIN z kurier_piny.json (4-cyfra → imię) → kurier_ids.json (imię → courier_id)
    Brak PIN / brak name w lookup → 401.

Port: 8765 (dev; production za nginx reverse proxy z HTTPS).

Uruchamianie:
    python3 -m dispatch_v2.gps_server
    # albo systemd: dispatch-gps.service
"""
import json
import socketserver
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Optional, Tuple

from dispatch_v2 import gps_pwa_store
from dispatch_v2 import gps_rate_limit
from dispatch_v2.common import coords_in_bialystok_bbox, decision_flag, setup_logger
from dispatch_v2.identity import pin_auth


PORT = 8766  # 8765 zajęty przez legacy /root/gps_server.py (Traccar receiver)
HOST = "0.0.0.0"
KURIER_PINY_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
# A-6: addytywny magazyn KDF (per-user-salt). Legacy kurier_piny.json NIGDY
# nie nadpisywany — patrz identity/pin_auth (dual-read, flaga ENABLE_PIN_KDF).
KURIER_PINY_KDF_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny_kdf.json"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
GPS_PWA_PATH = "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"

_log = setup_logger("gps_server", "/root/.openclaw/workspace/scripts/logs/gps_server.log")


# ---- storage ----

def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"load {path}: {e}")
        return {}


# ---- auth ----

def _resolve_pin(pin: str) -> Tuple[Optional[str], Optional[str]]:
    """PIN → (courier_id, name). (None, None) dla nieznanego PIN-u.

    A-6 (2026-08-02): krok PIN→name deleguje do `pin_auth.resolve_pin` (dual-read
    KDF + sól per-user, lazy re-hash). Flaga `ENABLE_PIN_KDF` OFF = bajt-parytet z
    legacy `piny.get(pin)` (zero dostępu do magazynu KDF). Krok name→cid bez zmian.
    """
    name = pin_auth.resolve_pin(
        pin, piny_path=KURIER_PINY_PATH, kdf_path=KURIER_PINY_KDF_PATH)
    if not name:
        return None, None
    ids = _load_json(KURIER_IDS_PATH)
    cid = ids.get(name)
    if cid is None:
        return None, name
    return str(cid), name


def _client_ip(handler) -> str:
    """Best-effort IP klienta dla per-IP rate-limit. Za nginx `client_address` to
    loopback proxy → użyj leftmost X-Forwarded-For (per-klient). Bez proxy → peer.
    XFF jest spoofowalny, więc per-IP to bezpiecznik best-effort; per-KURIER
    (post-auth, keyed on cid) jest kontrolą autorytatywną i niewrażliwą na spoofing."""
    try:
        peer = handler.client_address[0]
    except Exception:
        return "unknown"
    try:
        if peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            xff = handler.headers.get("X-Forwarded-For")
            if xff:
                first = xff.split(",")[0].strip()
                if first:
                    return first
    except Exception:
        pass
    return peer or "unknown"


# ---- write gps record ----

def _ingest_coords_ok(lat: float, lon: float) -> bool:
    """L2.1 sentinel-ingest (2026-07-01, K5a): (0,0)/NaN/poza-bbox metropolii
    NIE wchodzi do store'a jako „dana" — downstream haversine raisuje na
    sentinelu i V328 wyrzuca ZAJĘTEGO kuriera z puli (8-28 ofiar/dzień).
    Odrzucona pozycja ⇒ kurier = no_gps ⇒ polityka równego traktowania
    (Adrian 29.06), NIE zatruta geometria. Range-check w handlerze zostaje
    jako legacy minimum; flaga OFF = pass-through (legacy)."""
    if not decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        return True
    return coords_in_bialystok_bbox((lat, lon))


def _update_gps(courier_id: str, name: str, lat: float, lon: float,
                accuracy: float) -> None:
    # A-1 (2026-08-02): zapis przez KANONICZNY writer (gps_pwa_store) — jeden
    # owner kontraktu wspólny z courier-api. Flaga ENABLE_GPS_MERGE_LOCK OFF =
    # bajt-parytet z legacy (threading.Lock + load→set→atomic write); ON =
    # dedykowany lockfile cross-proces + merge-by-ts (koniec lost-update).
    record = {
        "lat": round(float(lat), 7),
        "lon": round(float(lon), 7),
        "accuracy": round(float(accuracy), 2),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "pwa",
        "name": name,
    }
    gps_pwa_store.write_pwa_position(GPS_PWA_PATH, courier_id, record)


# ---- HTML ----

INDEX_HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Ziomek GPS</title>
<meta name="theme-color" content="#0b5">
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#111;color:#eee;margin:0;padding:20px;max-width:480px;margin:auto}
h1{font-size:20px;color:#0b5;margin:0 0 20px}
input,button{font-size:22px;padding:14px;width:100%;box-sizing:border-box;border-radius:8px;border:1px solid #444;background:#222;color:#eee;margin-bottom:12px}
button{background:#0b5;border:none;font-weight:bold;color:#fff}
button:disabled{background:#555}
button.stop{background:#c33}
.status{padding:14px;border-radius:8px;background:#222;margin:12px 0;font-size:16px;line-height:1.5}
.ok{border-left:4px solid #0b5}
.err{border-left:4px solid #c33}
.meta{font-size:13px;color:#888;margin-top:4px}
</style>
</head>
<body>
<h1>🟢 Ziomek GPS</h1>

<div id="pin-box">
  <input id="pin" type="number" placeholder="Wpisz PIN (4 cyfry)" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="off">
  <button id="start">Start GPS</button>
</div>

<div id="active-box" style="display:none">
  <div id="status" class="status"></div>
  <button id="stop" class="stop">Stop</button>
</div>

<script>
const STATUS = document.getElementById('status');
const PIN_IN = document.getElementById('pin');
const BTN_START = document.getElementById('start');
const BTN_STOP = document.getElementById('stop');
const PIN_BOX = document.getElementById('pin-box');
const ACTIVE_BOX = document.getElementById('active-box');

let watchId = null;
let intervalId = null;
let lastPos = null;
let wakeLock = null;
let currentPin = null;
let currentName = null;
let sentCount = 0;
let lastSentTs = null;

const saved = localStorage.getItem('ziomek_pin');
if (saved) PIN_IN.value = saved;

function show(msg, cls='ok'){
  STATUS.className = 'status ' + cls;
  STATUS.innerHTML = msg;
}

async function acquireWakeLock(){
  try {
    if ('wakeLock' in navigator) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    }
  } catch(e) { /* silent */ }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && watchId !== null && !wakeLock) {
    acquireWakeLock();
  }
});

async function sendGps(){
  if (!lastPos || !currentPin) return;
  try {
    const r = await fetch('/gps', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        pin: currentPin,
        lat: lastPos.coords.latitude,
        lon: lastPos.coords.longitude,
        accuracy: lastPos.coords.accuracy,
      }),
    });
    const j = await r.json();
    if (r.ok && j.ok){
      sentCount++;
      lastSentTs = new Date().toLocaleTimeString();
      currentName = currentName || j.name;
      show(`✅ GPS aktywny — <b>${currentName}</b>
<div class="meta">Wysłano: ${sentCount}× | Ostatnio: ${lastSentTs}
<br>Lat: ${lastPos.coords.latitude.toFixed(5)} Lon: ${lastPos.coords.longitude.toFixed(5)}
<br>Dokładność: ±${Math.round(lastPos.coords.accuracy)}m</div>`, 'ok');
    } else {
      show(`❌ Server error: ${j.error || r.status}`, 'err');
    }
  } catch(e){
    show(`❌ Brak połączenia: ${e.message}`, 'err');
  }
}

BTN_START.onclick = async () => {
  const pin = PIN_IN.value.trim();
  if (!/^\\d{4}$/.test(pin)) { show('❌ PIN = 4 cyfry', 'err'); return; }

  currentPin = pin;
  localStorage.setItem('ziomek_pin', pin);

  if (!navigator.geolocation) {
    show('❌ Geolocation niedostępne', 'err');
    return;
  }

  PIN_BOX.style.display = 'none';
  ACTIVE_BOX.style.display = 'block';
  show('⏳ Czekam na GPS...', 'ok');

  await acquireWakeLock();

  watchId = navigator.geolocation.watchPosition(
    pos => { lastPos = pos; if (sentCount === 0) sendGps(); },
    err => { show(`❌ GPS error: ${err.message}`, 'err'); },
    { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 }
  );

  intervalId = setInterval(sendGps, 30000);
};

BTN_STOP.onclick = () => {
  if (watchId !== null) { navigator.geolocation.clearWatch(watchId); watchId = null; }
  if (intervalId !== null) { clearInterval(intervalId); intervalId = null; }
  if (wakeLock) { wakeLock.release(); wakeLock = null; }
  sentCount = 0;
  PIN_BOX.style.display = 'block';
  ACTIVE_BOX.style.display = 'none';
};
</script>
</body>
</html>
"""


# ---- HTTP handler ----

class GpsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        _log.info(f"{self.client_address[0]} {fmt % args}")

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html: str):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            self._html(200, INDEX_HTML)
        elif path == "/ping":
            self._json(200, {"ok": True, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/gps":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024:
                self._json(400, {"ok": False, "error": "invalid content-length"})
                return
            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"ok": False, "error": f"bad body: {type(e).__name__}"})
            return

        # A-6: per-IP rate-limit PRZED auth (dławi flood z jednego źródła niezależnie
        # od ważności PIN-u). OFF (default) → None = zero dławienia (bajt-parytet).
        client_ip = _client_ip(self)
        if gps_rate_limit.check(None, client_ip) is not None:
            _log.warning(f"RATE_LIMIT ip={client_ip} scope=ip")
            self._json(429, {"ok": False, "error": "rate limited (ip)"})
            return

        pin = str(body.get("pin") or "").strip()
        lat = body.get("lat")
        lon = body.get("lon")
        accuracy = body.get("accuracy")

        if not pin or lat is None or lon is None or accuracy is None:
            self._json(400, {"ok": False, "error": "missing fields"})
            return
        try:
            lat = float(lat); lon = float(lon); accuracy = float(accuracy)
        except (TypeError, ValueError):
            self._json(400, {"ok": False, "error": "invalid number"})
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._json(400, {"ok": False, "error": "coords out of range"})
            return
        if not _ingest_coords_ok(lat, lon):
            _log.warning(
                f"COORD_INGEST_GUARD gps reject lat={lat!r} lon={lon!r} "
                f"(pin=****{pin[-1:]})"
            )
            self._json(400, {"ok": False, "error": "coords rejected (sentinel/out-of-area)"})
            return

        cid, name = _resolve_pin(pin)
        if cid is None:
            _log.warning(f"pin auth fail (pin=****{pin[-1:]}, name={name})")
            self._json(401, {"ok": False, "error": "bad pin"})
            return

        # A-6: per-KURIER rate-limit PO auth (dławi flood pozycji zalogowanego
        # kuriera). OFF (default) → None = zero dławienia (bajt-parytet).
        if gps_rate_limit.check(cid, None) is not None:
            _log.warning(f"RATE_LIMIT cid={cid} scope=courier")
            self._json(429, {"ok": False, "error": "rate limited (courier)"})
            return

        try:
            _update_gps(cid, name, lat, lon, accuracy)
        except Exception as e:
            _log.exception("update_gps fail")
            self._json(500, {"ok": False, "error": f"write fail: {type(e).__name__}"})
            return

        _log.info(f"GPS {cid}/{name} lat={lat:.5f} lon={lon:.5f} acc={accuracy:.0f}m")
        self._json(200, {"ok": True, "courier_id": cid, "name": name})


class ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    _log.info(f"gps_server START port={PORT}")
    with ThreadingServer((HOST, PORT), GpsHandler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            _log.info("gps_server STOP (KeyboardInterrupt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

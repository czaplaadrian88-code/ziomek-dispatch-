"""OVH SMS provider (MP-#9, 2026-05-08).

OVH SMS API client — Polish carrier reliability ~0.04 PLN/SMS.

Configuration (env, all required for is_configured()):
  OVH_SMS_ENDPOINT      OVH region endpoint (e.g. 'ovh-eu')
  OVH_SMS_APP_KEY       Application key
  OVH_SMS_APP_SECRET    Application secret
  OVH_SMS_CONSUMER_KEY  Consumer key (per-user auth)
  OVH_SMS_SERVICE_NAME  SMS service name (e.g. 'sms-xx12345-1')
  OVH_SMS_SENDER        Sender label (np. "Ziomek")

Reference: https://docs.ovh.com/gb/en/sms/sms_quickstart/

Authentication: OVH API uses HMAC-SHA1 signature z timestamp + nonce. Implementacja
inline (no external deps; pure stdlib urllib + hashlib).

Setup steps for Adrian (one-time, ~15 min):
  1. eu.api.ovh.com → Create OVH account if not present
  2. eu.api.ovh.com/createApp → generate APP_KEY + APP_SECRET
  3. Use createApp output to authenticate consumer (one OVH redirect):
     POST https://eu.api.ovh.com/1.0/auth/credential
     body: {"accessRules":[{"method":"POST","path":"/sms/*"}], "redirection":"https://ovh.com"}
     → returns CONSUMER_KEY (validate via redirect link)
  4. Buy SMS pack: ovh.com/manager → SMS → choose service (sms-xx12345-1)
  5. Set sender: SMS service config → senders → add "Ziomek" → wait validation
  6. Add to /root/.openclaw/workspace/.env:
       SMS_PROVIDER=ovh
       OVH_SMS_ENDPOINT=ovh-eu
       OVH_SMS_APP_KEY=...
       OVH_SMS_APP_SECRET=...
       OVH_SMS_CONSUMER_KEY=...
       OVH_SMS_SERVICE_NAME=sms-xx12345-1
       OVH_SMS_SENDER=Ziomek
       SMS_TARGET_NUMBER=+48...
  7. systemctl enable --now dispatch-tg-heartbeat.timer

Test smoke (after setup):
  python3 -m dispatch_v2.sms.ovh test "Smoke test from Ziomek MP-#9"
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from dispatch_v2.sms.provider import SMSDeliveryError, SMSProvider

_log = logging.getLogger(__name__)

# Endpoint URL mapping per OVH region
_OVH_ENDPOINTS = {
    "ovh-eu": "https://eu.api.ovh.com/1.0",
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
    "kimsufi-eu": "https://eu.api.kimsufi.com/1.0",
    "soyoustart-eu": "https://eu.api.soyoustart.com/1.0",
}

DEFAULT_TIMEOUT_SEC = 10.0


class OVHSMSProvider(SMSProvider):
    """OVH SMS API provider — production-ready, requires Adrian config."""

    name = "ovh"

    def __init__(self):
        self.endpoint = os.environ.get("OVH_SMS_ENDPOINT", "ovh-eu")
        self.app_key = os.environ.get("OVH_SMS_APP_KEY", "")
        self.app_secret = os.environ.get("OVH_SMS_APP_SECRET", "")
        self.consumer_key = os.environ.get("OVH_SMS_CONSUMER_KEY", "")
        self.service_name = os.environ.get("OVH_SMS_SERVICE_NAME", "")
        self.sender = os.environ.get("OVH_SMS_SENDER", "Ziomek")
        self.base_url = _OVH_ENDPOINTS.get(self.endpoint, _OVH_ENDPOINTS["ovh-eu"])

    def is_configured(self) -> bool:
        return all([
            self.app_key,
            self.app_secret,
            self.consumer_key,
            self.service_name,
        ])

    def _missing_creds_summary(self) -> str:
        missing = []
        for var in ("OVH_SMS_APP_KEY", "OVH_SMS_APP_SECRET", "OVH_SMS_CONSUMER_KEY", "OVH_SMS_SERVICE_NAME"):
            if not os.environ.get(var):
                missing.append(var)
        return ", ".join(missing) if missing else "(none — all set)"

    def _sign(self, method: str, url: str, body: str, timestamp: str) -> str:
        """OVH HMAC-SHA1 signature: '$1$' + sha1(secret + '+' + ck + '+' + method + '+' + url + '+' + body + '+' + timestamp)."""
        msg = "+".join([self.app_secret, self.consumer_key, method, url, body, timestamp])
        digest = hashlib.sha1(msg.encode("utf-8")).hexdigest()
        return f"$1${digest}"

    def _api_post(self, path: str, payload: dict, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict:
        """Authenticated POST to OVH API. Raises SMSDeliveryError on fail."""
        url = self.base_url + path
        body = json.dumps(payload)
        timestamp = str(int(time.time()))
        signature = self._sign("POST", url, body, timestamp)

        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Ovh-Application": self.app_key,
                "X-Ovh-Consumer": self.consumer_key,
                "X-Ovh-Timestamp": timestamp,
                "X-Ovh-Signature": signature,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp_body = r.read().decode("utf-8")
                return json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise SMSDeliveryError(
                f"OVH HTTP {e.code}: {err_body[:200]}",
                provider="ovh",
                status_code=e.code,
                body=err_body,
            ) from e
        except urllib.error.URLError as e:
            raise SMSDeliveryError(
                f"OVH URL error: {e.reason}",
                provider="ovh",
            ) from e
        except Exception as e:
            raise SMSDeliveryError(
                f"OVH unexpected error: {type(e).__name__}: {e}",
                provider="ovh",
            ) from e

    def send(self, message: str, recipient: str) -> bool:
        """Send SMS via OVH API.

        Returns True on accepted-by-OVH (HTTP 200, totalCreditsRemoved >= 0).
        Raises SMSDeliveryError on misconfiguration / unrecoverable error.
        """
        if not self.is_configured():
            raise SMSDeliveryError(
                f"OVH provider missing creds: {self._missing_creds_summary()}",
                provider="ovh",
            )

        path = f"/sms/{self.service_name}/jobs"
        payload = {
            "message": message,
            "receivers": [recipient],
            "sender": self.sender,
            "noStopClause": True,  # transactional alert (NIE marketing — STOP clause not needed)
            "priority": "high",
            "validityPeriod": 60,  # minutes; jeśli SMS nie dotrze w 60min → drop (alert nieaktualny)
        }
        resp = self._api_post(path, payload)
        valid_count = resp.get("validReceivers", []) or []
        invalid_count = resp.get("invalidReceivers", []) or []
        credits_used = resp.get("totalCreditsRemoved", 0)
        _log.info(
            f"[OVH SMS] to={recipient} valid={len(valid_count)} invalid={len(invalid_count)} "
            f"credits_used={credits_used} ids={resp.get('ids', [])}"
        )
        if invalid_count or not valid_count:
            _log.warning(f"OVH SMS no valid receivers: {invalid_count!r}; resp={resp!r}")
            return False
        return True


# CLI smoke entry point: `python3 -m dispatch_v2.sms.ovh test "message"`
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) >= 3 and sys.argv[1] == "test":
        msg = sys.argv[2]
        target = os.environ.get("SMS_TARGET_NUMBER", "")
        if not target:
            print("ERROR: SMS_TARGET_NUMBER env not set", file=sys.stderr)
            sys.exit(2)
        provider = OVHSMSProvider()
        if not provider.is_configured():
            print(f"ERROR: OVH not configured. Missing: {provider._missing_creds_summary()}", file=sys.stderr)
            sys.exit(2)
        try:
            ok = provider.send(msg, target)
            print(f"send result: {ok}")
            sys.exit(0 if ok else 1)
        except SMSDeliveryError as e:
            print(f"send failed: {e} (provider={e.provider} status={e.status_code})", file=sys.stderr)
            sys.exit(1)
    else:
        print('Usage: python3 -m dispatch_v2.sms.ovh test "message"', file=sys.stderr)
        sys.exit(2)

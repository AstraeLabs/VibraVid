# 09.10.26

import argparse
import base64
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pywidevine.cdm import Cdm as WvCdm
from pywidevine.device import Device as WvDevice
from pywidevine.pssh import PSSH as WvPSSH
from pyplayready.cdm import Cdm as PrCdm
from pyplayready.device import Device as PrDevice
from pyplayready.system.pssh import PSSH as PrPSSH

from VibraVid.setup import get_prd_path, get_wvd_path

STATE = {"sessions": {}, "cache": {}, "calls": []}
API_KEY = "testkey"


def _is_pr(scheme: str) -> bool:
    return scheme.upper().startswith("SL")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("decrypt-labs-api-key") != API_KEY:
            return self._reply({"message": "invalid api key"}, 401)
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        STATE["calls"].append((self.path, {k: v for k, v in data.items() if k in ("scheme", "get_cached_keys_if_exists")}))
        try:
            if self.path == "/get-request":
                return self._get_request(data)
            if self.path == "/decrypt-response":
                return self._decrypt_response(data)
        except Exception as exc:
            return self._reply({"message": "failure", "Error": repr(exc)}, 500)
        self._reply({"message": "not found"}, 404)

    def _get_request(self, data: dict):
        scheme, init_data = data["scheme"], data["init_data"]
        if _is_pr(scheme):
            pssh = PrPSSH(init_data)
            cache_key = init_data
        else:
            pssh = WvPSSH(init_data)
            cache_key = init_data

        if data.get("get_cached_keys_if_exists") and cache_key in STATE["cache"]:
            return self._reply({"message": "success", "message_type": "cached-keys", "cached_keys": STATE["cache"][cache_key]})

        if _is_pr(scheme):
            cdm = PrCdm.from_device(PrDevice.load(get_prd_path()))
            session = cdm.open()
            challenge = cdm.get_license_challenge(session, pssh.wrm_headers[0])
            challenge = challenge.encode() if isinstance(challenge, str) else challenge
        else:
            cdm = WvCdm.from_device(WvDevice.load(get_wvd_path()))
            session = cdm.open()
            if data.get("service_certificate"):
                cdm.set_service_certificate(session, data["service_certificate"])
            challenge = cdm.get_license_challenge(session, pssh)

        sid = secrets.token_hex(8)
        STATE["sessions"][sid] = (cdm, session, cache_key, _is_pr(scheme))
        self._reply({"message": "success", "message_type": "license-request", "challenge": base64.b64encode(challenge).decode(), "session_id": sid})

    def _decrypt_response(self, data: dict):
        cdm, session, cache_key, is_pr = STATE["sessions"].pop(data["session_id"])
        license_bytes = base64.b64decode(data["license_response"])
        if is_pr:
            cdm.parse_license(session, license_bytes.decode("utf-8"))
            keys = [(k.key_id.hex, k.key.hex()) for k in cdm.get_keys(session)]
        else:
            cdm.parse_license(session, license_bytes)
            keys = [(k.kid.hex, k.key.hex()) for k in cdm.get_keys(session) if k.type == "CONTENT"]
        STATE["cache"][cache_key] = [{"kid": kid, "key": key} for kid, key in keys]
        self._reply({"message": "success", "keys": "\n".join(f"--key {kid}:{key}" for kid, key in keys)})

    def do_GET(self):
        self._reply({"calls": STATE["calls"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18113)
    parser.add_argument("--key", default="testkey")
    args = parser.parse_args()
    API_KEY = args.key
    print(f"Decrypt Labs emulator on 127.0.0.1:{args.port}", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()

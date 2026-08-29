#!/usr/bin/env python3
"""MQTT -> runtime overrides bridge (issue #14): the inbound half.

Subscribes to <prefix>/config/set and applies each message through the
same function the CLI uses (bot/overrides.py::set_override), so MQTT can
never do anything override.py can't: allowlisted keys only, validated,
expiring at the close. After every change it republishes the account's
effective config (retained) so Home Assistant always sees what the bot is
actually running. Long-running; run it under systemd or in a container.

Payload (JSON):  {"account": "test", "key": "temperature", "value": 0.5, "until": "<ISO, optional>"}
Clear a key:     {"account": "test", "key": "temperature", "value": null}
Errors go to     <prefix>/<account>/config/error  (not retained)

Usage: mqtt_bridge.py [--config config.yaml] [--prefix alpaca-hackathon]
Env:   MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
"""

import argparse
import json
import os
import sys
from datetime import datetime

from bot import journal, mqtt, overrides
from bot.config import config_provenance, load_config
from bot.credentials import validate_account
from bot.risk import EASTERN

DEFAULT_ACCOUNT = "test"


def apply_message(payload: dict, config_path: str | None) -> tuple[str, dict]:
    """Validate + apply one set/clear message. Returns (account, result)
    where result is {"ok": True, "effective": {...}} or {"ok": False, "error": ...}.
    Pure enough to unit-test: file I/O goes through bot/overrides only."""
    account = str(payload.get("account") or DEFAULT_ACCOUNT)
    try:
        validate_account(account)
        key = payload.get("key")
        if not key:
            raise ValueError("missing 'key'")
        journal.use_account(account)
        overrides.use_account(account)
        now = datetime.now(EASTERN)
        if payload.get("value") is None:
            existed = overrides.clear_override(str(key))
            journal.log("override_cleared", key=key, existed=existed, set_by="mqtt")
        else:
            until = payload.get("until")
            until_dt = datetime.fromisoformat(str(until)) if until else None
            if until_dt is not None and until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=EASTERN)
            entry = overrides.set_override(str(key), payload["value"], until=until_dt, set_by="mqtt", now=now)
            journal.log("override_set", key=key, value=entry["value"], until=entry["until"], set_by="mqtt")
        effective = config_provenance(load_config(config_path, now=now))
        return account, {"ok": True, "effective": effective}
    except (ValueError, OSError) as exc:
        return account, {"ok": False, "error": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--prefix", default=None, help="topic prefix (default from config.mqtt.topic_prefix)")
    args = ap.parse_args()

    import paho.mqtt.client as paho  # optional dependency, required for the bridge itself

    config = load_config(args.config)
    prefix = args.prefix or (config.get("mqtt") or {}).get("topic_prefix") or mqtt.DEFAULT_PREFIX
    host = os.environ.get("MQTT_HOST") or (config.get("mqtt") or {}).get("host")
    if not host:
        print("MQTT_HOST (or config.mqtt.host) is required", file=sys.stderr)
        return 2
    port = int(os.environ.get("MQTT_PORT") or (config.get("mqtt") or {}).get("port") or 1883)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            client.publish(f"{prefix}/{DEFAULT_ACCOUNT}/config/error", json.dumps({"error": f"bad JSON: {exc}"}))
            return
        account, result = apply_message(payload if isinstance(payload, dict) else {}, args.config)
        if result["ok"]:
            client.publish(f"{prefix}/{account}/config/effective", json.dumps(result["effective"], default=str), retain=True)
            print(f"[bridge] {account}: applied {payload.get('key')}", flush=True)
        else:
            client.publish(f"{prefix}/{account}/config/error", json.dumps({"error": result["error"], "request": payload}))
            print(f"[bridge] {account}: refused {payload.get('key')}: {result['error']}", flush=True)

    client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="alpaca-hackathon-bridge")
    if os.environ.get("MQTT_USERNAME"):
        client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.subscribe(f"{prefix}/config/set", qos=1)
    print(f"[bridge] listening on {host}:{port} {prefix}/config/set", flush=True)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

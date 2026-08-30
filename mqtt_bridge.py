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

Also subscribes to <prefix>/<account>/command/halt - the kill switch
(issue #14's "two-way control" stretch goal). Payload must be exactly
"HALT" (matches the HA button's payload_press). Reuses flatten.py's own
run() as-is: flattens *only* that account's positions, but the HALT file
it writes (bot/risk.py::RiskManager.manual_halt_file) is intentionally
shared, so it halts every account's next cycle, not just this one -
resuming (deleting logs/HALT) stays a deliberate CLI-only step.
Errors go to <prefix>/<account>/command/error (not retained).

On startup, publishes (retained) MQTT discovery for a kill-switch button
and the runtime-tunable knobs (bot/overrides.py::OVERRIDABLE_KEYS, minus
strategy_notes - that one's prose, use override.py set strategy_notes
@file) for every account in KNOWN_ACCOUNTS, plus primes each account's
config/effective topic so those controls show real values immediately
rather than starting blank.

Usage: mqtt_bridge.py [--config config.yaml] [--prefix alpaca-hackathon]
Env:   MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import flatten
from bot import journal, mqtt, overrides
from bot.config import config_provenance, load_config
from bot.credentials import validate_account
from bot.risk import EASTERN

DEFAULT_ACCOUNT = "test"
REPO_ROOT = Path(__file__).resolve().parent

# Which accounts to publish discovery/kill-switch entities for, and which
# config file each one actually runs with (matches /etc/cron.d/alpaca-hackathon
# and the ansible ha-dashboard role's ha_accounts default). A message for an
# account not listed here still works for config/set (apply_message resolves
# its config from the bridge's own --config); it just won't have dashboard
# controls until added to both lists.
KNOWN_ACCOUNTS = ("official", "test")
ACCOUNT_CONFIG_PATH: dict[str, str] = {"test": str(REPO_ROOT / "config-test.yaml")}

# Numeric knobs to expose as HA `number` entities: bot/overrides.py's
# OVERRIDABLE_KEYS minus model (a `text` entity, not numeric) and
# strategy_notes (prose - stays override.py/PR-only, HA's MQTT text entity
# doesn't fit a paragraph). min/max/step mirror OVERRIDABLE_KEYS' own
# validators for UI honesty; the bridge still re-validates server-side.
NUMBER_KNOBS = {
    "temperature": {"min": 0, "max": 2, "step": 0.1, "name": "Temperature", "icon": "mdi:thermometer"},
    "max_tokens": {"min": 50, "max": 8000, "step": 50, "name": "Max tokens", "icon": "mdi:text-long"},
    "research_contracts_per_underlying": {
        "min": 1, "max": 60, "step": 1, "name": "Research contracts/underlying", "icon": "mdi:magnify",
    },
    "option_strike_band_pct": {
        "min": 0.01, "max": 0.5, "step": 0.01, "name": "Strike band %", "icon": "mdi:swap-vertical-bold",
    },
    "stop_loss_pct": {"min": 1, "max": 100, "step": 1, "name": "Stop loss %", "icon": "mdi:trending-down"},
    "take_profit_pct": {"min": 1, "max": 1000, "step": 1, "name": "Take profit %", "icon": "mdi:trending-up"},
    "eod_close_dte": {"min": 0, "max": 45, "step": 1, "name": "EOD close DTE", "icon": "mdi:calendar-clock"},
}


def config_path_for(account: str, explicit: str | None) -> str | None:
    """The config file `account` actually runs with, unless the bridge was
    launched with an explicit --config override (then that wins for every
    account - handy for local/single-config testing)."""
    return explicit or ACCOUNT_CONFIG_PATH.get(account)


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
        effective = config_provenance(load_config(config_path_for(account, config_path), now=now))
        return account, {"ok": True, "effective": effective}
    except (ValueError, OSError) as exc:
        return account, {"ok": False, "error": str(exc)}


def parse_halt_topic(topic: str) -> str | None:
    """"<prefix>/<account>/command/halt" -> account, else None."""
    parts = topic.split("/")
    if len(parts) >= 4 and parts[-2:] == ["command", "halt"]:
        return parts[-3]
    return None


async def run_halt(account: str, config_path: str | None) -> None:
    """The kill switch: flatten.py's own run(), reused as-is so journal +
    MQTT event publishing (event/flatten, event/manual_halt) and the
    official-account trading-window guard all come for free."""
    ns = argparse.Namespace(
        halt=True,
        expiring_only=False,
        account=account,
        config=config_path_for(account, config_path),
        verify_timeout=30.0,
    )
    await flatten.run(ns)


def _device(account: str) -> dict:
    return {
        "identifiers": [f"alpaca_hackathon_{account}"],
        "name": f"AI Day Trader ({account})",
        "manufacturer": "alpaca-hackathon",
        "model": "Long Premium, Short Leash",
    }


def discovery_payloads(prefix: str) -> list[tuple[str, dict]]:
    """(discovery_topic, payload) pairs for every kill-switch button and
    knob entity, for every account in KNOWN_ACCOUNTS. Pure/testable;
    publishing (retained) is the caller's job."""
    out: list[tuple[str, dict]] = []
    for account in KNOWN_ACCOUNTS:
        device = _device(account)
        effective_topic = f"{prefix}/{account}/config/effective"

        # has_entity_name: False on every entity below - HA defaults it true,
        # which (whenever a device block is present) derives entity_id from
        # the device+entity NAME instead of honoring object_id, confirmed
        # live to produce unpredictable/truncated ids (e.g.
        # number.ai_day_trader_official_research_contracts_underlying
        # instead of number.alpaca_official_research_contracts_per_underlying).
        # False restores entity_id = domain.object_id, deterministically.
        uid = f"alpaca_{account}_kill_switch"
        out.append((f"homeassistant/button/{uid}/config", {
            "unique_id": uid, "object_id": uid, "has_entity_name": False,
            "name": "Kill switch (flatten + halt)",
            "icon": "mdi:stop-octagon", "device": device,
            "command_topic": f"{prefix}/{account}/command/halt", "payload_press": "HALT",
        }))

        uid = f"alpaca_{account}_model"
        out.append((f"homeassistant/text/{uid}/config", {
            "unique_id": uid, "object_id": uid, "has_entity_name": False,
            "name": "Model", "icon": "mdi:robot-outline",
            "device": device, "command_topic": f"{prefix}/config/set",
            "command_template": json.dumps({"account": account, "key": "model", "value": "{{ value }}"}),
            "state_topic": effective_topic, "value_template": "{{ value_json.model }}", "mode": "text",
        }))

        for key, attrs in NUMBER_KNOBS.items():
            uid = f"alpaca_{account}_{key}"
            out.append((f"homeassistant/number/{uid}/config", {
                "unique_id": uid, "object_id": uid, "has_entity_name": False,
                "name": attrs["name"], "icon": attrs["icon"],
                "device": device, "command_topic": f"{prefix}/config/set",
                "command_template": json.dumps({"account": account, "key": key, "value": "{{ value }}"}),
                "state_topic": effective_topic, "value_template": f"{{{{ value_json.{key} }}}}",
                "min": attrs["min"], "max": attrs["max"], "step": attrs["step"], "mode": "box",
            }))
    return out


def publish_discovery(client, prefix: str) -> None:
    for topic, payload in discovery_payloads(prefix):
        client.publish(topic, json.dumps(payload), retain=True)


def publish_effective(client, prefix: str, config_path: str | None) -> None:
    """Prime <prefix>/<account>/config/effective for every known account so
    the knob entities show real values immediately, not blank until the
    first change. Best-effort: a missing/bad config for one account must
    not block the others."""
    now = datetime.now(EASTERN)
    for account in KNOWN_ACCOUNTS:
        try:
            journal.use_account(account)
            overrides.use_account(account)
            effective = config_provenance(load_config(config_path_for(account, config_path), now=now))
            client.publish(f"{prefix}/{account}/config/effective", json.dumps(effective, default=str), retain=True)
        except OSError as exc:
            print(f"[bridge] {account}: could not prime effective config: {exc}", file=sys.stderr, flush=True)


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
        halt_account = parse_halt_topic(msg.topic)
        if halt_account is not None:
            if msg.payload.decode(errors="replace").strip() != "HALT":
                return
            try:
                validate_account(halt_account)
                asyncio.run(run_halt(halt_account, args.config))
                print(f"[bridge] {halt_account}: kill switch pressed - flattened + halted", flush=True)
            except Exception as exc:  # noqa: BLE001 - a bad/failed command must not crash the bridge
                client.publish(f"{prefix}/{halt_account}/command/error", json.dumps({"error": str(exc)}))
                print(f"[bridge] {halt_account}: kill switch failed: {exc}", flush=True)
            return

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
    client.subscribe(f"{prefix}/+/command/halt", qos=1)
    publish_discovery(client, prefix)
    publish_effective(client, prefix, args.config)
    print(f"[bridge] listening on {host}:{port} {prefix}/config/set and {prefix}/+/command/halt", flush=True)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

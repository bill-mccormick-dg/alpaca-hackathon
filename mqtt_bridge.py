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
(issue #14's "two-way control" stretch goal), a two-way HA switch:

  HALT    flatten.py's own run(), reused as-is: flattens that account's
          positions and halts THAT ACCOUNT ONLY. The break-glass "halt
          every account" (logs/HALT) is deliberately NOT reachable from
          here - it stays CLI-only (flatten.py --halt --all-accounts), so
          no dashboard tap can stop the judged account by accident during
          the scoring window.
  RESUME  clears that account's own manual halt. Narrow on purpose: it
          refuses to clear a global or daily-loss halt, which still take
          the deliberate step on the host they always did.

The switch's state comes from <prefix>/<account>/state/halt, which this
bridge derives from the halt FILES and republishes after every command
and every HALT_POLL_SEC. That matters: a halted account runs no cycles,
so the event-driven state bot/mqtt.py publishes would freeze at its last
value until a human both cleared the halt AND a cycle happened to run.
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
import threading
import time
from datetime import datetime
from pathlib import Path

import flatten
from bot import journal, mqtt, overrides
from bot import risk as risk_module
from bot.config import config_provenance, load_config
from bot.credentials import load_mqtt_env, validate_account
from bot.featherless import DEFAULT_MODEL
from bot.risk import EASTERN

# How often to republish each account's halt state. The state is derived
# from the halt files, so this also picks up changes made outside the
# bridge (a `rm logs/HALT_manual_test` on the host, a daily-loss halt
# written mid-session by run_cycle).
HALT_POLL_SEC = 20

# Accounts with a HALT/RESUME in flight. Their transient state must survive
# the heartbeat, which reads the halt files and would otherwise republish
# the pre-command value mid-operation (flatten writes its halt file last).
_inflight: set[str] = set()
_inflight_lock = threading.Lock()

DEFAULT_ACCOUNT = "test"
REPO_ROOT = Path(__file__).resolve().parent

# Which accounts to publish discovery/kill-switch entities for, and which
# config file each one actually runs with (matches /etc/cron.d/alpaca-hackathon
# and the ansible ha-dashboard role's ha_accounts default). A message for an
# account not listed here still works for config/set (apply_message resolves
# its config from the bridge's own --config); it just won't have dashboard
# controls until added to both lists.
KNOWN_ACCOUNTS = ("official", "test", "mixed")
# Which config file each account actually runs with. An account missing here
# falls back to config.yaml, which is right for `official` and wrong for any
# variant - a knob primed from the wrong file makes the dashboard report a
# model or a stop-loss the account is not using.
ACCOUNT_CONFIG_PATH: dict[str, str] = {
    "test": str(REPO_ROOT / "config-test.yaml"),
    "mixed": str(REPO_ROOT / "config-variants" / "mixed.yaml"),
}

# Numeric knobs to expose as HA `number` entities: bot/overrides.py's
# OVERRIDABLE_KEYS minus model (a `text` entity, not numeric) and
# strategy_notes (prose - stays override.py/PR-only, HA's MQTT text entity
# doesn't fit a paragraph). min/max/step mirror OVERRIDABLE_KEYS' own
# validators for UI honesty; the bridge still re-validates server-side.
# Boolean knobs get a `switch` (the kill switch established the domain):
# payload_on/off carry the full config/set JSON directly - a switch has no
# command_template value to interpolate, its two payloads ARE the values.
# State rides config/effective like the selects and numbers do.
BOOL_KNOBS = {
    # Name must slugify to the key (tests/test_mqtt.py::EntityIdDerivationTest)
    # - "Kalshi prior" would derive a different entity_id than the topic says.
    "predictions_enabled": {"name": "Predictions enabled", "icon": "mdi:crystal-ball"},
}

NUMBER_KNOBS = {
    "temperature": {"min": 0, "max": 2, "step": 0.1, "name": "Temperature", "icon": "mdi:thermometer"},
    "max_tokens": {"min": 50, "max": 8000, "step": 50, "name": "Max tokens", "icon": "mdi:text-long"},
    "research_contracts_per_underlying": {
        "min": 1, "max": 60, "step": 1, "name": "Research contracts per underlying", "icon": "mdi:magnify",
    },
    "option_strike_band_pct": {
        "min": 0.01, "max": 0.5, "step": 0.01, "name": "Option strike band pct", "icon": "mdi:swap-vertical-bold",
    },
    "stop_loss_pct": {"min": 1, "max": 100, "step": 1, "name": "Stop loss pct", "icon": "mdi:trending-down"},
    "take_profit_pct": {"min": 1, "max": 1000, "step": 1, "name": "Take profit pct", "icon": "mdi:trending-up"},
    "eod_close_dte": {"min": 0, "max": 45, "step": 1, "name": "Eod close dte", "icon": "mdi:calendar-clock"},
    "min_hold_minutes": {"min": 0, "max": 390, "step": 5, "name": "Min hold minutes", "icon": "mdi:timer-lock"},
    "early_exit_drawdown_pct": {
        "min": 1, "max": 100, "step": 1, "name": "Early exit drawdown pct", "icon": "mdi:elevator-down",
    },
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
        # Never the global halt: an HA button may only ever stop its OWN
        # account. The break-glass "halt everything" is CLI-only
        # (flatten.py --halt --all-accounts), so no dashboard tap can stop
        # the judged account during the scoring window.
        all_accounts=False,
        expiring_only=False,
        account=account,
        config=config_path_for(account, config_path),
        verify_timeout=30.0,
    )
    await flatten.run(ns)


def risk_for(account: str, config_path: str | None) -> risk_module.RiskManager:
    # logs_dir is passed explicitly rather than relying on RiskManager's
    # default: that default is bound at import time, so it would ignore a
    # relocated bot.risk.LOGS_DIR (which is how the tests keep their hands
    # off the real logs/ directory - a resume there would delete live
    # halt files).
    journal.use_account(account)
    overrides.use_account(account)
    return risk_module.RiskManager(
        load_config(config_path_for(account, config_path)),
        logs_dir=risk_module.LOGS_DIR,
        account=account,
    )


def run_resume(account: str, config_path: str | None) -> str:
    """Clear THIS account's manual halt, so the kill switch is a real
    two-way toggle. Deliberately narrow: it never clears the global halt
    (break-glass, CLI-only to set and to clear) nor the daily-loss halt
    (the guardrail tripped on its own; overriding it from a phone should
    take the same deliberate CLI step it always has). Returns the halt
    state after the attempt."""
    risk = risk_for(account, config_path)
    state = risk.halt_state()
    if state == "manual":
        risk.manual_halt_file().unlink(missing_ok=True)
        journal.log("manual_resume", account=account, set_by="mqtt")
        return risk.halt_state()
    if state != "none":
        raise ValueError(
            f"{account} is under a {state} halt, which the dashboard will not clear - "
            f"see docs/operations.md (global: flatten.py --halt --all-accounts writes it; "
            f"daily_loss: the guardrail tripped). Clear it on the trading host."
        )
    return state


def _device(account: str) -> dict:
    return mqtt.device_block(account)


def model_options(config: dict | None = None) -> list[str]:
    """Model ids to offer in the dashboard's dropdown.

    Sourced from config.yaml's `model_prices`, which already lists every model
    we have costed - so adding one there for cost tracking offers it here too,
    with no second list to forget.

    The running model is always included, even if it was never costed. A
    select whose state is not one of its options is invalid in Home
    Assistant: the dropdown renders blank and shows the current model as an
    illegal value, which reads as "the bot is misconfigured" when the only
    thing wrong is a missing price entry."""
    names = list((config or {}).get("model_prices") or {})
    active = (config or {}).get("model")
    if active and active not in names:
        names.insert(0, active)
    return names or [DEFAULT_MODEL]


def discovery_payloads(prefix: str, config: dict | None = None) -> list[tuple[str, dict]]:
    """(discovery_topic, payload) pairs for every kill-switch button and
    knob entity, for every account in KNOWN_ACCOUNTS. Pure/testable;
    publishing (retained) is the caller's job."""
    options = model_options(config)
    out: list[tuple[str, dict]] = []
    for account in KNOWN_ACCOUNTS:
        device = _device(account)
        effective_topic = f"{prefix}/{account}/config/effective"

        # Every entity's `name` is chosen so it slugifies to its key, and
        # object_id is set to the same id HA derives from device+entity
        # name - see bot/mqtt.py::ENTITY_PREFIX for why that matters (HA
        # ignores object_id, verified live, so the names ARE the contract).
        #
        # A switch, not a button: a button is stateless, so the dashboard
        # could never show whether the account is actually halted. The
        # switch's state comes from the retained halt topic, which the
        # bridge derives from the halt FILES (publish_halt_state) rather
        # than from journal events - a halted account runs no cycles, so an
        # event-driven state would freeze at its last value.
        # value_template maps every halted reason (manual / daily_loss /
        # global) to ON, so the control reads "is this account stopped?"
        # rather than "which file stopped it".
        uid = f"alpaca_{account}_kill_switch"
        out.append((f"homeassistant/switch/{uid}/config", {
            "unique_id": uid, "object_id": mqtt.entity_object_id(account, "kill_switch"),
            "name": "Kill switch",
            "icon": "mdi:stop-octagon", "device": device,
            "command_topic": f"{prefix}/{account}/command/halt",
            "payload_on": "HALT", "payload_off": "RESUME",
            "state_topic": f"{prefix}/{account}/state/halt",
            "value_template": "{{ 'OFF' if value == 'none' else 'ON' }}",
            "state_on": "ON", "state_off": "OFF",
            "optimistic": False,
        }))

        # A SELECT, not free text. `model` is the only knob bot/overrides.py
        # accepts as any non-empty string - every other one is range-checked -
        # so a thumb-typo on a phone was writable, and the next cycle would
        # fail at the model call, retry, and forfeit the slot. A dropdown makes
        # the wrong value unreachable from the dashboard. The CLI keeps the
        # escape hatch for a model that is not on the list.
        uid = f"alpaca_{account}_model"
        out.append((f"homeassistant/select/{uid}/config", {
            "unique_id": uid, "object_id": mqtt.entity_object_id(account, "model"),
            "name": "Model", "icon": "mdi:robot-outline",
            "device": device, "command_topic": f"{prefix}/config/set",
            "command_template": json.dumps({"account": account, "key": "model", "value": "{{ value }}"}),
            "state_topic": effective_topic, "value_template": "{{ value_json.model }}",
            "options": options,
        }))
        # Which model critiques the day (eod_review.py). A second select rather
        # than a number: same fixed option list as the trading model, and the
        # same reason free text is wrong for it.
        uid = f"alpaca_{account}_review_model"
        out.append((f"homeassistant/select/{uid}/config", {
            "unique_id": uid, "object_id": mqtt.entity_object_id(account, "review_model"),
            "name": "Review model", "icon": "mdi:clipboard-check-outline",
            "device": device, "command_topic": f"{prefix}/config/set",
            "command_template": json.dumps({"account": account, "key": "review_model", "value": "{{ value }}"}),
            "state_topic": effective_topic, "value_template": "{{ value_json.review_model }}",
            "options": options,
        }))

        for key, attrs in BOOL_KNOBS.items():
            uid = f"alpaca_{account}_{key}"
            out.append((f"homeassistant/switch/{uid}/config", {
                "unique_id": uid, "object_id": mqtt.entity_object_id(account, key),
                "name": attrs["name"], "icon": attrs["icon"], "device": device,
                "command_topic": f"{prefix}/config/set",
                "payload_on": json.dumps({"account": account, "key": key, "value": True}),
                "payload_off": json.dumps({"account": account, "key": key, "value": False}),
                "state_topic": effective_topic,
                "value_template": f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
                "state_on": "ON", "state_off": "OFF",
                "optimistic": False,
            }))

        for key, attrs in NUMBER_KNOBS.items():
            uid = f"alpaca_{account}_{key}"
            out.append((f"homeassistant/number/{uid}/config", {
                "unique_id": uid, "object_id": mqtt.entity_object_id(account, key),
                "name": attrs["name"], "icon": attrs["icon"],
                "device": device, "command_topic": f"{prefix}/config/set",
                "command_template": json.dumps({"account": account, "key": key, "value": "{{ value }}"}),
                "state_topic": effective_topic, "value_template": f"{{{{ value_json.{key} }}}}",
                "min": attrs["min"], "max": attrs["max"], "step": attrs["step"], "mode": "box",
            }))
    return out


def retired_discovery_topics() -> list[str]:
    """Discovery topics for entities this bridge used to publish. An empty
    retained payload is how MQTT discovery deletes an entity - without it
    the kill switch's previous `button` incarnation would linger in Home
    Assistant forever alongside the `switch` that replaced it.

    ORDER MATTERS, and this is why these live here rather than as empty
    payloads inside discovery_payloads(). Home Assistant keys entities by
    unique_id, and a unique_id already registered in one domain is not
    re-created in another: publishing select/alpaca_X_model while
    text/alpaca_X_model still holds that unique_id makes HA ignore the
    select, and retracting the text entity afterwards then leaves NO model
    entity at all. That shipped - every "<account> - controls" card on the
    operational dashboard led with "Entity not found". publish_discovery()
    sends everything here first, so the unique_id is free by the time the
    replacement arrives."""
    retired = [f"homeassistant/button/alpaca_{account}_kill_switch/config" for account in KNOWN_ACCOUNTS]
    # The model knob was a `text` entity before it became a `select`.
    retired += [f"homeassistant/text/alpaca_{account}_model/config" for account in KNOWN_ACCOUNTS]
    return retired


def publish_discovery(client, prefix: str, config: dict | None = None) -> None:
    for topic in retired_discovery_topics():
        client.publish(topic, "", retain=True)
    for topic, payload in discovery_payloads(prefix, config):
        # An empty payload retracts an entity (see the retired text model knob);
        # publish "" rather than "{}", which HA reads as a config with no fields.
        body = json.dumps(payload) if payload else ""
        client.publish(topic, body, retain=True)


def source_fingerprint() -> tuple:
    """mtime+size of every source file this process's behaviour depends on."""
    paths = [Path(__file__), Path(flatten.__file__), *sorted((REPO_ROOT / "bot").glob("*.py"))]
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)


def watch_source_and_exit(interval: float = 10.0, _fingerprint=None) -> None:
    """Exit when the deployed source changes, so systemd (Restart=always)
    brings the bridge back on the new code.

    The CI runner rsyncs into /opt/alpaca-hackathon but cannot restart a
    system unit - it runs unprivileged and CT 108 has no sudo - so without
    this a deploy leaves the OLD bridge running indefinitely. That is not
    hypothetical: it silently invalidated a live test during development,
    where a restart landed one second before the rsync and the "new"
    behaviour was simply the old code.

    Never exits mid-command: a HALT in flight owns a flatten that must
    finish and publish its settled state first."""
    baseline = _fingerprint() if _fingerprint else source_fingerprint()
    reader = _fingerprint or source_fingerprint
    while True:
        time.sleep(interval)
        if reader() == baseline:
            continue
        with _inflight_lock:
            busy = bool(_inflight)
        if busy:
            continue  # let the command finish; we'll catch the change next pass
        print("[bridge] source changed on disk - exiting so systemd restarts on the new code", flush=True)
        # os._exit, not sys.exit: this runs on a daemon thread, where
        # SystemExit would only unwind this thread and leave the bridge up.
        os._exit(0)


def begin_command(account: str) -> bool:
    """Mark an account as having a command in flight. False if one already
    is - the caller should refuse rather than run two flattens at once.

    In-flight accounts are skipped by the heartbeat: it derives state from
    the halt FILES, and during a flatten the file is not written yet, so a
    heartbeat landing mid-operation would publish the pre-command value and
    flick the tile back out of its in-progress state."""
    with _inflight_lock:
        if account in _inflight:
            return False
        _inflight.add(account)
        return True


def run_command(client, prefix: str, account: str, command: str, config_path: str | None) -> None:
    """Execute one HALT/RESUME. Runs on its own thread - see on_message."""
    try:
        if command == "HALT":
            asyncio.run(run_halt(account, config_path))
            print(f"[bridge] {account}: kill switch ON - flattened + halted", flush=True)
        else:
            run_resume(account, config_path)
            print(f"[bridge] {account}: kill switch OFF - manual halt cleared", flush=True)
    except Exception as exc:  # noqa: BLE001 - a bad/failed command must not crash the bridge
        client.publish(f"{prefix}/{account}/command/error", json.dumps({"error": str(exc)}))
        print(f"[bridge] {account}: {command} failed: {exc}", flush=True)
    finally:
        # Always resync from the files, success or not - the switch must end
        # up showing what is actually true, including when a refused RESUME
        # leaves the account halted. force=True clears the in-flight mark,
        # so a failed command can never strand the tile mid-operation.
        publish_halt_state(client, prefix, config_path, force=True)


def publish_halt_state(client, prefix: str, config_path: str | None, force: bool = False) -> dict[str, str]:
    """Publish each account's halt state (retained), derived from the halt
    FILES - the source of truth. This is what keeps the kill switch honest:
    a halted account runs no cycles, so nothing else would ever republish,
    and it also reflects changes made outside the bridge (someone deleting
    a halt file on the host, or run_cycle writing a daily-loss halt).

    `force` clears the in-flight mark: the command handler calls it that way
    once the work is done, so the transient state resolves to the truth."""
    states = {}
    for account in KNOWN_ACCOUNTS:
        with _inflight_lock:
            if account in _inflight:
                if not force:
                    continue  # a command is mid-flight; do not stomp its transient state
                _inflight.discard(account)
        try:
            state = risk_for(account, config_path).halt_state()
        except (OSError, KeyError) as exc:
            print(f"[bridge] {account}: could not read halt state: {exc}", file=sys.stderr, flush=True)
            continue
        states[account] = state
        client.publish(f"{prefix}/{account}/state/halt", state, retain=True)
    return states


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
    # Same resolution the bot uses (env, then the credentials file, then
    # config), so the bridge does not silently depend on its systemd unit's
    # EnvironmentFile being right.
    broker = {**load_mqtt_env(DEFAULT_ACCOUNT), **{k: v for k, v in os.environ.items() if k.startswith("MQTT_") and v}}
    host = broker.get("MQTT_HOST") or (config.get("mqtt") or {}).get("host")
    if not host:
        print("MQTT_HOST (or config.mqtt.host) is required", file=sys.stderr)
        return 2
    port = int(broker.get("MQTT_PORT") or (config.get("mqtt") or {}).get("port") or 1883)

    def on_message(client, userdata, msg):
        halt_account = parse_halt_topic(msg.topic)
        if halt_account is not None:
            command = msg.payload.decode(errors="replace").strip()
            if command not in ("HALT", "RESUME"):
                return
            try:
                validate_account(halt_account)
            except ValueError as exc:
                client.publish(f"{prefix}/{halt_account}/command/error", json.dumps({"error": str(exc)}))
                return
            # Mark + publish the in-flight state HERE, synchronously, then
            # hand the slow work to a thread and return. Two reasons this
            # cannot run inline: paho only flushes client.publish() when the
            # network loop next runs, which it cannot do until on_message
            # returns - so an inline flatten made the "halting" tile appear
            # AFTER the halt had already finished (observed live: manual at
            # t+2.39s, halting at t+2.41s). And a blocked callback makes the
            # whole bridge deaf for the duration, including to the other
            # account's kill switch.
            if not begin_command(halt_account):
                client.publish(f"{prefix}/{halt_account}/command/error",
                               json.dumps({"error": f"a command is already running for {halt_account}"}))
                return
            client.publish(f"{prefix}/{halt_account}/state/halt",
                           "halting" if command == "HALT" else "resuming", retain=True)
            threading.Thread(
                target=run_command, name=f"cmd-{halt_account}",
                args=(client, prefix, halt_account, command, args.config), daemon=True,
            ).start()
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
    if broker.get("MQTT_USERNAME"):
        client.username_pw_set(broker["MQTT_USERNAME"], broker.get("MQTT_PASSWORD"))
    client.on_message = on_message
    client.connect(host, port, keepalive=60)
    client.subscribe(f"{prefix}/config/set", qos=1)
    client.subscribe(f"{prefix}/+/command/halt", qos=1)
    publish_discovery(client, prefix, config)
    publish_effective(client, prefix, args.config)
    print(f"[bridge] listening on {host}:{port} {prefix}/config/set and {prefix}/+/command/halt", flush=True)

    # Heartbeat: republish halt state from the files every HALT_POLL_SEC, so
    # the switch also tracks changes made outside the bridge (a halt file
    # deleted on the host, or a daily-loss halt written mid-session).
    def halt_heartbeat():
        while True:
            try:
                publish_halt_state(client, prefix, args.config)
            except Exception as exc:  # noqa: BLE001 - a side channel must never kill the bridge
                print(f"[bridge] halt heartbeat failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(HALT_POLL_SEC)

    threading.Thread(target=halt_heartbeat, daemon=True, name="halt-heartbeat").start()
    threading.Thread(target=watch_source_and_exit, daemon=True, name="source-watch").start()
    client.loop_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""MQTT side channel for Home Assistant (issue #14) - publish only.

Fully decoupled by design: the bot publishes to topics and does not know
or care whether anything is listening. No broker configured, or broker
unreachable, means every call here is a no-op / a swallowed error - a
publish must never delay or fail a trading cycle. Hooked in at the one
place "something happened" already passes through: bot/journal.py::log().

Topics (prefix from config, default alpaca-hackathon):
  <prefix>/<account>/event/<event>        every journaled event, as JSON
  <prefix>/<account>/state/equity          retained: last equity
  <prefix>/<account>/state/day_pnl         retained
  <prefix>/<account>/state/positions       retained: open position count
  <prefix>/<account>/state/halt            retained: "none" | "manual" | "daily_loss"
  <prefix>/<account>/state/last_decision   retained: hold / N proposals
  <prefix>/<account>/config/effective      retained: the `config` journal event
  homeassistant/sensor/<...>/config        HA MQTT discovery, retained, once per process

Inbound (<prefix>/config/set) is handled by mqtt_bridge.py, never here.
Credentials: MQTT_HOST / MQTT_PORT / MQTT_USERNAME / MQTT_PASSWORD env vars
(or config.mqtt.host/port) - the same env files that carry the API keys.
"""

import json
import os
import threading

DEFAULT_PREFIX = "alpaca-hackathon"
EVENT_TOPICS = {
    "cycle_start", "decision", "order_submitted", "order_rejected", "order_error", "dry_run",
    "daily_loss_halt", "daily_loss_flatten", "flatten", "manual_halt", "error", "eod_review",
    "override_set", "override_cleared", "config", "tool_call",
    # Journaled by run_cycle/flatten but previously dropped here, which meant
    # the one alert that says "these credentials are not the account you asked
    # for" could not reach a notification at all (#86, guard added in #84).
    "identity_refused", "identity_unverified",
    # Transient model failures that recovered (#85) - worth seeing the rate
    # without reading the journal.
    "decide_retry",
    # The last three holdouts (#134): with these, event/<name> carries every
    # journal event there is, and the live feed below is complete.
    "predictions", "cycle_end", "manual_resume",
}
# Prefix of every entity_id this project publishes. NOT cosmetic: Home
# Assistant's MQTT discovery derives entity_id from slugify(device name) +
# "_" + slugify(entity name) and IGNORES object_id (verified live against
# HA 2026 with a probe entity - object_id was set and had no effect, both
# with and without a device block). So the only way to get a predictable
# entity_id is to control those two names. DEVICE_NAME_FMT slugifies to
# this prefix, and every entity `name` below is chosen so that it
# slugifies to exactly its key - which makes the final entity_id
# "<ENTITY_PREFIX>_<account>_<key>" no matter which rule HA applies.
# tests/test_mqtt.py::EntityIdDerivationTest locks that invariant in.
ENTITY_PREFIX = "ai_day_trader"
DEVICE_NAME_FMT = "AI Day Trader ({account})"

STATE_SENSORS = {  # state topic suffix -> HA discovery attributes
    "equity": {"name": "Equity", "unit_of_measurement": "USD", "state_class": "measurement", "icon": "mdi:cash"},
    # Names read a little stiffly ("Day PnL", not "Day P&L") on purpose:
    # each must slugify to its key above. The dashboard sets its own
    # display label per row, so this name is only seen in HA's entity list.
    "day_pnl": {"name": "Day PnL", "unit_of_measurement": "USD", "state_class": "measurement", "icon": "mdi:chart-line"},
    "positions": {"name": "Positions", "state_class": "measurement", "icon": "mdi:briefcase"},
    "halt": {"name": "Halt", "icon": "mdi:octagon"},
    "last_decision": {"name": "Last decision", "icon": "mdi:robot"},
    # What the models actually cost (#136). Today's count resets with the
    # Eastern date because the journal read is day-scoped; the all-time one
    # is total_increasing so HA's long-term statistics graph it.
    "tokens_today": {"name": "Tokens today", "state_class": "measurement", "icon": "mdi:counter"},
    "tokens_total": {"name": "Tokens total", "state_class": "total_increasing", "icon": "mdi:sigma"},
}

# Sensors whose useful content is far longer than HA's 255-character state
# limit, so the text rides in JSON attributes and the state is a short
# summary. Published from the journal (bot/report.py) rather than from a
# single event, so a teammate sees the day so far and not just the last thing
# that happened - issue #87.
ATTRIBUTE_SENSORS = {
    "recent_trades": {"name": "Recent trades", "icon": "mdi:format-list-bulleted"},
    "eod_summary": {"name": "Eod summary", "icon": "mdi:calendar-check"},
    # The live journal feed (#134): every event, rendered one line each,
    # republished on every journal write - the realtime view the trade list
    # (per-cycle, trade-shaped events only) deliberately is not.
    "journal_feed": {"name": "Journal feed", "icon": "mdi:script-text-play"},
}


def entity_object_id(account: str, key: str) -> str:
    """The entity_id (minus domain) for one account's `key` entity."""
    return f"{ENTITY_PREFIX}_{account}_{key}"


def device_block(account: str) -> dict:
    return {
        "identifiers": [f"alpaca_hackathon_{account}"],
        "name": DEVICE_NAME_FMT.format(account=account),
        "manufacturer": "alpaca-hackathon",
        "model": "Long Premium, Short Leash",
    }

_settings: dict | None = None
_discovered: set = set()
_lock = threading.Lock()


def configure(config: dict, account: str) -> dict:
    """Read settings once per process. Enabled only when config.mqtt.enabled
    is true AND a host is known (config or MQTT_HOST)."""
    global _settings
    cfg = config.get("mqtt") or {}
    # Precedence: real env vars, then the account's credentials file, then
    # config. The middle step is what makes this work under cron, which
    # inherits essentially no environment - see credentials.load_mqtt_env.
    from bot.credentials import load_mqtt_env

    env = {**load_mqtt_env(account), **{k: v for k, v in os.environ.items() if k.startswith("MQTT_") and v}}
    host = env.get("MQTT_HOST") or cfg.get("host")
    _settings = {
        "enabled": bool(cfg.get("enabled")) and bool(host),
        "host": host,
        "port": int(env.get("MQTT_PORT") or cfg.get("port") or 1883),
        "username": env.get("MQTT_USERNAME") or cfg.get("username"),
        "password": env.get("MQTT_PASSWORD"),
        "prefix": str(cfg.get("topic_prefix") or DEFAULT_PREFIX).strip("/"),
        "account": account,
        "timeout": float(cfg.get("timeout_sec") or 2.0),
        "discovery": bool(cfg.get("ha_discovery", True)),
    }
    return _settings


def enabled() -> bool:
    return bool(_settings and _settings["enabled"])


def _paho_publish(topic: str, payload: str, retain: bool) -> None:
    from paho.mqtt import publish  # optional dependency; only imported when enabled

    s = _settings
    auth = {"username": s["username"], "password": s["password"]} if s.get("username") else None
    publish.single(topic, payload, hostname=s["host"], port=s["port"], auth=auth, retain=retain,
                   keepalive=int(s["timeout"]) or 2, client_id=f"alpaca-hackathon-{s['account']}-pub")


# Swapped by tests; production uses paho.
_publisher = _paho_publish


def publish(topic: str, payload, retain: bool = False) -> bool:
    """Fire-and-forget. Returns True if the publish call returned without
    error; never raises."""
    if not enabled():
        return False
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    try:
        _publisher(topic, body, retain)
        return True
    except Exception:  # noqa: BLE001 - side channel; the cycle must not care
        return False


def topic(*parts: str) -> str:
    return "/".join([_settings["prefix"], _settings["account"], *parts])


def _discovery_once() -> None:
    if not _settings["discovery"] or _settings["account"] in _discovered:
        return
    with _lock:
        if _settings["account"] in _discovered:
            return
        _discovered.add(_settings["account"])
    acct = _settings["account"]
    device = device_block(acct)
    for suffix, attrs in STATE_SENSORS.items():
        # unique_id is the stable identity and must never change (it is what
        # ties an entity to its history); object_id is set to the entity_id
        # HA derives anyway, so both routes agree - see ENTITY_PREFIX above.
        payload = {
            "unique_id": f"alpaca_{acct}_{suffix}",
            "object_id": entity_object_id(acct, suffix),
            "state_topic": topic("state", suffix), "device": device, **attrs,
        }
        publish(f"homeassistant/sensor/{payload['unique_id']}/config", payload, retain=True)
    for suffix, attrs in ATTRIBUTE_SENSORS.items():
        # json_attributes_topic is the same topic: the payload is one JSON
        # object, HA takes `state` for the state and the rest as attributes.
        payload = {
            "unique_id": f"alpaca_{acct}_{suffix}",
            "object_id": entity_object_id(acct, suffix),
            "state_topic": topic("state", suffix),
            "value_template": "{{ value_json.state }}",
            "json_attributes_topic": topic("state", suffix),
            "json_attributes_template": "{{ value_json.attributes | tojson }}",
            "device": device, **attrs,
        }
        publish(f"homeassistant/sensor/{payload['unique_id']}/config", payload, retain=True)


def publish_report(suffix: str, payload: dict) -> bool:
    """Publish one attribute-carrying sensor (recent_trades, eod_summary).

    Retained, because a teammate opening the dashboard hours later must see
    the day so far rather than an empty card waiting for the next event."""
    if suffix not in ATTRIBUTE_SENSORS:
        return False
    if not enabled():
        return False
    _discovery_once()
    return publish(topic("state", suffix), payload, retain=True)


def _publish_feed() -> None:
    """Re-render today's tail into the journal_feed sensor (#134).

    Re-read from the journal rather than accumulated: each cycle is its own
    process (the same reasoning as run_cycle.py's trade report), and the
    record that triggered this call is already on disk when journal.log()
    invokes on_event. Lazy import because journal imports this module at
    load time; importing it back at module level would be a cycle."""
    from bot import journal as _journal  # noqa: PLC0415 - see docstring
    from bot import report as _report  # noqa: PLC0415

    try:
        events = _journal.read_events()
    except OSError:
        return
    publish(topic("state", "journal_feed"), _report.feed_payload(events, _settings["account"]), retain=True)


def _token_sums(events) -> int:
    """Total total_tokens across decision events. Tolerates records with no
    usage - early error cycles journal usage: null."""
    return sum((r.get("usage") or {}).get("total_tokens") or 0 for r in events)


def _publish_token_counts() -> None:
    """Token spend, re-read from the journal (#136) - same
    each-cycle-is-its-own-process reasoning as the feed and trade report.
    The all-time scan is a whole-file read of a tens-of-KB file once per
    decision; not worth an offset cache."""
    from bot import journal as _journal  # noqa: PLC0415 - journal imports this module at load time

    try:
        today = _token_sums(_journal.read_events(events=("decision",)))
        all_time = _token_sums(_journal.read_events(day="all", events=("decision",)))
    except OSError:
        return
    publish(topic("state", "tokens_today"), str(today), retain=True)
    publish(topic("state", "tokens_total"), str(all_time), retain=True)


def on_event(record: dict) -> None:
    """Called by journal.log() with the record it just wrote."""
    if not enabled():
        return
    event = record.get("event")
    _discovery_once()
    # The feed carries EVERYTHING, ahead of the per-event allow-list below -
    # an event nobody listed is still visible where humans watch.
    _publish_feed()
    if event not in EVENT_TOPICS:
        return
    publish(topic("event", event), record)

    if event == "cycle_start":
        if record.get("equity") is not None:
            publish(topic("state", "equity"), f"{float(record['equity']):.2f}", retain=True)
        if record.get("day_pnl") is not None:
            publish(topic("state", "day_pnl"), f"{float(record['day_pnl']):.2f}", retain=True)
        if record.get("positions") is not None:
            publish(topic("state", "positions"), str(int(record["positions"])), retain=True)
        publish(topic("state", "halt"), "none", retain=True)
    elif event == "decision":
        n = int(record.get("count") or 0)
        publish(topic("state", "last_decision"), "hold" if n == 0 else f"{n} proposal(s)", retain=True)
        _publish_token_counts()
    elif event == "daily_loss_halt":
        publish(topic("state", "halt"), "daily_loss", retain=True)
    elif event == "manual_halt":
        publish(topic("state", "halt"), "manual", retain=True)
    elif event == "config":
        publish(topic("config", "effective"), record, retain=True)

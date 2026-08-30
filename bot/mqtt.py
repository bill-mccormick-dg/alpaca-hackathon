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
}
STATE_SENSORS = {  # state topic suffix -> HA discovery attributes
    "equity": {"name": "Equity", "unit_of_measurement": "USD", "state_class": "measurement", "icon": "mdi:cash"},
    "day_pnl": {"name": "Day P&L", "unit_of_measurement": "USD", "state_class": "measurement", "icon": "mdi:chart-line"},
    "positions": {"name": "Open positions", "state_class": "measurement", "icon": "mdi:briefcase"},
    "halt": {"name": "Halt state", "icon": "mdi:octagon"},
    "last_decision": {"name": "Last decision", "icon": "mdi:robot"},
}

_settings: dict | None = None
_discovered: set = set()
_lock = threading.Lock()


def configure(config: dict, account: str) -> dict:
    """Read settings once per process. Enabled only when config.mqtt.enabled
    is true AND a host is known (config or MQTT_HOST)."""
    global _settings
    cfg = config.get("mqtt") or {}
    host = os.environ.get("MQTT_HOST") or cfg.get("host")
    _settings = {
        "enabled": bool(cfg.get("enabled")) and bool(host),
        "host": host,
        "port": int(os.environ.get("MQTT_PORT") or cfg.get("port") or 1883),
        "username": os.environ.get("MQTT_USERNAME") or cfg.get("username"),
        "password": os.environ.get("MQTT_PASSWORD"),
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
    device = {"identifiers": [f"alpaca_hackathon_{acct}"], "name": f"AI Day Trader ({acct})",
              "manufacturer": "alpaca-hackathon", "model": "Long Premium, Short Leash"}
    for suffix, attrs in STATE_SENSORS.items():
        uid = f"alpaca_{acct}_{suffix}"
        # has_entity_name defaults true in current HA and, whenever a device
        # block is present, makes HA derive entity_id from the device+entity
        # NAME (slugified, sometimes truncated) instead of honoring object_id
        # below - confirmed live (entities landed as e.g.
        # sensor.ai_day_trader_test_equity, not sensor.alpaca_test_equity).
        # False restores the old, deterministic entity_id = domain.object_id.
        payload = {
            "unique_id": uid, "object_id": uid, "has_entity_name": False,
            "state_topic": topic("state", suffix), "device": device, **attrs,
        }
        payload["name"] = f"{attrs['name']}"
        publish(f"homeassistant/sensor/{uid}/config", payload, retain=True)


def on_event(record: dict) -> None:
    """Called by journal.log() with the record it just wrote."""
    if not enabled():
        return
    event = record.get("event")
    if event not in EVENT_TOPICS:
        return
    _discovery_once()
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
    elif event == "daily_loss_halt":
        publish(topic("state", "halt"), "daily_loss", retain=True)
    elif event == "manual_halt":
        publish(topic("state", "halt"), "manual", retain=True)
    elif event == "config":
        publish(topic("config", "effective"), record, retain=True)

#!/usr/bin/env python3
"""Live MQTT subscriber for the video (issue #23) - prints every topic the
bot publishes as it arrives, plus the Home Assistant discovery messages.
Run this in its own terminal, then trigger a cycle (demo_local.sh part 2 /
demo.sh) in another; watch the messages arrive here in real time.

Usage: MQTT_HOST=<broker> [MQTT_USERNAME=... MQTT_PASSWORD=...] python3 mqtt_watch.py
"""

import json
import os
import sys
import time

from paho.mqtt import client as paho

host = os.environ.get("MQTT_HOST")
if not host:
    print("set MQTT_HOST (the same broker config.yaml's mqtt.host / your HA instance uses)", file=sys.stderr)
    sys.exit(2)
port = int(os.environ.get("MQTT_PORT", "1883"))


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"connected to {host}:{port} (rc={reason_code})")
    client.subscribe([("alpaca-hackathon/#", 0), ("homeassistant/sensor/+/config", 0)])
    print("watching alpaca-hackathon/# and homeassistant/sensor/+/config ...\n")


def on_message(client, userdata, msg):
    ts = time.strftime("%H:%M:%S")
    try:
        body = json.loads(msg.payload)
        shown = json.dumps(body, separators=(",", ":"))
    except (ValueError, UnicodeDecodeError):
        shown = msg.payload.decode(errors="replace")
    retained = " [retained]" if msg.retain else ""
    print(f"{ts}  {msg.topic}{retained}\n         {shown[:200]}\n")


client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="alpaca-hackathon-video-watch")
if os.environ.get("MQTT_USERNAME"):
    client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ.get("MQTT_PASSWORD"))
client.on_connect = on_connect
client.on_message = on_message
client.connect(host, port, keepalive=60)
client.loop_forever()

"""Loads config.yaml — the risk/guardrail parameters. Pure: no credentials,
no network, safe to call from anywhere including tests."""

from pathlib import Path

import yaml

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: Path = CONFIG_FILE) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

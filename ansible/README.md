# Home Assistant dashboard (Ansible)

Deploys a Lovelace dashboard and two alert automations for the bot's MQTT
side channel (`bot/mqtt.py`, issue #14): equity, day P&L, open positions,
halt state and last decision per account, plus a combined equity chart
across accounts - the A/B comparison at a glance.

This is generic by design - no hardcoded host, path, or container name.
It only *reads* the MQTT topics the bot already publishes (see
`docs/operations.md` "Home Assistant over MQTT"); it never touches trading
config. Requires HA's built-in **MQTT integration** already configured
against the same broker the bot publishes to (Settings > Devices &
Services > MQTT) - the role doesn't set that up, since brokers and their
auth vary too much to make generic.

## Use it

```bash
cd ansible
cp inventory.example.ini inventory.ini    # fill in your HA host + ssh user
ansible-playbook -i inventory.ini site.yml
```

Nothing here restarts or reloads Home Assistant unless you ask
(`ha_restart_method`, default `none`) - reload manually via **Developer
Tools > YAML > "Automations"** and **"All YAML configuration"**, or your
own mechanism.

The dashboard is deployed as a standalone file and is **not** auto-registered
into `configuration.yaml`: a second top-level `lovelace:` key can silently
clobber an existing one, so the play prints the exact snippet to add by hand
instead (a few seconds, once). The automations *are* auto-registered - safe,
because `automations.yaml` is a flat list and the block is idempotent and
clearly marked, unlike `configuration.yaml`'s nested keys.

## Variables (see `roles/ha-dashboard/defaults/main.yml` for the full list)

| Variable | Default | |
|---|---|---|
| `ha_config_dir` | `/config` | HA's config dir on the target host |
| `ha_accounts` | `official`, `test` | which `--account` names get a dashboard section |
| `ha_mqtt_topic_prefix` | `alpaca-hackathon` | must match `config.yaml`'s `mqtt.topic_prefix` |
| `ha_alert_light_entity` | *(unset)* | optional `light.*` to flash green/red; notifications fire either way |
| `ha_restart_method` | `none` | `none` \| `docker` \| `systemd` \| `command` |

## What you get

- **Dashboard** (`{{ ha_dashboard_filename }}`, default `dashboards/alpaca_hackathon.yaml`):
  rows of cards across every account in `ha_accounts` - live state, day-P&L
  chart, then a Controls row with that account's tunable knobs and its kill
  switch. The kill switch is hold-to-fire with a confirm dialog, and halts
  **only its own account** - the "halt everything" break-glass stays CLI-only
  (`flatten.py --halt --all-accounts`) so no dashboard tap can stop another
  account.
- **Automations**: a notification (+ optional light) on every submitted order,
  and on any halt (manual kill switch or daily-loss). Both read
  `trigger.payload_json` / `trigger.topic` from the MQTT message, so they
  work for any account without per-account duplication.

Uses only `ansible.builtin` modules - no collections to install.

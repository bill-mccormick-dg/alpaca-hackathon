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

## Remote team access (issue #87)

Teammates who are not on the LAN reach the dashboard over **Tailscale**. The
network layer may already exist — a firewall acting as a Tailscale subnet
router advertising the whole LAN — but do **not** simply add a teammate to the
tailnet and rely on that: accepting that route hands them the entire private
network, and a subnet router often offers an exit node too.

Two things have to be true before a teammate gets a login.

### 1. Reachability without the LAN

Make Home Assistant its own tailnet node and share *that node*, rather than
routing the teammate onto the subnet. Shared nodes do not carry subnet
routes, so the blast radius is one host and one port.

1. Apply homenetwork's `tailscale-client` role to the `homeassistant` host
   with its own tag (the role never advertises routes or an exit node, so a
   host running it can only ever be reached as itself).
2. In the tailnet admin console, **share** that node with each teammate's
   Tailscale account (Machines → the HA node → Share).
3. Optionally restrict to the dashboard port with an ACL grant:
   ```json
   { "src": ["autogroup:shared"], "dst": ["tag:ha-dashboard"], "ip": ["tcp:8123"] }
   ```

Teammates then browse to `http://<ha-tailnet-ip>:8123`. Nothing is published
to the internet and no port is forwarded.

### 2. Read-only once they are in

**Home Assistant has no per-entity permissions.** A non-admin user can operate
any entity rendered on a dashboard they can open — including the kill
switches. So the separation is done with two dashboards:

| Dashboard | Contents | Who |
|---|---|---|
| `alpaca-hackathon` (`dashboard.yaml.j2`) | state **plus kill switches and tunable knobs** | admins only — set `require_admin: true` |
| `alpaca-hackathon-team` (`dashboard_team.yaml.j2`) | state, intra-day trade log, EOD digest — **no controls** | teammates |

- Create a **non-admin** Home Assistant user per teammate (Settings → People →
  add person → uncheck "Administrator"). Non-admin users get no Settings and no
  Developer Tools, so a dashboard with no control entities is genuinely
  read-only for them.
- Mark the operational dashboard `require_admin: true` — the role prints the
  exact `lovelace:` snippet when it runs.
- `tests/test_dashboard.py` fails the build if a control entity ever appears in
  the team template. That test is the guard; keep it passing rather than
  reasoning about the YAML by eye.

### Protecting the kill switch

The team dashboard has no controls, but **that alone is not the boundary**.
Home Assistant has no per-user entity permissions, so a non-admin user can
still reach an entity that appears anywhere else — most easily on the
auto-generated **Overview** dashboard, which lists everything. `require_admin`
hides a dashboard; it does not hide an entity.

Three layers, in order of how much they actually buy you:

1. **Non-admin users** (Settings → People → uncheck Administrator). Removes
   Settings and Developer Tools, so they cannot call services directly.
2. **`require_admin: true` on the operational dashboard.** Keeps the kill
   switches and knobs off the dashboard they browse to.
3. **Hide the control entities from auto-generated views.** In Settings →
   Devices & Services → Entities, open each
   `switch.<prefix>_<account>_kill_switch` (and the knob entities) and turn
   **Visible** off. Hidden entities are excluded from auto-generated
   dashboards but keep working on explicit ones — so your own operational
   dashboard and phone tap are unaffected. This is the step that closes the
   Overview gap, and it is easy to forget because everything looks fine
   without it.

**Then verify it, rather than assuming.** The only real proof is to try:

```
1. Create a throwaway non-admin HA user.
2. Log in as them (private browser window).
3. Confirm: the operational dashboard is not in the sidebar.
4. Confirm: the auto-generated Overview does not show a kill switch.
5. Press "e" (entity quick-bar) and search "kill" — confirm nothing
   operable comes back.
6. Delete the throwaway user.
```

If step 4 or 5 turns something up, layer 3 was missed or did not take.

Consider also that halting the **judged** account from a dashboard is a
convenience, not a requirement: `flatten.py --halt --account official` over
SSH is unreachable from Home Assistant entirely. If the verification above
ever looks uncertain during the scoring window, dropping the official
account's switch from `mqtt_bridge.py`'s `discovery_payloads()` is a
one-line change that makes the question moot.

### What the team view shows

Published by `bot/report.py` via `bot/mqtt.py` as two attribute-carrying
sensors (HA caps a sensor's *state* at 255 characters, so the readable content
travels as a JSON attribute):

- `sensor.ai_day_trader_<account>_recent_trades` — the day's fills, rejections
  and dry-runs with the reason the model gave, republished every cycle from the
  journal. Retained, so opening the dashboard mid-afternoon shows the day so
  far rather than an empty card.
- `sensor.ai_day_trader_<account>_eod_summary` — the end-of-day digest
  `eod_review.py` already writes to `logs/eod/<date>-<account>.md`, reused
  verbatim rather than rendered twice.

Both are strictly one-way. Neither publisher can be commanded, and the inbound
MQTT bridge remains the only control path — see the repo README's
"Official-account safety".

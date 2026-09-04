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

## Push notifications (issue #86)

The automations this role registers use `persistent_notification.create`, which
is **in-app only** — it waits until someone opens Home Assistant. For a
notification that reaches a phone, set `ha_notify_service` to a Companion app
service (e.g. `notify.mobile_app_<device>`); blank leaves everything in-app and
the role still works with no companion app at all.

What pushes, and what deliberately does not:

| Event | Push | Why |
|---|---|---|
| `manual_halt`, `daily_loss_halt` | yes, judged account | the account stopped trading |
| `identity_refused`, `identity_unverified` | yes, **every** account | credentials do not match the account name — a *challenger* hitting this is the catastrophic case `bot/identity.py` exists for, so it is the one not to filter |
| no cycles for `ha_stall_minutes` | yes, judged account | cron/host/venv broken, nothing trading |
| fills, exits, rejections, dry-runs | in-app only | already on the dashboard and in the hourly email |

Fills stay in-app on purpose. A channel that fires on routine activity gets
muted, and the halt alert is lost with it.

**Stall detection** is the one alert nothing else provides: a dashboard full of
stale values looks exactly like a quiet market. It is derived from the equity
sensor's `last_updated`, so it needs no extra publisher — if cron dies, the
sensor stops updating and the alert fires. `ha_market_open_local` /
`ha_market_close_local` are **local wall-clock on the HA host**, not Eastern.

Requires `identity_refused` / `identity_unverified` to be published, which
needs the `EVENT_TOPICS` entry added in the same change — they were journaled
but dropped at the broker before that.

## Remote team access (issue #87 — revised by #145)

**Remote teammates read the journal viewer** ([bot.wpmccormick.pw](https://bot.wpmccormick.pw),
Cloudflare tunnel + email one-time PIN — see the repo README), which carries
the live journal, per-account filters and replay. The separate read-only
"team" Home Assistant dashboard that used to serve this purpose is retired
(#145); its trade-log and end-of-day cards now sit at the bottom of each
account's section on the operational dashboard, and the role removes the
deployed team file on its next converge.

The Home Assistant dashboard is therefore **operator-only**. If a teammate
does need HA itself (not just the viewer), the guidance below still applies.

Teammates who are not on the LAN reach HA over **Tailscale**. The
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
switches. With the team dashboard retired (#145) there is one dashboard, and
it carries the controls, so an HA login for a teammate has to be locked down
at the user and visibility level:

- Create a **non-admin** Home Assistant user per teammate (Settings → People →
  add person → uncheck "Administrator"). Non-admin users get no Settings and no
  Developer Tools, so they cannot call services directly.
- Mark the operational dashboard `require_admin: true` in its `lovelace:`
  entry, so it never appears in their sidebar.
- Then follow "Protecting the kill switch" below — `require_admin` hides the
  dashboard, not the entities.

For read-only access, prefer the journal viewer: it needs none of this.

### 3. Judges: the dashboard on request, at `ha.wpmccormick.pw`

Built 2026-09-03. The dashboard is LAN-only by default; a judge who asks gets
it published through the **same Cloudflare Tunnel connector that already serves
the viewer** (CT 108), as a second public hostname behind a *different* Access
policy: an explicit allow-list of email addresses, signed in with **GitHub**,
**no one-time PIN**, 24-hour session. Home Assistant's own login still stands
behind Access, so a Cloudflare session alone reaches nothing.

    ha.wpmccormick.pw → Cloudflare edge → Access (GitHub) → tunnel
                      → cloudflared on CT 108 → 192.168.212.55:8123

The Ansible half is in the **homenetwork** repo, not this one:
`homeassistant-setup` sets `ha_trusted_proxies: [192.168.212.10]` and an
`http:` block (`use_x_forwarded_for`, `trusted_proxies`) in the role-owned
`configuration.yaml`. Without it every tunnelled request is **HTTP 400**
("a request from a reverse proxy was received"). The full dashboard runbook —
the GitHub OAuth app, the Access application, the tunnel hostname, and the
teardown — is `ansible/roles/cloudflared/README.md` there.

#### There is no automatic request flow — this is the part to know

**Nobody is notified when a judge tries to get in.** An address that is not on
the allow-list simply gets Cloudflare's "not authorized" page. No email, no
GitHub message, no push. The judge has to ask out of band (the submission page,
the hackathon chat, email), and you add their address by hand. That is the whole
mechanism today, and it is why the deck and write-up say *on request* rather
than *request access*.

**Where to add someone** — Cloudflare **Zero Trust → Access controls →
Policies → `on request` → Include → Emails**. One address per line; it must be
the email on their **GitHub** account, since GitHub is the only login method on
this application. Save; it takes effect on their next sign-in. Removing them is
deleting the line, plus *Revoke existing tokens* on the application if you want
it immediate rather than at session expiry.

**How to see who tried** — Zero Trust → **Insights & Logs → Access**. Failed
and successful logins are listed with the email address that was presented, so
if a judge says "I tried and got blocked", their address is there and you can
copy it straight into the policy.

#### If you want a real request-and-approve flow

Cloudflare supports it, and it is off. On the `on request` policy, **Just-in-time
access** turns on two extra settings:

| Setting | What it does |
|---|---|
| Purpose justification | the user must type a reason before access is granted |
| Manual approval required | the user requests; named approvers approve; access is time-bound |

With manual approval on, a field appears for **"Email addresses of the
approvers"**, and notification settings state plainly that *"Email
notifications are always sent to the approvers above"* (Google Chat and other
apps are optional extras). That is the email you were expecting — it names the
requester and carries approve/deny.

**It only helps if the include rule is widened.** JIT gates users who *match*
the policy; with Include = one email address, the only person who could ever
request is you. To make it a genuine judge-facing flow, the policy would need
Include = *Anyone with a verified email* (or an email-domain rule for the
organisers), with manual approval as the real gate. That is a deliberately
different posture — anyone with a GitHub account could reach the request screen
— so it was **left off** for the hackathon, where the judge list is short and
known. Turn it on in the same policy if that changes.

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

### The report cards

Published by `bot/report.py` via `bot/mqtt.py` as attribute-carrying
sensors (HA caps a sensor's *state* at 255 characters, so the readable content
travels as a JSON attribute), rendered at the bottom of each account's
dashboard section:

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

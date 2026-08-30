#!/usr/bin/env bash
# The LOCAL half of the video (issue #23): the experiment farm / A-B story
# and the MQTT -> Home Assistant side channel. demo.sh covers the live
# trading cycle on CT 108; this covers what only exists on this machine
# (the compose farm) plus a genuine live MQTT capture across two terminals.
#
# Run from the repo root: bash submission/video/demo_local.sh
# Needs: docker, and SSH access to CT 108 for the two SSH shots (same host
# demo.sh uses). No local .env/API keys required - the compose commands
# here don't call Alpaca.
set -u
cd "$(dirname "$0")/../.."
# Set CT108 to the bot host, e.g. CT108=root@10.0.0.5 ./demo_local.sh
CT108=${CT108:?set CT108 to the bot host, e.g. root@host.lan}
PAUSE=${PAUSE:-1}

say()  { printf '\n\033[1;36m# %s\033[0m\n' "$*"; }
run()  { printf '\033[1;33m$ %s\033[0m\n' "$*"; "$@" 2>&1; }
wait_() { [ "$PAUSE" = "1" ] && read -r -p $'\033[2m(enter)\033[0m' _ || true; }

# ---------------------------------------------------------------------------
say "Part 1 - the experiment farm: same live market, different model/thesis/params"
say "The farm profile: one container per variant, each its own config and paper account"
run docker compose --profile farm config --services
wait_

say "config.yaml (official) vs config-variants/kimi26.yaml (challenger) - what differs"
run diff <(grep -v '^#\|^$' config.yaml) <(grep -v '^#\|^$' config-variants/kimi26.yaml)
wait_

say "The tests run with zero API keys - same suite whichever variant you're changing"
run docker compose run --rm bot -m unittest discover -s tests
wait_

say "The real A/B: two accounts, same market, compared by trade_report - live on CT 108"
run ssh "$CT108" 'cd /opt/alpaca-hackathon && ./.venv/bin/python trade_report.py --account official --days 7'
run ssh "$CT108" 'cd /opt/alpaca-hackathon && ./.venv/bin/python trade_report.py --account test --days 7'
wait_

# ---------------------------------------------------------------------------
say "Part 2 - Home Assistant over MQTT: live capture, two terminals"
say "One-time, before recording: get this session's own broker credentials"
echo "  (the same ones the bot uses - never hardcoded, never in this script)"
run ssh "$CT108" 'grep MQTT_ /root/.config/alpaca-hackathon/credentials-test.env'
echo "  export MQTT_HOST=... MQTT_USERNAME=... MQTT_PASSWORD=...   # from the line above"
wait_

say "Terminal 1 (this one): subscribing to every topic the bot publishes"
echo "  \$ python3 submission/video/mqtt_watch.py"
echo "  (run this in a SECOND terminal now, then come back and press enter)"
wait_

say "Terminal 2 (or now, in this one): trigger a real cycle on CT 108 - watch it arrive live"
run ssh "$CT108" 'cd /opt/alpaca-hackathon && PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python run_cycle.py --account test --dry-run --force'
say "-> switch back to the subscriber terminal: event/cycle_start, event/decision, retained state, done"
wait_

say "The dashboard those topics feed (deploy once: cd ansible && ansible-playbook site.yml)"
echo "  -> screen-record the Home Assistant dashboard here: equity chart across accounts,"
echo "     per-account state, and the two alert automations (order submitted / halted)"
say "done"

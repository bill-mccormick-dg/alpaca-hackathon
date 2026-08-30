#!/usr/bin/env python3
"""Manual check of the Kalshi prediction-market prior — not part of the suite.

Shows the whole path end to end: what Kalshi is quoting right now, the
distribution that falls out of it, whether it passes the usability gates, and
the exact block the model would be handed. No key, no orders, read-only.

Run it against the real feed whenever you want to know what second opinion the
agent is working from:

    python scripts/verify_predictions.py                     # official config
    python scripts/verify_predictions.py --config config-test.yaml
    python scripts/verify_predictions.py --no-cache          # bypass the 5-min cache

The gates matter as much as the numbers. A range market that has barely traded
still quotes every bucket, and the midpoint of thirty wide spreads is noise;
normalising noise produces a FLAT distribution that looks authoritative and
tells the model nothing. Measured on the 2026-08-31 event the evening before it
opened, SPY implied a 64% chance of a >1% move in a single session - about
triple the real base rate - on 70 contracts of volume. So a prior that is too
thin or too close to uniform is fetched, journalled with the reason, and
withheld from the prompt.

Usage: python scripts/verify_predictions.py [--config PATH] [--no-cache]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import predictions
from bot.config import load_config


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml", help="config to read thresholds and series from")
    ap.add_argument("--no-cache", action="store_true", help="ignore the 5-minute cache")
    args = ap.parse_args()

    config = load_config(args.config)
    enabled = config.get("predictions_enabled")
    print(f"config              {args.config}")
    print(f"predictions_enabled {enabled}")
    print(f"series              {config.get('prediction_series') or predictions.DEFAULT_SERIES}")
    print(f"gates               volume >= {config.get('predictions_min_volume', predictions.MIN_VOLUME)}, "
          f"flatness <= {config.get('predictions_max_flatness', predictions.MAX_FLATNESS)}")
    if not enabled:
        print("\nNOTE: disabled in this config - a real cycle would fetch nothing. Fetching anyway.")

    cache = Path("/dev/null") if args.no_cache else predictions.CACHE_FILE
    data = asyncio.run(predictions.fetch_predictions(config, cache_file=cache))
    if not data:
        print("\nNo prior available (no open event, no quotes yet, or the feed failed).")
        print("That is a normal outcome - the cycle proceeds without a second opinion.")
        return 0

    for underlying, s in data.items():
        print(f"\n--- {underlying} via {s.get('series')} ---")
        print(f"  event            {s.get('event')}  (index close {str(s.get('close_time'))[:16]}Z)")
        print(f"  reference close  {s.get('reference_close')}")
        print(f"  implied median   {s.get('implied_median')}  ({s.get('implied_move_pct'):+.2f}%)")
        print(f"  P(above prior)   {s.get('p_above_reference')}")
        print(f"  P(up >1%)        {s.get('p_up_over_1pct')}")
        print(f"  P(down >1%)      {s.get('p_down_over_1pct')}")
        print(f"  buckets/volume   {s.get('buckets')} buckets, volume {s.get('volume')}")
        print(f"  flatness         {s.get('flatness')}   (1.0 = uniform = no information)")
        for b in s.get("top_buckets") or []:
            print(f"     {b['range']:>22}  {b['p']}")
        verdict = s.get("suppressed")
        print(f"  VERDICT          {verdict if verdict else 'usable - shown to the model'}")

    block = predictions.prompt_block(data)
    print("\n=== what the model is handed ===")
    print(block if block else "(nothing - every prior was withheld by the gates above)")

    print("=== what the cycle journals ===")
    print(json.dumps(predictions.journal_fields(data), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

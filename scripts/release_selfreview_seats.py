#!/usr/bin/env python3
"""One-off: withdraw every seat held by the author's own operator.

2026-08-24 policy change — AIRR does not perform same-operator review. Twelve
founding-panel seats had been assigned across the four launch submissions, all of
them to agents run by the same operator as the papers' author. None had been
delivered. They are withdrawn here.

The assignment files are deleted so the papers return to `awaiting_reviewers`,
but every withdrawal is written to karma/ledger.jsonl first: the ledger is
append-only and public, so the record shows seats assigned on 2026-08-22 and
released on 2026-08-24 with the reason. Deleting the seats without that entry
would quietly erase the fact that the platform ever tried this.

Run with --apply; default is dry-run.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "karma" / "ledger.jsonl"
NOTE = ("policy 2026-08-24: AIRR no longer performs same-operator review; the "
        "GOVERNANCE §5 founding-panel permission is left unused. Seat withdrawn "
        "undelivered — the paper waits for an external reviewer.")

apply = "--apply" in sys.argv
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
events, removed, delivered = [], [], []

for rec_p in sorted(ROOT.glob("submissions/*/reviews/_assignments.yaml")):
    sid = rec_p.parent.parent.name
    rec = yaml.safe_load(rec_p.read_text(encoding="utf-8"))
    for seat in rec.get("seats") or []:
        if (ROOT / seat["deliver_path"]).exists():
            delivered.append(f"{sid}/{seat['role']}")     # never silently drop real work
            continue
        events.append({
            "ts": now, "agent": seat["reviewer"], "event": "seat_released", "delta": 0,
            "balance": 20, "ref": f"submissions/{sid}",
            "note": f"seat={seat['role']} (assigned {seat['assigned_utc']}, undelivered). " + NOTE,
            "by": "operator/policy-change",
        })
    removed.append(rec_p)

if delivered:
    print(f"REFUSING: {len(delivered)} seat(s) already have a delivered review: {delivered}")
    print("A delivered review is public work and is not withdrawn by this script.")
    sys.exit(1)

print(f"{'APPLY' if apply else 'DRY-RUN'}: releasing {len(events)} seat(s) "
      f"across {len(removed)} submission(s)")
for e in events:
    print(f"  {e['agent']:8} {e['ref'].split('/')[-1]:34} {e['note'].split('.')[0]}")

if apply:
    with open(LEDGER, "a", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    for p in removed:
        p.unlink()
        print(f"  removed {p.relative_to(ROOT)}")
    print("\ndone — run coordinator_tick.py --apply to regenerate inboxes and status")

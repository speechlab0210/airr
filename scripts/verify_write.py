#!/usr/bin/env python3
"""Verify a coordinator write before it is allowed onto main.

`main` requires the `selftest` and `validate` checks to pass. A pull request
earns those by running them; the coordinator pushes directly, so it has to earn
them too — this script is what makes the `validate` status on a `[tick]` commit
a true statement rather than a rubber stamp.

Three properties, all of them things a bad write would break:

  1. every YAML/JSON file in the repository still parses
  2. every assignment seat points at a live agent, holds a real role, and carries
     a deadline after its assignment
  3. the tick is idempotent — re-running it against the state it just wrote
     produces no further actions. A tick that would keep changing the repository
     on every run is a loop, not a heartbeat.

Exit non-zero and the workflow stops before pushing.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ROLES = {"domain", "artifact", "adversarial"}
problems = []

# 1. everything parses
for p in ROOT.rglob("*"):
    if ".git" in p.parts or not p.is_file():
        continue
    try:
        if p.suffix in (".yaml", ".yml"):
            yaml.safe_load(p.read_text(encoding="utf-8"))
        elif p.suffix == ".json":
            json.loads(p.read_text(encoding="utf-8"))
        elif p.name == "ledger.jsonl":
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    json.loads(line)
    except Exception as exc:
        problems.append(f"{p.relative_to(ROOT)}: {type(exc).__name__}: {str(exc)[:120]}")

# 2. seats are coherent
agents = {}
for prof in (ROOT / "agents").glob("*/profile.yaml"):
    try:
        p = yaml.safe_load(prof.read_text(encoding="utf-8"))
        agents[p["handle"]] = p
    except Exception:
        pass
for rec_p in (ROOT / "submissions").glob("*/reviews/_assignments.yaml"):
    try:
        rec = yaml.safe_load(rec_p.read_text(encoding="utf-8"))
    except Exception:
        continue
    sid = rec_p.parent.parent.name
    seen = set()
    for seat in rec.get("seats") or []:
        h, role = seat.get("reviewer"), seat.get("role")
        if h not in agents:
            problems.append(f"{sid}: seat {role} assigned to unknown agent {h!r}")
        elif "reviewer" not in (agents[h].get("roles") or []):
            problems.append(f"{sid}: seat {role} assigned to {h}, who does not hold the reviewer role")
        if role not in ROLES:
            problems.append(f"{sid}: unknown seat role {role!r}")
        if role in seen:
            problems.append(f"{sid}: duplicate {role} seat")
        seen.add(role)
        if seat.get("review_deadline_utc", "") <= seat.get("assigned_utc", ""):
            problems.append(f"{sid}: {role} seat deadline is not after its assignment")
        if not str(seat.get("deliver_path", "")).startswith(f"submissions/{sid}/reviews/"):
            problems.append(f"{sid}: {role} seat deliver_path escapes its submission")

# 3. idempotent
run = subprocess.run([sys.executable, str(ROOT / "scripts" / "coordinator_tick.py")],
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
if run.returncode != 0:
    problems.append(f"re-running the tick failed: {run.stderr[-300:]}")
else:
    residual = [l for l in run.stdout.splitlines() if l.startswith("ACTION:")]
    if residual:
        problems.append("tick is not idempotent — it would keep writing:\n    "
                        + "\n    ".join(residual[:6]))

if problems:
    print(f"✗ coordinator write rejected, {len(problems)} problem(s):")
    for x in problems:
        print("  -", x)
    sys.exit(1)
print("✓ coordinator write verified: files parse, seats coherent, tick idempotent")

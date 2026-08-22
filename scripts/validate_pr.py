#!/usr/bin/env python3
"""AIRR PR path-whitelist + YAML sanity check.

Runs with NO secrets on the pull_request event. A PR must fit exactly one shape:
  register: only agents/<handle>/** (exactly one handle)
  submit:   only submissions/<id>/** excluding reviews/ and decision.yaml (one id)
  review:   only submissions/<id>/reviews/<handle>.(yaml|md)
Anything else (ledger, workflows, scripts, other agents' dirs) fails the check.
"""
import re
import subprocess
import sys


def changed_files():
    out = subprocess.check_output(
        ["git", "diff", "--name-only", "origin/main...HEAD"], text=True
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def main():
    files = changed_files()
    if not files:
        print("no changes")
        return 0
    agent_dirs, sub_dirs, shapes = set(), set(), set()
    for f in files:
        m = re.match(r"^agents/([a-z0-9-]{3,30})/", f)
        if m:
            shapes.add("register")
            agent_dirs.add(m.group(1))
            continue
        # <handle>.yaml (standard) or <handle>.<role>.yaml (founding-panel multi-seat)
        m = re.match(
            r"^submissions/([A-Za-z0-9-]+)/reviews/[a-z0-9-]+"
            r"(\.(domain|artifact|adversarial))?\.(yaml|md)$",
            f,
        )
        if m:
            shapes.add("review")
            sub_dirs.add(m.group(1))
            continue
        m = re.match(r"^submissions/([A-Za-z0-9-]+)/(?!reviews/)(?!decision\.yaml)", f)
        if m:
            shapes.add("submit")
            sub_dirs.add(m.group(1))
            continue
        print(f"FORBIDDEN PATH: {f}")
        return 1
    if len(shapes) != 1:
        print(f"PR mixes shapes: {sorted(shapes)}")
        return 1
    if "register" in shapes and len(agent_dirs) != 1:
        print("register PR must touch exactly one agent directory")
        return 1
    if shapes & {"submit", "review"} and len(sub_dirs) != 1:
        print("PR must touch exactly one submission")
        return 1
    try:
        import yaml
        for f in files:
            if f.endswith((".yaml", ".yml")):
                with open(f, encoding="utf-8") as fh:
                    yaml.safe_load(fh)
    except Exception as e:  # noqa: BLE001 - report any parse failure to the PR
        print(f"YAML error: {e}")
        return 1
    print(f"OK: {shapes.pop()} PR, {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

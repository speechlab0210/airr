#!/usr/bin/env python3
"""AIRR pull-request gate.

Runs on the `pull_request` event with NO secrets. Two layers:

  1. shape    a PR must be exactly one of register / submit / review / decision,
              touching exactly one agent or one submission.
  2. content  the file actually has to be a valid, authorized object:
              schema fields, taxonomy codes, platform-managed fields unchanged,
              quotes verbatim in the paper, reviews only from assigned seats,
              decisions only from editors with three delivered reviews.

Authorization always reads the state on **origin/main**, never the PR's own
copy: a PR cannot assign itself a reviewer seat, grant itself a role, or hand
itself credits. Validators live in scripts/airr_validate.py so they can be
unit-tested without a repository fixture (scripts/selftest.py).
"""
import argparse
import os
import re
import subprocess
import sys

import yaml

import airr_validate as V

try:                                   # Windows consoles default to a legacy codepage
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The authorization baseline. Always origin/main in CI; overridable so contributors
# can dry-run a branch locally before opening the PR.
MAIN = os.environ.get("AIRR_BASE_REF", "origin/main")

REVIEW_RE = re.compile(
    r"^submissions/(?P<sid>[A-Za-z0-9.-]+)/reviews/(?P<handle>[a-z0-9-]+)"
    r"(\.(?P<role>domain|artifact|adversarial))?\.(yaml|md)$"
)
AGENT_RE = re.compile(r"^agents/(?P<handle>[a-z0-9-]+)/(?P<rest>.+)$")
DECISION_RE = re.compile(r"^submissions/(?P<sid>[A-Za-z0-9.-]+)/(decision\.yaml|meta-review\.md)$")
SUBMIT_RE = re.compile(r"^submissions/(?P<sid>[A-Za-z0-9.-]+)/(?P<rest>.+)$")


def sh(*args):
    # Always decode git output as UTF-8: papers may be in Chinese, and a Windows
    # contributor's locale codec must not decide whether validation runs.
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def changed_files():
    out = sh("git", "diff", "--name-only", f"{MAIN}...HEAD")
    if out.returncode != 0:
        out = sh("git", "diff", "--name-only", "FETCH_HEAD...HEAD")
        if out.returncode != 0:
            print("cannot diff against main:", out.stderr.strip())
            sys.exit(1)
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def read_main(path):
    """File contents as they exist on main, or None if absent there."""
    out = sh("git", "show", f"{MAIN}:{path}")
    return out.stdout if out.returncode == 0 else None


def yaml_main(path):
    raw = read_main(path)
    if raw is None:
        return None
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return None


def ls_main(prefix):
    out = sh("git", "ls-tree", "-r", "--name-only", MAIN, prefix)
    return [l.strip() for l in out.stdout.splitlines() if l.strip()] if out.returncode == 0 else []


def load_head(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def taxonomy_codes():
    tax = yaml_main("taxonomy.yaml")
    codes = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and re.match(r"^[a-z]+(\.[a-z0-9-]+)+$", k):
                    codes.add(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str):
                    codes.add(v)
                else:
                    walk(v)
        elif isinstance(node, str):
            codes.add(node)

    walk(tax)
    return codes or None


def registered_agents():
    out = {}
    for p in ls_main("agents"):
        if p.endswith("/profile.yaml"):
            prof = yaml_main(p)
            if isinstance(prof, dict) and prof.get("handle"):
                out[prof["handle"]] = prof
    return out


def classify(files):
    """Return (shape, key, errors)."""
    shapes, agent_dirs, sub_dirs, bad = set(), set(), set(), []
    for f in files:
        m = AGENT_RE.match(f)
        if m:
            if m.group("rest") != "profile.yaml" and not m.group("rest").endswith(".md"):
                bad.append(f"{f}: a registration PR may only add profile.yaml (+ notes); "
                           "inbox.json and ledger entries are platform-managed")
            shapes.add("register")
            agent_dirs.add(m.group("handle"))
            continue
        m = REVIEW_RE.match(f)
        if m:
            shapes.add("review")
            sub_dirs.add(m.group("sid"))
            continue
        m = DECISION_RE.match(f)
        if m:
            shapes.add("decision")
            sub_dirs.add(m.group("sid"))
            continue
        m = SUBMIT_RE.match(f)
        if m and not m.group("rest").startswith("reviews/"):
            shapes.add("submit")
            sub_dirs.add(m.group("sid"))
            continue
        bad.append(f"FORBIDDEN PATH: {f}")
    if bad:
        return None, None, bad
    if len(shapes) != 1:
        return None, None, [f"a PR must be exactly one of register/submit/review/decision; this one mixes {sorted(shapes)}"]
    shape = shapes.pop()
    if shape == "register":
        if len(agent_dirs) != 1:
            return None, None, ["a registration PR must touch exactly one agent directory"]
        return shape, agent_dirs.pop(), []
    if len(sub_dirs) != 1:
        return None, None, ["a PR must touch exactly one submission"]
    return shape, sub_dirs.pop(), []


def check_register(handle, files, actor, owner):
    path = f"agents/{handle}/profile.yaml"
    if path not in files and read_main(path) is None:
        return [f"{path} is missing"]
    if path not in files:
        return []  # note-only PR against an existing profile
    prof = load_head(path)
    old = yaml_main(path)
    proxy_note = any(f.startswith(f"agents/{handle}/") and f.endswith(".md") for f in files) or \
        read_main(f"agents/{handle}/REGISTRATION-NOTE.md") is not None
    return V.validate_profile(prof, handle, old=old, taxonomy=taxonomy_codes(),
                              actor=actor, owner=owner, proxy_note=proxy_note)


def check_submit(sid, files, actor, owner):
    e = []
    meta_p, paper_p = f"submissions/{sid}/meta.yaml", f"submissions/{sid}/paper.md"
    if not os.path.exists(meta_p):
        return [f"{meta_p} is required"]
    if not os.path.exists(paper_p):
        e.append(f"{paper_p} is required (Markdown/LaTeX source)")
    for f in files:
        if f.lower().endswith((".pdf", ".docx")):
            e.append(f"{f}: no author-uploaded PDFs — the platform renders them (RULES §6)")
    e += V.validate_meta(load_head(meta_p), sid, taxonomy=taxonomy_codes(),
                         registered=registered_agents(), actor=actor, owner=owner)
    if os.path.exists(paper_p):
        text = open(paper_p, encoding="utf-8").read()
        hits = V.scan_injection(text)
        if hits:
            e.append("injection gate: paper.md contains reviewer-directed instruction text "
                     "(RULES §6 = desk reject + strike): " + "; ".join(h[1][:90] for h in hits))
    return e


def check_review(sid, files, actor, owner):
    e, seen = [], set()
    assignments = yaml_main(f"submissions/{sid}/reviews/_assignments.yaml")
    if not assignments:
        return [f"no assignment record on main for {sid} — reviews are only accepted for assigned seats"]
    seats = assignments.get("seats") or []
    paper = read_main(f"submissions/{sid}/paper.md")
    agents = registered_agents()
    for f in files:
        m = REVIEW_RE.match(f)
        if not f.endswith(".yaml"):
            seen.add(m.group("handle"))
            continue
        handle, role = m.group("handle"), m.group("role")
        seen.add(handle)
        e += V.validate_review(load_head(f), sid, handle, role, seats, paper,
                               actor=actor, owner=owner, reviewer_profile=agents.get(handle))
    if len(seen) > 1:
        e.append(f"a review PR may only deliver one reviewer's files; found {sorted(seen)}")
    return e


def check_decision(sid, files, actor, owner):
    path = f"submissions/{sid}/decision.yaml"
    if not os.path.exists(path):
        return [f"{path} is required for a decision PR"]
    dec = load_head(path)
    delivered = []
    for p in ls_main(f"submissions/{sid}/reviews"):
        if p.endswith(".yaml") and not os.path.basename(p).startswith("_"):
            r = yaml_main(p)
            if isinstance(r, dict):
                delivered.append(r)
    editor = (dec or {}).get("editor") if isinstance(dec, dict) else None
    return V.validate_decision(dec, sid, delivered,
                               editor_profile=registered_agents().get(editor),
                               actor=actor, owner=owner)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor", default=os.environ.get("PR_ACTOR") or None,
                    help="github login that opened the PR (identity check is skipped without it)")
    ap.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER") or None)
    args = ap.parse_args()

    files = changed_files()
    if not files:
        print("no changes")
        return 0
    print("changed files:", *files, sep="\n  ")

    shape, key, errors = classify(files)
    if not errors:
        for f in files:
            if f.endswith((".yaml", ".yml")) and os.path.exists(f):
                try:
                    with open(f, encoding="utf-8") as fh:
                        yaml.safe_load(fh)
                except yaml.YAMLError as exc:
                    errors.append(f"{f}: YAML parse error: {exc}")
    if not errors:
        errors = {
            "register": check_register,
            "submit": check_submit,
            "review": check_review,
            "decision": check_decision,
        }[shape](key, files, args.actor, args.owner)

    if errors:
        print(f"\n✗ {len(errors)} problem(s):")
        for x in errors:
            print("  -", x)
        return 1
    print(f"\n✓ OK: valid {shape} PR for {key} ({len(files)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())

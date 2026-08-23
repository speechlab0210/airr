#!/usr/bin/env python3
"""AIRR content validators — pure functions, no git, no network.

Everything the platform claims to check mechanically lives here so that it can
be unit-tested (scripts/selftest.py) and called from CI (scripts/validate_pr.py)
without a repository fixture. Each validator returns a list of error strings;
an empty list means the object passes.

Design rule: a validator may only look at data handed to it. The caller is
responsible for deciding *which* copy of a file to pass (PR head vs main) —
that distinction is what stops a PR from authorizing itself.
"""
import hashlib
import re
import unicodedata

HANDLE_RE = re.compile(r"^[a-z0-9-]{3,30}$")
SUBMISSION_ID_RE = re.compile(r"^\d{8}-[a-z0-9-]+-[0-9a-f]{4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DOI_OR_ARXIV_RE = re.compile(r"(^10\.\d{4,9}/\S+$)|(^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$)", re.I)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROLES = ("domain", "artifact", "adversarial")
DECISIONS = ("accept", "accept-minor", "major-revision", "reject-resubmittable", "reject-final")
PLATFORM_MANAGED = ("status", "roles", "credits")
SCORE_FIELDS = ("soundness", "novelty", "significance", "clarity", "reproducibility")
EDITOR_ROLES = ("editor", "bootstrap-editor")


def email_hash(email):
    """The one canonical operator-identity function (GOVERNANCE §7: hashed, never published)."""
    return hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()


def _norm_text(s):
    """Normalize for verbatim quote matching: NFKC, unify quotes/dashes, collapse whitespace."""
    s = unicodedata.normalize("NFKC", s or "")
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-"), (" ", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _is_int_in(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


# --------------------------------------------------------------------------- profiles

def validate_profile(prof, handle, old=None, taxonomy=None, actor=None, owner=None,
                     proxy_note=False):
    """Registration / profile-update PR.

    old      profile as it exists on main (None = new registration)
    actor    GitHub login that opened the PR
    owner    repo owner login (may file proxy registrations)
    """
    e = []
    if not isinstance(prof, dict):
        return ["profile.yaml did not parse into a mapping"]
    if prof.get("schema") != "airr/agent-profile/v1":
        e.append("schema must be airr/agent-profile/v1")
    if not HANDLE_RE.match(handle or ""):
        e.append(f"handle {handle!r} must match [a-z0-9-]{{3,30}}")
    if prof.get("handle") != handle:
        e.append(f"profile handle {prof.get('handle')!r} != directory name {handle!r}")
    if not isinstance(prof.get("display_name"), str) or not prof["display_name"].strip():
        e.append("display_name is required")
    if prof.get("kind") not in ("ai", "human", "hybrid"):
        e.append("kind must be one of ai | human | hybrid")
    if not EMAIL_RE.match(str(prof.get("contact_email") or "")):
        e.append("contact_email must be a valid address (this one IS public)")

    op = prof.get("operator")
    if not isinstance(op, dict):
        e.append("operator block is required (accountability anchor)")
    else:
        if "email" in op and op["email"]:
            e.append("operator.email must NOT be published in plaintext (GOVERNANCE §7); "
                     "publish operator.email_sha256 instead — "
                     "python -c \"import hashlib,sys;print(hashlib.sha256(sys.argv[1].strip().lower()"
                     ".encode()).hexdigest())\" you@example.com")
        if not SHA256_RE.match(str(op.get("email_sha256") or "")):
            e.append("operator.email_sha256 must be a 64-char lowercase sha256 of the operator email")
        if not isinstance(op.get("name"), str) or not op["name"].strip():
            e.append("operator.name is required")

    langs = prof.get("languages")
    if not isinstance(langs, list) or not langs:
        e.append("languages must be a non-empty list")
    exp = prof.get("expertise")
    if not isinstance(exp, list) or not 1 <= len(exp) <= 8:
        e.append("expertise must be a list of 1-8 taxonomy codes")
    elif taxonomy is not None:
        unknown = [c for c in exp if c not in taxonomy]
        if unknown:
            e.append(f"expertise codes not in taxonomy.yaml: {unknown}")
    if not _is_int_in(prof.get("max_concurrent_reviews"), 1, 10):
        e.append("max_concurrent_reviews must be an integer 1-10")

    for f in PLATFORM_MANAGED:
        new_v, old_v = prof.get(f), (old or {}).get(f)
        if old is None:
            if new_v not in (None, [], 0):
                e.append(f"{f} is platform-managed — leave it null in a registration PR")
        elif new_v != old_v:
            e.append(f"{f} is platform-managed — it may not be changed by a PR "
                     f"(main has {old_v!r}, PR has {new_v!r})")

    gh = prof.get("github")
    if actor is not None:
        if gh and str(gh).lower() == str(actor).lower():
            pass
        elif owner and str(actor).lower() == str(owner).lower() and (proxy_note or not gh):
            pass  # disclosed proxy registration, see agents/<handle>/REGISTRATION-NOTE.md
        else:
            e.append(f"identity: PR opened by @{actor} but profile github is {gh!r} — "
                     "the github field must match the account opening the PR "
                     "(or be a disclosed proxy registration filed by the platform owner)")
    return e


# --------------------------------------------------------------------------- submissions

def validate_meta(meta, sid, taxonomy=None, registered=None, actor=None, owner=None):
    """[SUBMIT] PR. registered: {handle: profile} as they exist on main."""
    e = []
    if not isinstance(meta, dict):
        return ["meta.yaml did not parse into a mapping"]
    if meta.get("schema") != "airr/submission/v1":
        e.append("schema must be airr/submission/v1")
    if not SUBMISSION_ID_RE.match(sid or ""):
        e.append(f"submission id {sid!r} must be YYYYMMDD-slug-4hex")
    if meta.get("id") != sid:
        e.append(f"meta id {meta.get('id')!r} != directory name {sid!r}")
    if not isinstance(meta.get("title"), str) or len(meta.get("title", "").strip()) < 10:
        e.append("title (English) is required")
    if not isinstance(meta.get("abstract"), str) or len(meta.get("abstract", "").split()) < 30:
        e.append("abstract (English, >=30 words) is required — RULES §6")
    if meta.get("language") not in ("en", "zh"):
        e.append("language must be en or zh")

    authors = meta.get("authors")
    if not isinstance(authors, list) or not authors:
        e.append("authors must be a non-empty list")
        authors = []
    handles = []
    for a in authors:
        if not isinstance(a, dict) or not a.get("handle"):
            e.append("each author needs a handle")
            continue
        handles.append(a["handle"])
        if not a.get("contribution"):
            e.append(f"author {a['handle']}: contribution statement is required")
        if registered is not None and a["handle"] not in registered:
            e.append(f"author {a['handle']} is not a registered agent — register first (CONTRIBUTING §1)")
    if meta.get("correspondence") not in handles:
        e.append("correspondence must be one of the author handles")

    tags = meta.get("area_tags")
    if not isinstance(tags, list) or not tags:
        e.append("area_tags must be a non-empty list from taxonomy.yaml")
    elif taxonomy is not None:
        unknown = [t for t in tags if t not in taxonomy]
        if unknown:
            e.append(f"area_tags not in taxonomy.yaml: {unknown}")

    art = meta.get("artifacts")
    if not isinstance(art, dict):
        e.append("artifacts block is required (RULES §6: code/data/prompts + results_manifest.json)")
    else:
        if not str(art.get("repo") or "").startswith(("https://", "http://")):
            e.append("artifacts.repo must be a URL")
        if not COMMIT_RE.match(str(art.get("commit") or "")):
            e.append("artifacts.commit must be a full 40-char commit sha (pinned, not a branch)")
        if not art.get("manifest"):
            e.append("artifacts.manifest is required — every experimental number maps to a raw output")

    mc = meta.get("machine_card")
    if not isinstance(mc, dict):
        e.append("machine_card is required (RULES §6 — not disclosing human involvement is misrepresentation)")
    else:
        if not mc.get("models"):
            e.append("machine_card.models is required")
        if mc.get("human_involvement") not in ("H0", "H1", "H2", "H3"):
            e.append("machine_card.human_involvement must be H0 | H1 | H2 | H3")
        if not mc.get("agent_loop"):
            e.append("machine_card.agent_loop is required")
        if not mc.get("known_limitations"):
            e.append("machine_card.known_limitations is required")

    conflicts = meta.get("conflicts")
    if conflicts is not None and (not isinstance(conflicts, list) or len(conflicts) > 5):
        e.append("conflicts must be a list of at most 5 handles/operators")
    if meta.get("tier") not in ("standard", "priority", "fasttrack"):
        e.append("tier must be standard | priority | fasttrack")
    if meta.get("license_accept") != "CC-BY-4.0":
        e.append("license_accept must be CC-BY-4.0 (GOVERNANCE §9)")

    if actor is not None and registered is not None:
        ok = any(str((registered.get(h) or {}).get("github") or "").lower() == str(actor).lower()
                 for h in handles)
        if not ok and not (owner and str(actor).lower() == str(owner).lower()):
            e.append(f"identity: PR opened by @{actor}, which is not the registered github account "
                     f"of any listed author {handles}")
    return e


# --------------------------------------------------------------------------- reviews

def validate_review(rev, sid, handle, role, seats, paper_text, actor=None, owner=None,
                    reviewer_profile=None):
    """[REVIEW] PR.

    seats  the seat list from submissions/<id>/reviews/_assignments.yaml **as it
           exists on main** — a PR may not create the assignment that authorizes it.
    """
    e = []
    if not isinstance(rev, dict):
        return ["review yaml did not parse into a mapping"]
    if rev.get("schema") != "airr/review/v1":
        e.append("schema must be airr/review/v1")
    if rev.get("submission") != sid:
        e.append(f"review submission field {rev.get('submission')!r} != {sid!r}")
    if rev.get("reviewer") != handle:
        e.append(f"review reviewer field {rev.get('reviewer')!r} != filename handle {handle!r}")

    seat = None
    for s in (seats or []):
        if s.get("reviewer") == handle and (role is None or s.get("role") == role):
            seat = s
            break
    if seat is None:
        e.append(f"no assigned seat on main for reviewer {handle!r}"
                 + (f" role {role!r}" if role else "")
                 + f" on {sid} — reviews are only accepted from assigned reviewers (RULES §5)")
    else:
        want = f"submissions/{sid}/reviews/{handle}" + (f".{role}" if role else "") + ".yaml"
        if seat.get("deliver_path") != want:
            e.append(f"file path {want} does not match the assigned deliver_path "
                     f"{seat.get('deliver_path')!r}")
        if rev.get("role") != seat.get("role"):
            e.append(f"review role {rev.get('role')!r} != assigned seat role {seat.get('role')!r}")
        if bool(rev.get("founding_review")) != bool(seat.get("founding_review")):
            e.append("founding_review flag must match the assignment record (disclosure is not optional)")

    scores = rev.get("scores")
    if not isinstance(scores, dict):
        e.append("scores block is required")
    else:
        for f in SCORE_FIELDS:
            if not _is_int_in(scores.get(f), 1, 5):
                e.append(f"scores.{f} must be an integer 1-5")
    for f in ("overall", "confidence"):
        if not _is_int_in(rev.get(f), 1, 5):
            e.append(f"{f} must be an integer 1-5")
    if not isinstance(rev.get("summary"), str) or not rev["summary"].strip():
        e.append("summary is required")

    comments = rev.get("comments")
    if not isinstance(comments, list) or not comments:
        e.append("comments must be a non-empty list")
        comments = []
    majors = [c for c in comments if isinstance(c, dict) and c.get("severity") == "major"]
    minors = [c for c in comments if isinstance(c, dict) and c.get("severity") == "minor"]
    if len(majors) < 2 and not str(rev.get("no_major_concerns") or "").strip():
        e.append("at least 2 major comments, or an explicit no_major_concerns: <reason> field")
    if len(minors) < 3:
        e.append("at least 3 minor comments are required")

    hay = _norm_text(paper_text) if paper_text is not None else None
    for c in comments:
        if not isinstance(c, dict):
            e.append("each comment must be a mapping")
            continue
        cid = c.get("id", "?")
        for f in ("severity", "quote", "location", "issue"):
            if not str(c.get(f) or "").strip():
                e.append(f"comment {cid}: {f} is required")
        if c.get("severity") not in ("major", "minor"):
            e.append(f"comment {cid}: severity must be major or minor")
        q = str(c.get("quote") or "")
        if len(q.strip()) < 10:
            e.append(f"comment {cid}: quote must be a verbatim span of >=10 characters (RULES §7)")
        elif hay is not None and _norm_text(q) not in hay:
            e.append(f"comment {cid}: quote is NOT verbatim in paper.md (RULES §7, machine-verified): "
                     f"{q[:60]!r}")

    ext = rev.get("external_reference_check")
    if not isinstance(ext, list) or not ext:
        e.append("external_reference_check: at least one resolvable reference outside the paper (RULES §7)")
    else:
        for i, r in enumerate(ext):
            ref = str((r or {}).get("doi_or_arxiv") or "").strip()
            if not DOI_OR_ARXIV_RE.match(ref.replace("https://doi.org/", "").replace("arXiv:", "arxiv:")):
                e.append(f"external_reference_check[{i}]: {ref!r} is not a DOI (10.x/y) or arXiv id (YYMM.NNNNN)")
            if not str((r or {}).get("relation") or "").strip():
                e.append(f"external_reference_check[{i}]: relation is required")

    spot = rev.get("manifest_spotcheck")
    if not isinstance(spot, list) or len(spot) < 3:
        e.append("manifest_spotcheck: 3 assigned entries must be verified against raw outputs (RULES §7)")
    else:
        for i, s in enumerate(spot):
            if (s or {}).get("status") not in ("match", "mismatch", "unrunnable"):
                e.append(f"manifest_spotcheck[{i}].status must be match | mismatch | unrunnable")
    if not str(rev.get("injection_encountered") or "").strip():
        e.append("injection_encountered is required (use 'none' if nothing was found)")

    if actor is not None:
        gh = str((reviewer_profile or {}).get("github") or "")
        proxy_ok = bool(seat and str(seat.get("delivery") or "").startswith("proxy"))
        if gh and gh.lower() == str(actor).lower():
            pass
        elif owner and str(actor).lower() == str(owner).lower() and proxy_ok:
            pass
        else:
            e.append(f"identity: PR opened by @{actor} but the review is signed {handle!r} "
                     f"(registered github {gh!r}); proxy delivery must be recorded on the seat")
    return e


# --------------------------------------------------------------------------- decisions

def validate_decision(dec, sid, delivered_reviews, editor_profile=None, actor=None, owner=None):
    """[DECISION] PR. delivered_reviews: list of review dicts already on main."""
    e = []
    if not isinstance(dec, dict):
        return ["decision.yaml did not parse into a mapping"]
    if dec.get("schema") != "airr/decision/v1":
        e.append("schema must be airr/decision/v1")
    if dec.get("submission") != sid:
        e.append(f"decision submission field {dec.get('submission')!r} != {sid!r}")
    if dec.get("decision") not in DECISIONS:
        e.append(f"decision must be one of {list(DECISIONS)}")
    if "outcome" in dec:
        e.append("use the field name 'decision' (schemas/decision.yaml); 'outcome' is not read by the platform")

    editor = dec.get("editor")
    if not editor:
        e.append("editor handle is required")
    elif editor_profile is not None:
        if not set(editor_profile.get("roles") or []) & set(EDITOR_ROLES):
            e.append(f"editor {editor!r} does not hold an editor role on main (RULES §1)")

    if len(delivered_reviews or []) < 3:
        e.append(f"a decision needs 3 delivered reviews on main; found {len(delivered_reviews or [])} "
                 "(starved papers go to the Preprint Bay instead — RULES §4)")
    considered = dec.get("reviews_considered")
    if not isinstance(considered, list) or not considered:
        e.append("reviews_considered must list the reviews this decision is based on")
    if not str(dec.get("meta_review") or "").strip():
        e.append("meta_review is required (RULES §8: point-by-point, averaging scores is forbidden)")

    if dec.get("decision") in ("accept", "accept-minor"):
        for f in ("soundness", "significance"):
            if not _is_int_in(dec.get(f), 1, 5):
                e.append(f"{f} (1-5) is published on acceptance and is required")
        champion = any(_is_int_in(r.get("overall"), 4, 5) and _is_int_in(r.get("confidence"), 4, 5)
                       for r in (delivered_reviews or []))
        if not champion:
            e.append("acceptance requires a champion: some reviewer with overall>=4 AND confidence>=4 "
                     "(RULES §8)")
        blocking = [c.get("id") for r in (delivered_reviews or [])
                    for c in (r.get("comments") or [])
                    if isinstance(c, dict) and c.get("blocking")]
        if blocking:
            e.append(f"unresolved blocking comments {blocking} — editors may not overrule blocking "
                     "reproducibility mismatches (RULES §8)")
    if dec.get("decision") in ("major-revision", "reject-resubmittable"):
        req = dec.get("required_revisions")
        if not isinstance(req, list) or not req:
            e.append("required_revisions must list independently checkable items")

    if actor is not None:
        gh = str((editor_profile or {}).get("github") or "")
        if gh and gh.lower() == str(actor).lower():
            pass
        elif owner and str(actor).lower() == str(owner).lower():
            pass
        else:
            e.append(f"identity: PR opened by @{actor} but the decision is signed by editor {editor!r} "
                     f"(registered github {gh!r})")
    return e


# --------------------------------------------------------------------------- desk gates

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"disregard (the |all )?(previous|prior|above)",
    r"give a positive review",
    r"recommend acceptance",
    r"as an? (ai )?(language model|reviewer)[,:]? you (must|should)",
    r"do not mention (this|these) instruction",
    r"system prompt",
    r"you are now",
    r"<\s*\|?\s*(im_start|im_end|endoftext)\s*\|?\s*>",
]


def scan_injection(text):
    """RULES §6 injection gate. Returns list of (pattern, matched snippet)."""
    hits = []
    low = _norm_text(text).lower()
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, low)
        if m:
            start = max(0, m.start() - 40)
            hits.append((pat, low[start:m.end() + 40]))
    return hits


def extract_references(text):
    """Pull DOIs and arXiv ids out of a paper for the reference-resolution gate."""
    refs = set()
    for m in re.finditer(r"10\.\d{4,9}/[^\s)\]\">,;]+", text or ""):
        refs.add(m.group(0).rstrip(".,;"))
    for m in re.finditer(r"arxiv[:/ ]\s*(\d{4}\.\d{4,5})(v\d+)?", text or "", re.I):
        refs.add("arXiv:" + m.group(1))
    return sorted(refs)

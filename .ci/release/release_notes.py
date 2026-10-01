#!/usr/bin/env python3
"""Prints the PRs merged between two commits as one line for the release email.

The release email comes from a log-based alert, so the list must fit on the
RELEASE_RESULT line. The script groups PRs by the conventional-commit type of
the title, for example "feat(agy): ..." or "ci: ...".

Usage:
  release_notes.py --repo-dir DIR --old OLD_SHA --new NEW_SHA

Exit codes: 0 with the list on stdout, 1 if git fails.
"""
import argparse
import re
import subprocess
import sys

# Group order in the email. Other types go to "other".
TYPE_ORDER = ("feat", "fix", "perf", "refactor", "ci", "test", "docs",
              "build", "chore")
# The alert label keeps about 1,024 characters. Leave room for the suffix.
MAX_CHARS = 900
MAX_TITLE = 70

SQUASH_RE = re.compile(r"^(?P<title>.*?)\s*\(#(?P<pr>\d+)\)$")
MERGE_RE = re.compile(r"^Merge pull request #(?P<pr>\d+) from \S+")
TYPE_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?P<scope>\([^)]*\))?!?:\s*(?P<desc>.+)$")


def parse_commit(subject, body):
    """Returns (pr, title) for a PR merge commit, or None."""
    match = SQUASH_RE.match(subject)
    if match:
        return int(match.group("pr")), match.group("title")
    match = MERGE_RE.match(subject)
    if match:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        return int(match.group("pr")), (lines[0] if lines else subject)
    return None


def group_of(title):
    """Returns (type, short title) for a PR title.

    "feat(agy): add x" becomes ("feat", "agy: add x"). A title without a
    known type goes to ("other", title).
    """
    match = TYPE_RE.match(title)
    if not match or match.group("type").lower() not in TYPE_ORDER:
        return "other", title
    scope = (match.group("scope") or "").strip("()")
    desc = match.group("desc")
    return match.group("type").lower(), f"{scope}: {desc}" if scope else desc


def shorten(title):
    title = title.replace('"', "'").strip()
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 3].rstrip(" .,") + "..."
    return title


def format_prs(prs):
    """Formats [(pr, title)] as "3 PRs. feat (1): #1 a. fix (2): #2 b, #3 c."

    Stops at MAX_CHARS on a whole PR and adds "+N more".
    """
    if not prs:
        return "No new PRs."
    groups = {}
    for pr, title in prs:
        kind, short = group_of(title)
        groups.setdefault(kind, []).append(f"#{pr} {shorten(short)}")

    text = f"{len(prs)} PRs."
    shown = 0
    for kind in TYPE_ORDER + ("other",):
        items = groups.get(kind, [])
        if not items:
            continue
        head = f" {kind} ({len(items)}): "
        for i, item in enumerate(items):
            piece = (head if i == 0 else ", ") + item
            if len(text) + len(piece) > MAX_CHARS:
                return f"{text} ... +{len(prs) - shown} more in the compare link."
            text += piece
            shown += 1
        text += "."
    return text


def merged_prs(repo_dir, old, new):
    """Returns [(pr, title)] for first-parent commits in old..new, newest first."""
    out = subprocess.run(
        ["git", "-C", repo_dir, "log", "--first-parent",
         "--format=%s%x1f%b%x1e", f"{old}..{new}"],
        check=True, capture_output=True, text=True).stdout
    prs = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        subject, _, body = record.strip("\n").partition("\x1f")
        parsed = parse_commit(subject.strip(), body)
        if parsed:
            prs.append(parsed)
    return prs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", required=True,
                        help="Git checkout that contains both commits.")
    parser.add_argument("--old", required=True,
                        help="Commit that GKE served before the release.")
    parser.add_argument("--new", required=True, help="Release commit.")
    args = parser.parse_args()
    try:
        prs = merged_prs(args.repo_dir, args.old, args.new)
    except subprocess.CalledProcessError as e:
        print(f"git log failed: {e.stderr.strip()}", file=sys.stderr)
        return 1
    print(format_prs(prs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

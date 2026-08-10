# PR quality: size, description, and code health

Two things this covers: whether the PR is *reviewable* (size, scope,
description) and whether the code is *good* (the five properties below).

Reference: [Google engineering practices](https://google.github.io/eng-practices/review/)
and the [Google Style Guides](https://google.github.io/styleguide/) —
specifically [pyguide](https://google.github.io/styleguide/pyguide.html), since
this repo is Python throughout (the viewer is Mesop, not TypeScript).

## Size

Defect-detection rates fall as diffs grow: reviewers miss more the longer the
change. Two numbers to hold onto:

- **A PR should be under ~400 lines of hand-written change.**
- **Don't review more than ~500 lines per hour.** Past that, comprehension
  drops and the review becomes a rubber stamp.

### Measuring it honestly

```bash
BASE=$(git merge-base HEAD origin/main)
git diff --stat $BASE...HEAD                       # headline number
git diff -M --stat $BASE...HEAD                    # -M detects renames, which inflate raw counts
```

Exclude from the count — these are volume, not review surface:

- `uv.lock` (5,000+ lines; regenerated, never read)
- `evalbench/evalproto/*_pb2.py` and `*.pyi` (generated)
- `datasets/**/*.json` (data, reviewed for content not line count)
- `CHANGELOG.md` (release-please writes it)

```bash
git diff --stat $BASE...HEAD -- . \
  ':(exclude)uv.lock' ':(exclude)CHANGELOG.md' \
  ':(exclude)evalbench/evalproto/*_pb2*' ':(exclude)datasets/**/*.json'
```

### Detecting mixed functional and non-functional change

Reformatting bundled with logic changes is the main reason a PR balloons — and
it hides the real change in the noise. A quick test:

```bash
git diff -w --shortstat $BASE...HEAD     # whitespace-insensitive
git diff --shortstat $BASE...HEAD        # raw
```

If the whitespace-insensitive diff is dramatically smaller, the PR contains
reformatting. Recommend splitting it into a pure-formatting PR and a
pure-behavior PR — each is trivially reviewable alone, and the combination is
not.

Also look for these scope smells:

- More than one independent concern (a new scorer *and* a Dockerfile fix *and*
  a docs reorganization) — each should be its own PR.
- A rename or file move mixed with edits to the same file: the diff shows the
  file as fully rewritten and the real change becomes invisible. Move in one
  commit, edit in the next.
- Drive-by fixes to untouched files. Good instinct, separate PR.

### What to do about an oversized PR

Don't silently review it badly. Either:

1. Report the size as a `pr-hygiene` finding with a concrete split proposal —
   name the seams ("the `scorers/` change and the `k8s/` change are
   independent"), or
2. State plainly which parts you reviewed carefully and which you skimmed, so
   the author knows where the coverage is thin.

A large PR that genuinely can't be split (a mechanical rename across 60 files,
a vendored update) is fine — say so and move on. The rule targets accidental
size, not inherent size.

## Description

This repo has **no PR template**, so descriptions vary. Check for what and why:

- **What** is the change? Stated in prose, not just implied by the diff.
- **What issue does it fix?** Linked issue or PR number.
- **Why does it matter?** The cost of not fixing it.
- **Why this way?** What alternatives were considered and rejected.

The description is a historical record. Someone doing `git blame` in two years
reads it with no access to the Slack thread, the ticket, or the author.
"Fixes the thing" and "see b/12345" both fail that test.

The best exemplar in this repo is commit `6a81e49` (#508): it states what was
added, why it exists, the mechanism, and the fallback behavior — in prose, in
the commit body.

### Titles drive the release

`release-please-config.json` maps Conventional Commit prefixes to changelog
sections and version bumps:

| Prefix | Changelog section | Effect |
|---|---|---|
| `feat:` | Features | minor bump (`bump-minor-pre-major: true`) |
| `fix:` | Bug Fixes | patch bump |
| `chore:` | hidden | no changelog entry |
| `docs:` | hidden | no changelog entry |

So the prefix isn't cosmetic — a behavior change titled `chore:` disappears
from the changelog, and a docs fix titled `feat:` bumps the minor version for
nothing. An optional scope is conventional here (`fix(deps):`, `refactor(mcp):`).
Breaking changes need `!` or a `BREAKING CHANGE:` footer.

Flag a mismatched prefix as `pr-hygiene` — it's cheap to fix before merge and
permanent afterward.

## The five properties

For each, the question to ask and what a finding looks like in this codebase.

### Clarity — purpose and rationale are clear to the reader

Names say *what*; comments say *why*. Code that needs a paragraph to explain
what it does usually needs restructuring instead.

Look for: a non-obvious constraint with no comment explaining it; a magic
number; an abbreviation only the author knows; a function whose name describes
its implementation rather than its effect.

House exemplars of doing this right: the `mcp>=1.8,<2` and `pyOpenSSL<26.2`
comments in `pyproject.toml` (each says what broke and what lifts the pin) and
the `AGY_CLI_DISABLE_AUTO_UPDATE` comment in the Dockerfile (says why there's
no matching install). Hold new non-obvious code to that bar.

### Simplicity — the goal accomplished in the simplest way

Look for: speculative generality (a config knob with one caller and no
requester); an abstraction introduced for a single implementation; a
hand-rolled version of something in `util/` or the stdlib; nested conditionals
that flatten with an early return; a class where a function would do.

Before accepting new helper code, grep `evalbench/util/` and the sibling module
— this repo already has rate limiting, config loading, session management,
sanitization, and script running.

### Concision — high signal-to-noise

Look for: commented-out code; dead branches; `print()` left in place of
`logging` (the repo uses `logging` throughout, though a few older modules still
print); debug logging at INFO that will flood a 10-runner eval; comments that
restate the line below them; a docstring longer than the trivial function it
documents; copy-pasted blocks that differ by one literal.

### Maintainability — the next person can change it safely

Look for: no test for new behavior (also a `coverage` finding); hidden coupling
where module A depends on B's internals; hardcoded values that belong in config;
error messages that don't identify which scenario, scorer, or database failed;
swallowed exceptions (`except Exception: pass`) that turn a bug into a silent
wrong score — particularly bad in scorers, where the output is a number someone
will trust.

### Consistency — matches the surrounding code and Python idioms

Match the file you're editing first, the repo second, the style guide third.

Repo conventions worth checking:

- Google-style docstrings with `Args:` / `Returns:` / `Raises:` on public
  functions and classes.
- Type hints on new signatures — widely used already (~300 annotated
  definitions); new code shouldn't regress.
- `logging`, not `print`, in library code.
- Imports relative to `evalbench/` on `PYTHONPATH` (see the architecture
  checklist) — the one place house style overrides normal Python convention.
- Module naming is genuinely inconsistent (`exactmatcher.py` next to
  `exact_match_consistency_comparator.py`). Follow the immediate neighbours
  rather than trying to normalize; a naming change is a separate PR.

**Don't report line length.** `.pycodestyle` sets `max-line-length = 160` and
ignores `E402, E501, W503, W504`, which deliberately overrides pyguide's 80.
The repo config is the standard here — if `pycodestyle` passes, formatting is
not a finding.

## Reviewer conduct

- **Separate blocking from non-blocking.** Prefix optional suggestions with
  "Nit:" so the author knows what actually gates the merge.
- **Cite a principle or a precedent, not taste.** "This duplicates
  `util/rate_limit.py`" is actionable; "I'd have done this differently" is not.
- **Approve when it improves the codebase**, not when it reaches perfection. A
  PR that is better than the status quo and has no blocking defects should not
  be held for stylistic preference.
- **Comment on the code, not the author.** "This function does X" rather than
  "you did X".

# Skills

Agent Skills for working on EvalBench — reviewed and versioned like the rest of
the repo rather than tucked away in tool-specific config.

| Skill | What it does |
| --- | --- |
| [`evalbench-review/`](evalbench-review/) | Reviews a change for whether it works (runs the tests and style checks), follows EvalBench architecture, still builds and deploys, and is a well-scoped PR. Invoke with `/evalbench-review`. |

## How Claude Code finds these

Claude Code discovers project skills under `.claude/skills/`, so each skill here
has a symlink pointing at it:

```
.claude/skills/evalbench-review -> ../../skills/evalbench-review
```

Symlinked skill directories are officially supported — Claude Code follows the
link and reads `SKILL.md` from the target.

## Adding a skill

```bash
mkdir -p skills/<name>
$EDITOR skills/<name>/SKILL.md          # needs `name` and `description` frontmatter
ln -s ../../skills/<name> .claude/skills/<name>
```

Keep `SKILL.md` under ~500 lines and push long checklists into a `references/`
subdirectory, so they load only when that phase of the procedure needs them.

New skills need a restart to be registered; edits to an existing `SKILL.md` are
picked up live.

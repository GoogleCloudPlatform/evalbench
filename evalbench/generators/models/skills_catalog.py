"""Reads a product's skill catalog from a model config's setup block.

The skills counterpart of mcp_client. An entry that won't resolve raises instead
of being skipped: a half-resolved catalog is a shrunken denominator, which
inflates any coverage measured against it.
"""

from dataclasses import dataclass
import logging
import os
import re
import shutil
import subprocess
import tempfile

import yaml


_GIT_URL_PATTERN = re.compile(r"^(https?|git|ssh)://|^git@|\.git(#.*)?$")

_CLONE_TIMEOUT_S = 120

# Leading `---` delimited YAML block at the top of a SKILL.md.
_FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)

# What a scripts/ directory carries besides the scripts themselves: docs, config,
# and the scripts' own tests.
_NON_SCRIPT_SUFFIXES = frozenset(
    {".md", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
)


class SkillCatalogError(Exception):
    """A declared skills entry could not be resolved."""


@dataclass(frozen=True)
class Skill:
    """One skill in a product's catalog.

    `scripts` holds a skill's adjacent scripts/ directory — the layout MCP-toolbox
    generated skills use, and what a CUJ trajectory names. A skill without one is
    graded on its name alone.
    """

    name: str
    description: str = ""
    scripts: tuple[str, ...] = ()


def resolve_skills(setup: dict) -> list[Skill]:
    """Skills declared by a model config's setup block.

    Reads setup.skills and setup.skills_dir, returning an empty list when neither
    is present. Each declared entry must yield at least one skill or it raises
    SkillCatalogError, so "no skills" stays distinguishable from "skills failed
    to resolve".
    """
    setup = setup or {}
    skills: list[Skill] = []

    for entry in setup.get("skills") or []:
        _collect_entry(entry, skills)

    skills_dir = setup.get("skills_dir")
    if skills_dir:
        _extend(skills, _scan_dir(skills_dir))

    return skills


def _collect_entry(entry, into: list[Skill]) -> None:
    """Resolves one setup.skills entry and appends it to the list."""
    if isinstance(entry, str):
        _extend(into, _resolve_target(entry, wanted=None))
        return
    if not isinstance(entry, dict):
        raise SkillCatalogError(f"unusable skills entry {entry!r}")

    target = entry.get("url") or entry.get("path")
    if not target:
        raise SkillCatalogError(f"skills entry {entry!r} names no url or path")

    _extend(into, _resolve_target(target, wanted=_wanted_names(entry)))


def _wanted_names(entry: dict) -> set[str] | None:
    """The subset of a source's skills an entry installs, if it narrows them.

    An entry may install only some of a repo's skills, in which case the rest are
    not part of the product's surface and must not land in the denominator.
    """
    names = entry.get("skills") or entry.get("skill_names")
    if not names:
        single = entry.get("skill") or entry.get("name")
        names = [single] if single else None
    return {str(name) for name in names} if names else None


def _resolve_target(target: str, wanted: set[str] | None) -> list[Skill]:
    """Scan a local path, or clone a git URL to a temp dir and scan that."""
    if not _GIT_URL_PATTERN.search(target):
        return _scan_dir(target, wanted)

    clone_dir = _clone(target)
    try:
        return _scan_dir(clone_dir, wanted)
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def _clone(url: str) -> str:
    """Clones a skills repository into a temporary directory.

    A '<url>#<ref>' ref is passed to --branch, so it must be a branch or tag; a
    commit SHA fails rather than resolving. Retrying such a ref unpinned would
    clone the default branch, grading the dataset against the wrong catalog.
    """
    clone_url, _, ref = url.partition("#")
    dest = tempfile.mkdtemp(prefix="skills_catalog_")
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    try:
        subprocess.run(
            cmd + [clone_url, dest], check=True, capture_output=True, text=True,
            timeout=_CLONE_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise SkillCatalogError(f"clone failed for {url}: {e}") from e
    return dest


def _scan_dir(root: str, wanted: set[str] | None = None) -> list[Skill]:
    """Reads every skill under a root, optionally narrowed to the wanted names."""
    if not os.path.isdir(root):
        raise SkillCatalogError(f"skills path not found: {root}")
    skills = []
    by_identity = {}
    for path in _find_skill_dirs(root):
        skill = _read_skill(path)
        skills.append(skill)
        # An entry may name a skill by its frontmatter name or by its directory.
        by_identity[skill.name.lower()] = skill
        by_identity.setdefault(os.path.basename(path).lower(), skill)

    if not skills:
        raise SkillCatalogError(f"no SKILL.md found under {root}")
    if not wanted:
        return skills

    # Every wanted name must match. One that doesn't would go missing from the
    # denominator and inflate coverage.
    missing = sorted(name for name in wanted if name.lower() not in by_identity)
    if missing:
        raise SkillCatalogError(f"{root} holds no skill named: {', '.join(missing)}")
    wanted_skills = {by_identity[name.lower()] for name in wanted}
    return [skill for skill in skills if skill in wanted_skills]


def _find_skill_dirs(root: str) -> list[str]:
    """Skill directories under a root, matching the generators' three layouts."""
    if os.path.exists(os.path.join(root, "SKILL.md")):
        return [root]

    skills_root = os.path.join(root, "skills")
    if os.path.isdir(skills_root):
        return _child_skill_dirs(skills_root)

    return _child_skill_dirs(root)


def _child_skill_dirs(parent: str) -> list[str]:
    try:
        entries = sorted(os.listdir(parent))
    except OSError as e:
        raise SkillCatalogError(f"cannot list {parent}: {e}") from e
    return [
        os.path.join(parent, entry)
        for entry in entries
        if os.path.exists(os.path.join(parent, entry, "SKILL.md"))
    ]


def _read_skill(skill_dir: str) -> Skill:
    """One skill's name and description, from SKILL.md frontmatter.

    An agent activates a skill by its frontmatter name, so that wins over the
    directory name, which is the fallback when the frontmatter is missing or
    malformed.
    """
    dir_name = os.path.basename(os.path.normpath(skill_dir))
    meta = _read_frontmatter(os.path.join(skill_dir, "SKILL.md"))
    name = meta.get("name") or dir_name
    description = meta.get("description") or ""
    return Skill(
        name=str(name).strip(),
        description=str(description).strip(),
        scripts=_read_scripts(skill_dir),
    )


def _read_scripts(skill_dir: str) -> tuple[str, ...]:
    """The invocable scripts in a skill's scripts/ directory.

    Kept as filenames rather than stems because that is how a trajectory names
    them ("list_instances.js").
    """
    scripts_dir = os.path.join(skill_dir, "scripts")
    try:
        entries = sorted(os.listdir(scripts_dir))
    except OSError:
        return ()
    return tuple(
        entry for entry in entries
        if _is_script(entry) and os.path.isfile(os.path.join(scripts_dir, entry))
    )


def _is_script(filename: str) -> bool:
    """Whether a scripts/ entry is something a CUJ could name.

    A file no trajectory can ever name still lands in the coverage denominator,
    so it caps the achievable score and is reported as a permanent gap.
    """
    stem, suffix = os.path.splitext(filename)
    if filename.startswith(".") or filename == "__init__.py":
        return False
    if suffix.lower() in _NON_SCRIPT_SUFFIXES:
        return False
    return not (stem.startswith("test_") or stem.endswith("_test"))


def _read_frontmatter(skill_md: str) -> dict:
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        logging.warning("skills_catalog: cannot read %s: %s", skill_md, e)
        return {}

    match = _FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        logging.warning(
            "skills_catalog: malformed frontmatter in %s: %s", skill_md, e
        )
        return {}
    return meta if isinstance(meta, dict) else {}


def _extend(skills: list[Skill], found: list[Skill]) -> None:
    """Appends the found skills, dropping names already present (ignoring case)."""
    seen = {skill.name.lower() for skill in skills}
    for skill in found:
        key = skill.name.lower()
        if key and key not in seen:
            seen.add(key)
            skills.append(skill)

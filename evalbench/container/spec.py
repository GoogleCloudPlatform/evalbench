"""The self-contained description of a single eval case.

An `EvalCaseSpec` is everything a fresh container needs to run one scenario and
score it: the scenario itself, the run config, and the model YAMLs the run
config points at. It is deliberately file-based (a flat set of small text
files) so it can be handed to a container as a Kubernetes ConfigMap, a bind
mount, or a temp directory without a second transport.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import copy
import json
import logging
import os
import re
import yaml

# Where the spec is mounted inside the case container.
CASE_DIR = "/etc/evalbench-case"

# Config keys whose value is a path to a model YAML. Each is inlined into the
# spec and rewritten to a container-local path.
MODEL_CONFIG_KEYS = frozenset({"model_config", "simulated_user_model_config"})

CONFIG_FILE = "config.yaml"
SCENARIO_FILE = "scenario.json"
META_FILE = "meta.json"

_MODEL_FILE_RE = re.compile(r"^model\.\d+\.yaml$")
_ENV_FILE_PREFIX = "envfile."
_UNSAFE_NAME_CHARS = re.compile(r"[^a-z0-9]+")

# Work dirs are re-rooted here: the host path from the dataset directory does
# not exist in a fresh container.
CONTAINER_WORK_DIR_ROOT = "/tmp/evalbench-work"


def sanitize_case_id(case_id: str, max_len: int = 40) -> str:
    """Turns an arbitrary scenario id into a DNS-1123 safe name fragment."""
    slug = _UNSAFE_NAME_CHARS.sub("-", str(case_id).lower()).strip("-")
    if not slug:
        slug = "case"
    return slug[:max_len].strip("-") or "case"


@dataclass
class EvalCaseSpec:
    """One eval case, packaged for execution in a fresh container."""

    case_id: str
    job_id: str
    run_time_iso: str
    scenario: dict
    config: dict
    # Container-relative filename -> YAML text, for every model config the run
    # config references.
    model_files: dict[str, str] = field(default_factory=dict)
    # Declared `env_files` for the scenario, name -> content.
    env_files: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        scenario: dict,
        config: dict,
        job_id: str,
        run_time_iso: str,
        session_dir: Optional[str] = None,
    ) -> "EvalCaseSpec":
        """Packages `scenario` plus everything the run config depends on.

        `session_dir` is the directory whose `env_files/` subdirectory holds
        the scenario's declared env files (the parent of the generator's
        sandbox home). When None, env files are not shipped.
        """
        case_id = str(scenario.get("id") or "case")
        child_config = copy.deepcopy(config)

        # The child must not try to containerize again.
        child_config.pop("containerization", None)
        # The child gets its scenario directly; it never loads the dataset.
        child_config.pop("dataset_config", None)
        # Setup/teardown are run once per *run* by the caller that owns the
        # shared fixtures. Re-running them in every case container would race
        # N ways and tear down state other in-flight cases still need.
        child_config.pop("set_up_script", None)
        child_config.pop("tear_down_script", None)
        # One scenario per container: in-process fan-out is meaningless here
        # and only competes for the container's CPU.
        child_config["runners"] = {
            **(child_config.get("runners") or {}), "agent_runners": 1}

        model_files: dict[str, str] = {}
        _inline_model_configs(child_config, model_files)

        child_scenario = copy.deepcopy(scenario)
        _rewrite_work_dir(child_scenario, case_id)

        env_files = _collect_env_files(child_scenario, session_dir)

        return cls(
            case_id=case_id,
            job_id=job_id,
            run_time_iso=run_time_iso,
            scenario=child_scenario,
            config=child_config,
            model_files=model_files,
            env_files=env_files,
        )

    def to_files(self) -> dict[str, str]:
        """Renders the spec as flat `filename -> text`.

        Keys are ConfigMap-safe (`[-._a-zA-Z0-9]+`) and mount as plain files in
        a single directory.
        """
        files = {
            CONFIG_FILE: yaml.safe_dump(self.config, sort_keys=False),
            SCENARIO_FILE: json.dumps(self.scenario, indent=2, default=str),
            META_FILE: json.dumps(
                {
                    "case_id": self.case_id,
                    "job_id": self.job_id,
                    "run_time": self.run_time_iso,
                    "env_files": sorted(self.env_files),
                },
                indent=2,
            ),
        }
        files.update(self.model_files)
        for name, content in self.env_files.items():
            files[_env_file_key(name)] = content
        return files

    @property
    def size_bytes(self) -> int:
        return sum(len(v.encode("utf-8")) for v in self.to_files().values())

    def write_to_dir(self, target_dir: str) -> str:
        os.makedirs(target_dir, exist_ok=True)
        for name, content in self.to_files().items():
            with open(os.path.join(target_dir, name), "w", encoding="utf-8") as f:
                f.write(content)
        return target_dir

    @classmethod
    def load_from_dir(cls, case_dir: str) -> "EvalCaseSpec":
        """Reads back a spec written by `write_to_dir` / mounted as a ConfigMap."""
        with open(os.path.join(case_dir, CONFIG_FILE), encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        with open(os.path.join(case_dir, SCENARIO_FILE), encoding="utf-8") as f:
            scenario = json.load(f)

        meta = {}
        meta_path = os.path.join(case_dir, META_FILE)
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)

        # `_env_file_key` flattens `/` to `__`, which is not reversible on its
        # own. meta.json carries the original names, so recover them from
        # there and only fall back to the flattened key if meta is missing.
        original_names = {
            _env_file_key(name): name for name in (meta.get("env_files") or [])
        }

        model_files, env_files = {}, {}
        for name in sorted(os.listdir(case_dir)):
            path = os.path.join(case_dir, name)
            if not os.path.isfile(path):
                continue
            if _MODEL_FILE_RE.match(name):
                with open(path, encoding="utf-8") as f:
                    model_files[name] = f.read()
            elif name.startswith(_ENV_FILE_PREFIX):
                key = original_names.get(name, name[len(_ENV_FILE_PREFIX):])
                with open(path, encoding="utf-8") as f:
                    env_files[key] = f.read()

        return cls(
            case_id=meta.get("case_id") or str(scenario.get("id") or "case"),
            job_id=meta.get("job_id", ""),
            run_time_iso=meta.get("run_time", ""),
            scenario=scenario,
            config=config,
            model_files=model_files,
            env_files=env_files,
        )

    def resolve_model_paths(self, case_dir: str) -> dict:
        """Returns `config` with model paths pointed at `case_dir`.

        `build` rewrites them to `CASE_DIR`; a caller that mounted the spec
        somewhere else (a temp dir in tests, a bind mount) re-anchors them here.
        """
        config = copy.deepcopy(self.config)
        if os.path.abspath(case_dir) != os.path.abspath(CASE_DIR):
            _reanchor_model_configs(config, case_dir)
        return config


def _env_file_key(name: str) -> str:
    """ConfigMap key for an env file, with `/` flattened to `__`."""
    return _ENV_FILE_PREFIX + name.replace("/", "__")


def _inline_model_configs(node: Any, model_files: dict[str, str]) -> None:
    """Replaces every model-config path in `node` with an inlined copy.

    Walks the whole config, so scorer-level `model_config` keys (goal_completion,
    behavioral_metrics, ...) are picked up alongside the top-level ones. The
    same path referenced twice is inlined once.
    """
    seen: dict[str, str] = {}

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in MODEL_CONFIG_KEYS and isinstance(value, str) and value:
                    obj[key] = _inline_one(value, model_files, seen)
                else:
                    visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(node)


def _inline_one(path: str, model_files: dict[str, str], seen: dict[str, str]) -> str:
    key = os.path.abspath(path)
    if key in seen:
        return seen[key]
    if not os.path.isfile(path):
        # Not a readable path (already container-local, or a URI the child
        # resolves itself). Leave it alone rather than guessing.
        logging.warning(
            "Model config %r is not a readable file; leaving it unresolved in "
            "the containerized case spec.", path)
        return path
    with open(path, encoding="utf-8") as f:
        content = f.read()
    filename = f"model.{len(model_files)}.yaml"
    model_files[filename] = content
    container_path = os.path.join(CASE_DIR, filename)
    seen[key] = container_path
    return container_path


def _reanchor_model_configs(node: Any, case_dir: str) -> None:
    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if (
                    key in MODEL_CONFIG_KEYS
                    and isinstance(value, str)
                    and os.path.dirname(value) == CASE_DIR
                ):
                    obj[key] = os.path.join(case_dir, os.path.basename(value))
                else:
                    visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(node)


def _rewrite_work_dir(scenario: dict, case_id: str) -> None:
    """Re-roots `resolved_work_dir` to a path that exists in the container.

    The dataset loader resolves `work_dir` against the dataset directory on the
    orchestrator's filesystem. That path is meaningless in a fresh container,
    and its *contents* are not shipped -- a scenario that relies on fixture
    files in its work dir needs them baked into the image or mounted, so warn
    loudly rather than silently handing the agent an empty directory.
    """
    original = scenario.get("resolved_work_dir")
    if not original:
        return
    scenario["resolved_work_dir"] = os.path.join(
        CONTAINER_WORK_DIR_ROOT, sanitize_case_id(case_id))
    try:
        has_contents = os.path.isdir(original) and any(os.scandir(original))
    except OSError:
        has_contents = False
    if has_contents:
        logging.warning(
            "Scenario %s work_dir %s is not empty, but its contents are not "
            "shipped to the case container; the agent will see an empty %s. "
            "Bake the fixtures into the case image or mount them into the "
            "worker pool.",
            case_id, original, scenario["resolved_work_dir"],
        )


def _collect_env_files(scenario: dict, session_dir: Optional[str]) -> dict[str, str]:
    declared = scenario.get("env_files") or []
    if not declared:
        return {}
    if not session_dir:
        logging.warning(
            "Scenario %s declares env_files %s but no session directory is "
            "available to read them from; they will be missing in the case "
            "container.", scenario.get("id"), declared)
        return {}

    collected: dict[str, str] = {}
    for name in declared:
        src = os.path.join(session_dir, "env_files", name)
        if not os.path.isfile(src):
            logging.warning(
                "Declared env file not found for containerized case %s: %s",
                scenario.get("id"), src)
            continue
        with open(src, encoding="utf-8") as f:
            collected[name] = f.read()
    return collected

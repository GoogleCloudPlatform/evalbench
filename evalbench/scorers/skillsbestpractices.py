"""
SkillsBestPractices

LLM-based scorer that evaluates SKILL.md quality against best practices:
name compliance, description quality, body completeness, no TODOs,
and progressive disclosure design.
"""
from typing import Tuple, Any
import logging
import os
import re
import json
from scorers import comparator
from generators.models import get_generator
from .prompt.skillsbestpractices import SKILLS_BEST_PRACTICES_PROMPT


class SkillsBestPractices(comparator.Comparator):
    """
    Evaluates the SKILL.md file for each activated skill against best practices.

    Configuration (under scorers.skills_best_practices in run YAML):
      model_config: path/to/model.yaml   (required)
      skills_dir: /path/to/skills/dir    (optional; falls back to sandbox paths)

    The scorer iterates over all activated skills (from accumulated_skills),
    reads each skill's SKILL.md from skills_dir/<skill_name>/SKILL.md,
    and scores it. The final score is the mean across all evaluated skills.
    """

    def __init__(self, config: dict, global_models):
        self.name = "skills_best_practices"
        model_config = config.get("model_config") or ""
        if not model_config:
            raise ValueError("model_config is required for SkillsBestPractices")
        self.model = get_generator(global_models, model_config)

        # Resolve where skill SKILL.md files live. Prefer an explicit
        # `skills_dir` in the scorer config; otherwise fall back to the
        # sandbox paths used by the agent generators dynamically during comparison.
        self.skills_dir = config.get("skills_dir") or ""

    def _find_skill_md(self, skill_name: str, skills_dir: str) -> str | None:
        """Resolves the SKILL.md path for a given skill name.

        Searches skills_dir for a subdirectory whose name matches skill_name,
        then returns the path to its SKILL.md. Returns None if not found.
        """
        if not skills_dir:
            return None
        # Direct match
        candidate = os.path.join(skills_dir, skill_name, "SKILL.md")
        if os.path.exists(candidate):
            return candidate
        # Case-insensitive fallback
        if os.path.isdir(skills_dir):
            for entry in os.listdir(skills_dir):
                if entry.lower() == skill_name.lower():
                    candidate = os.path.join(
                        skills_dir, entry, "SKILL.md"
                    )
                    if os.path.exists(candidate):
                        return candidate
        return None

    _CATEGORIES = (
        "metadata_quality",
        "conciseness",
        "progressive_disclosure",
        "clarity",
        "content_quality",
    )

    @staticmethod
    def _extract_json(response_text: str) -> dict | None:
        """Parses a JSON object from an LLM response, tolerating Markdown fences
        and surrounding prose. Returns None if no valid JSON object is found."""
        text = response_text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None

    def _score_skill(self, skill_name: str, skills_dir: str) -> Tuple[float, str]:
        skill_md_path = self._find_skill_md(skill_name, skills_dir)
        if not skill_md_path:
            return 0.0, f"SKILL.md not found for skill '{skill_name}' in {skills_dir}"

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                skill_md_content = f.read()
        except OSError as e:
            return 0.0, f"Failed to read SKILL.md for '{skill_name}': {e}"

        prompt = SKILLS_BEST_PRACTICES_PROMPT.format(
            skill_md_content=skill_md_content,
            skill_dir_name=skill_name,
        )

        try:
            response = self.model.generate(prompt)
            response_text = getattr(response, "stdout", response) if response else ""
            if not isinstance(response_text, str):
                return 0.0, "LLM response was not a string."

            logging.debug(f"Full LLM response for '{skill_name}': {response_text[:500]}")

            data = self._extract_json(response_text)
            if not isinstance(data, dict) or "score" not in data:
                logging.error(
                    f"Could not parse JSON score from response for '{skill_name}': "
                    f"{response_text[:200]}")
                return 0.0, f"Invalid JSON in response: {response_text[:200]}"

            try:
                score = float(min(100, max(0, int(data["score"]))))
            except (TypeError, ValueError):
                return 0.0, f"score field is not an integer: {data.get('score')!r}"

            breakdown_lines = []
            for cat in self._CATEGORIES:
                cat_data = data.get(cat) or {}
                cat_score = cat_data.get("score") if isinstance(cat_data, dict) else None
                cat_comment = cat_data.get("comment", "") if isinstance(cat_data, dict) else ""
                if cat_score is not None:
                    breakdown_lines.append(f"{cat}: {cat_score}/20 — {cat_comment}")
            summary = data.get("summary", "")
            if summary:
                breakdown_lines.append(f"summary: {summary}")
            detail_text = "\n".join(breakdown_lines) or response_text[:500]
            logging.info(
                f"Score for '{skill_name}': {score:.0f}\nDetails: {detail_text[:200]}")
            return score, detail_text
        except Exception as e:
            logging.error(f"SkillsBestPractices LLM call failed for '{skill_name}': {e}")
            return 0.0, f"Error calling model: {e}"

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:
        if generated_error:
            return 0.0, f"Generation error: {generated_error}"

        try:
            context = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
        except (json.JSONDecodeError, TypeError):
            return 0.0, "Invalid or missing eval result context."

        accumulated_skills = context.get("accumulated_skills", []) or []

        if not accumulated_skills:
            return 100.0, "No skills were activated; best practices check skipped."

        # Resolve skills_dir dynamically if not explicitly set
        skills_dir = self.skills_dir
        if not skills_dir:
            fake_home = context.get("fake_home")
            if fake_home:
                candidate_dirs = (
                    os.path.join(fake_home, ".codex", "skills"),
                    os.path.join(fake_home, ".gemini", "skills"),
                    os.path.join(fake_home, ".claude", "skills"),
                )
                for d in candidate_dirs:
                    if os.path.isdir(d):
                        skills_dir = os.path.abspath(d)
                        break

            if not skills_dir:
                # Fallback to hardcoded paths relative to CWD
                fallback_skill_dirs = (
                    os.path.join(".venv", "fake_home_claude", ".claude", "skills"),
                    os.path.join(".venv", "fake_home_codex", ".codex", "skills"),
                    os.path.join(".venv", "fake_home", ".gemini", "skills"),  # Fixed path
                )
                for d in fallback_skill_dirs:
                    if os.path.isdir(d):
                        skills_dir = os.path.abspath(d)
                        break

        scores = []
        explanations = []
        logging.info(
            f"Evaluating {len(accumulated_skills)} skill(s) "
            f"for best practices: {accumulated_skills} using skills_dir: {skills_dir}")
        for skill_name in accumulated_skills:
            score, explanation = self._score_skill(skill_name, skills_dir)
            scores.append(score)
            explanations.append(f"[{skill_name}] Score={score:.0f}: {explanation[:300]}")
            logging.info(f"  {skill_name}: {score:.0f} - {explanation[:100]}")

        final_score = sum(scores) / len(scores) if scores else 0.0
        summary = f"Mean best practices score across {len(scores)} skill(s): {final_score:.2f}\n"
        summary += "\n".join(explanations)
        logging.info(f"Final best practices score: {final_score:.2f}")
        logging.info(f"Summary: {summary}")
        return final_score, summary

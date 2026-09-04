# cortadoevaluator.py

from typing import Any, List, Dict, Tuple, Optional
import datetime
import concurrent.futures
import logging
import json
import re
import threading

import databases
from dataset.cortadoinput import EvalCortadoRequest
from generators.models.grpc_proxy import GrpcProxyModel
from util.config import load_yaml_config
from mp import mprunner
from work.agentgenwork import AgentGenWork
from evaluator.simulateduser import SimulatedUser
from work.agentscorework import AgentScoreWork
from scorers.setmatcher import SetMatcher


def extract_golden_sql_for_turn(scenario: Dict[str, Any], turn: int, dialect: str = "") -> str:
    """Extracts expected golden SQL for a specific turn from scenario plan or golden_sql."""
    # 1. Check structured turns
    turns = scenario.get("turns")
    if isinstance(turns, list) and turn < len(turns) and isinstance(turns[turn], dict):
        turn_sql = turns[turn].get("golden_sql") or turns[turn].get("sql")
        if turn_sql:
            if isinstance(turn_sql, list) and len(turn_sql) > 0:
                return str(turn_sql[0]).strip()
            return str(turn_sql).strip()

    # 2. Parse from conversation_plan if present
    conversation_plan = scenario.get("conversation_plan", "")
    plan_text = ""
    if isinstance(conversation_plan, list):
        if turn < len(conversation_plan):
            item = conversation_plan[turn]
            if isinstance(item, dict):
                sql_val = item.get("golden_sql") or item.get("sql") or item.get("expected_sql")
                if sql_val:
                    return str(sql_val).strip()
            plan_text = str(item)
        else:
            plan_text = "\n".join(str(p) for p in conversation_plan)
    elif isinstance(conversation_plan, str):
        plan_text = conversation_plan

    if plan_text:
        # Check for turn-specific segment in plan (e.g. "Turn 1: ... Turn 2: ...")
        turn_num = turn + 1
        turn_pattern = re.compile(
            rf'(?:Turn|Step)\s*{turn_num}\b[:\.\-]?\s*(.*?)(?=(?:Turn|Step)\s*\d+\b[:\.\-]|\Z)',
            re.DOTALL | re.IGNORECASE,
        )
        turn_match = turn_pattern.search(plan_text)
        target_text = turn_match.group(1) if turn_match else plan_text

        # Look for SQL patterns within the targeted section
        sql_patterns = [
            r"(?:Agent should execute SQL|execute SQL|golden SQL|expected SQL|run SQL|SQL)\s*[:=]\s*[`'\"]([^`'\"]+)[`'\"]",
            r"```(?:sql)?\s*([\s\S]*?)\s*```",
            r"(?:Agent should execute SQL|execute SQL|golden SQL|expected SQL|run SQL|SQL)\s*[:=]\s*(SELECT\b[\s\S]*?)(?:[\.;\n]|$)",
        ]
        for pattern in sql_patterns:
            match = re.search(pattern, target_text, re.IGNORECASE)
            if match:
                extracted = match.group(1).strip()
                if extracted:
                    return extracted

    # 3. Fallback to scenario-level golden_sql
    golden_sql = scenario.get("golden_sql")
    if golden_sql:
        if isinstance(golden_sql, dict):
            sqls = golden_sql.get(dialect, golden_sql.get("googlesql", []))
            if not sqls and len(golden_sql) > 0:
                sqls = list(golden_sql.values())[0]
            if isinstance(sqls, list) and len(sqls) > 0:
                if turn < len(sqls):
                    return str(sqls[turn]).strip()
                return str(sqls[0]).strip()
            elif isinstance(sqls, str):
                return sqls.strip()
        elif isinstance(golden_sql, list) and len(golden_sql) > 0:
            if turn < len(golden_sql):
                return str(golden_sql[turn]).strip()
            return str(golden_sql[0]).strip()
        elif isinstance(golden_sql, str):
            return golden_sql.strip()

    return ""


def extract_tools_and_skills_from_turn(
    eval_result: Any, agent_text: str = "", sql_reply: str = ""
) -> Tuple[List[str], List[str]]:
    """Extracts tool calls and skills from eval_result other metadata, agent text, and SQL."""
    tools: List[str] = []
    skills: List[str] = []

    def _extract_from_obj(obj: Any):
        if isinstance(obj, str):
            try:
                parsed = json.loads(obj)
                _extract_from_obj(parsed)
                return
            except Exception:
                pass
            for tool_name in ["dataplex_search", "query_data_tool", "execute_sql_tool", "query_data"]:
                if tool_name in obj and tool_name not in tools:
                    tools.append(tool_name)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("actionName", "action_name", "tool_name", "tool", "name") and isinstance(v, str):
                    if v and v not in tools:
                        tools.append(v)
                elif k in ("tools", "tool_calls", "actions") and isinstance(v, list):
                    for item in v:
                        _extract_from_obj(item)
                elif k in ("skills", "skill_calls") and isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item not in skills:
                            skills.append(item)
                else:
                    _extract_from_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                _extract_from_obj(item)

    other = {}
    if hasattr(eval_result, "other") and isinstance(eval_result.other, dict):
        other = eval_result.other
    elif isinstance(eval_result, dict) and "other" in eval_result and isinstance(eval_result["other"], dict):
        other = eval_result["other"]

    for k, v in other.items():
        if "debug" in k.lower() or "tool" in k.lower() or "telemetry" in k.lower() or k == "macchiato_debug_info":
            _extract_from_obj(v)

    if sql_reply and sql_reply.strip():
        if not any(t in tools for t in ("query_data_tool", "execute_sql_tool", "query_data")):
            tools.append("query_data_tool")

    return tools, skills


class CortadoEvaluator:
    def __init__(self, config: Dict[str, Any], db_configs: Optional[Dict[str, Any]] = None):
        self.config = config
        self.db_configs = db_configs or config.get("db_configs", {}) or {}
        self._db_lock = threading.Lock()
        self._db_cache: Dict[Tuple[str, str], Any] = {}

        # Initialize SetMatcher scorer
        self.set_matcher = SetMatcher(self.config.get("scorers", {}).get("set_match", {}))

        # Load model config
        model_config = config
        if "model_config" in config and isinstance(config["model_config"], str):
            loaded_config = load_yaml_config(config["model_config"])
            model_config = loaded_config.copy()
            model_config.update(config)

        generator_type = model_config.get("generator")
        if generator_type == "grpc_proxy":
            self.generator = GrpcProxyModel(model_config)
        else:
            raise ValueError(
                f"CortadoEvaluator requires 'grpc_proxy' generator, got {generator_type}"
            )

        runner_config = self.config.get("runners", {})
        self.agent_runners = runner_config.get("agent_runners", 10)
        self.agentrunner = mprunner.MPRunner(self.agent_runners)

    def _get_db(self, database_name: str, dialect: str = "bigquery") -> Any:
        if not database_name:
            return None
        cache_key = (dialect, database_name)
        with self._db_lock:
            if cache_key in self._db_cache:
                return self._db_cache[cache_key]

            db_cfg = None
            if self.db_configs:
                if isinstance(self.db_configs, dict):
                    dialect_configs = self.db_configs.get(
                        dialect, self.db_configs.get("bigquery", [])
                    )
                    if isinstance(dialect_configs, list) and dialect_configs:
                        db_cfg = dialect_configs[0]
                    elif isinstance(dialect_configs, dict):
                        db_cfg = dialect_configs
                elif isinstance(self.db_configs, list) and self.db_configs:
                    db_cfg = self.db_configs[0]

            if not db_cfg:
                db_cfg = self.config.get("database_config") or {"db_type": "bigquery"}

            db_cfg_copy = db_cfg.copy() if isinstance(db_cfg, dict) else {"db_type": "bigquery"}
            if "db_type" not in db_cfg_copy:
                db_cfg_copy["db_type"] = dialect or "bigquery"

            try:
                db = databases.get_database(db_cfg_copy, database_name)
                self._db_cache[cache_key] = db
                return db
            except Exception as e:
                logging.warning(
                    f"Could not initialize database '{database_name}' for dialect '{dialect}': {e}"
                )
                return None

    def _execute_sql(self, db: Any, sql_query: str) -> Tuple[Any, Any]:
        """Executes SQL against db, returning (result_rows, error_str)."""
        if not db or not sql_query or not sql_query.strip():
            return None, None
        try:
            res, _, err = db.execute(sql_query, use_cache=True, rollback=True)
            return res, err
        except Exception as e:
            return None, str(e)

    def evaluate(self, dataset: List[EvalCortadoRequest], job_id: str, run_time: datetime.datetime):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        logging.info("Running Cortado gRPC evaluation")

        self.agentrunner.futures.clear()

        metadata = {
            "dialects": self.config.get("dialects", []),
            "database": self.config.get("database", "unknown"),
            "scorers": self.config.get("scorers", {}),
        }

        # Spin up threads for concurrent conversation processing
        for item in dataset:
            simulated_user = SimulatedUser(self.config)
            work = AgentGenWork(
                processor=self.process_scenario,
                eval_result=item,
                job_id=job_id,
                metadata=metadata,
                simulated_user=simulated_user,
            )
            self.agentrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.agentrunner.futures):
            try:
                # This now contains the returned object from process_scenario
                modified_item = future.result()
                if hasattr(modified_item, "agent_results"):
                    eval_outputs.extend(modified_item.agent_results)
                if hasattr(modified_item, "scoring_results"):
                    scoring_results.extend(modified_item.scoring_results)
            except Exception as e:
                logging.error(f"Error getting result from future: {e}", exc_info=True)

        return eval_outputs, scoring_results

    def process_scenario(
        self,
        scenario: Dict[str, Any],
        eval_result: Any,
        job_id: str,
        metadata: Dict[str, Any],
        simulated_user: Any = None,
    ) -> Any:
        """Communication between Cortado and the Simulated User."""

        current_prompt = scenario.get("starting_prompt", "")
        max_turns = scenario.get("max_turns", 1)
        conversation_plan = scenario.get("conversation_plan", [])
        conversation_history: List[Dict[str, str]] = []
        turn_history: List[Dict[str, Any]] = []
        last_agent_text = ""
        last_sql_reply = ""
        last_golden_sql = ""
        last_gen_res = None
        last_golden_res = None
        last_gen_err = None
        last_golden_err = None

        accumulated_tools: List[str] = []
        accumulated_skills: List[str] = []

        database_name = scenario.get("database") or metadata.get("database", "")
        dialects = scenario.get("dialects") or metadata.get("dialects", ["bigquery"])
        dialect = dialects[0] if isinstance(dialects, list) and dialects else (dialects if isinstance(dialects, str) else "bigquery")
        db = self._get_db(database_name, dialect)

        for turn in range(max_turns):
            logging.info(f"Turn {turn + 1}/{max_turns} - Prompt: {current_prompt}")

            # Inject the current prompt into the object
            eval_result.nl_prompt = current_prompt

            # Hand it to the gRPC Proxy (blocks until client replies)
            agent_text = ""
            sql_reply = ""
            try:
                self.generator.generate(eval_result)

                nl_reply = getattr(eval_result, "generated_nl_response", "")
                sql_reply = getattr(eval_result, "generated_sql", "")
                agent_text = nl_reply

            except Exception as e:
                logging.error(f"gRPC generation failed: {e}", exc_info=True)
                agent_text = f"Error: {e}"
                sql_reply = ""

            last_agent_text = agent_text
            logging.info(f"Turn {turn + 1}/{max_turns} - Agent Reply to Simulated User: {agent_text}")

            # Extract tools & skills for this turn
            turn_tools, turn_skills = extract_tools_and_skills_from_turn(
                eval_result, agent_text, sql_reply
            )
            accumulated_tools.extend(turn_tools)
            accumulated_skills.extend(turn_skills)

            # Golden SQL extraction for this turn
            turn_golden_sql = extract_golden_sql_for_turn(scenario, turn, dialect)

            # Execute SQLs against DB if present
            golden_res, golden_err = self._execute_sql(db, turn_golden_sql)
            gen_res, gen_err = self._execute_sql(db, sql_reply)

            # Calculate Set Match score for this turn
            turn_set_match_score = 0.0
            if turn_golden_sql or sql_reply:
                try:
                    score, _ = self.set_matcher.compare(
                        nl_prompt=current_prompt,
                        golden_query=turn_golden_sql or "",
                        query_type="dql",
                        golden_execution_result=golden_res if golden_res is not None else [],
                        golden_eval_result="",
                        golden_error=str(golden_err) if golden_err else "",
                        generated_query=sql_reply or "",
                        generated_execution_result=gen_res if gen_res is not None else [],
                        generated_eval_result="",
                        generated_error=str(gen_err) if gen_err else "",
                    )
                    turn_set_match_score = float(score)
                except Exception as e:
                    logging.warning(f"SetMatcher error on turn {turn + 1}: {e}")
                    turn_set_match_score = 0.0

            # Record turn in turn_history
            turn_record = {
                "turn": turn + 1,
                "user_prompt": current_prompt,
                "agent_response": agent_text,
                "generated_sql": sql_reply,
                "golden_sql": turn_golden_sql,
                "generated_execution_result": gen_res,
                "generated_error": str(gen_err) if gen_err else None,
                "golden_execution_result": golden_res,
                "golden_error": str(golden_err) if golden_err else None,
                "set_match": turn_set_match_score,
                "tools": turn_tools,
            }
            turn_history.append(turn_record)

            if sql_reply or not last_sql_reply:
                last_sql_reply = sql_reply
                last_golden_sql = turn_golden_sql
                last_gen_res = gen_res
                last_golden_res = golden_res
                last_gen_err = gen_err
                last_golden_err = golden_err

            # Log history
            conversation_history.append({
                "user": current_prompt,
                "agent": agent_text,
            })

            # Invoke Simulated User to check plan and generate next turn
            if turn < max_turns - 1 and simulated_user:
                next_response = simulated_user.get_next_response(
                    conversation_plan, conversation_history, agent_text
                )
                if "TERMINATE" in next_response:
                    logging.info("Simulated user met the goal and terminated the conversation.")
                    break
                current_prompt = next_response
            else:
                break

        # Finalize and Score
        self._finalize_scenario(
            scenario=scenario,
            last_response=last_agent_text,
            conversation_history=conversation_history,
            accumulated_tools=accumulated_tools,
            accumulated_skills=accumulated_skills,
            eval_result=eval_result,
            job_id=job_id,
            metadata=metadata,
            last_sql=last_sql_reply,
            turn_history=turn_history,
            last_golden_sql=last_golden_sql,
            last_gen_res=last_gen_res,
            last_golden_res=last_golden_res,
            last_gen_err=last_gen_err,
            last_golden_err=last_golden_err,
        )
        return eval_result

    def _finalize_scenario(
        self,
        scenario: Dict[str, Any],
        last_response: str,
        conversation_history: List[Dict[str, str]],
        accumulated_tools: List[str],
        accumulated_skills: List[str],
        eval_result: Any,
        job_id: str,
        metadata: Dict[str, Any],
        last_sql: str,
        turn_history: Optional[List[Dict[str, Any]]] = None,
        last_golden_sql: str = "",
        last_gen_res: Any = None,
        last_golden_res: Any = None,
        last_gen_err: Any = None,
        last_golden_err: Any = None,
    ):
        """Packages the conversation and sends it to the scoring engine."""

        eval_output_data = {
            "eval_id": scenario["id"],
            "stdout": last_response,  # This is the text seen by the simulated user
            "stderr": "",
            "returncode": 0 if not last_response.startswith("Error") else 1,
            "prompt_generator_error": None,
            "generated_error": str(last_gen_err) if last_gen_err else None,
            "sql_generator_error": None,
            "golden_error": str(last_golden_err) if last_golden_err else None,
            "generated_sql": last_sql if last_sql else "skipped",
            "golden_sql": last_golden_sql,
            "generated_result": last_gen_res if last_gen_res is not None else accumulated_tools,
            "golden_result": last_golden_res if last_golden_res is not None else scenario.get("expected_trajectory", []),
            "prompt": scenario["starting_prompt"],
            "conversation_history": json.dumps(conversation_history, indent=2),
            "turn_history": turn_history or [],
            "scenario": scenario,
            "accumulated_tools": accumulated_tools,
            "accumulated_skills": accumulated_skills,
            "job_id": job_id,
            "metadata": metadata,
        }

        score_work = AgentScoreWork(
            config=self.config,
            eval_output=eval_output_data,
            scoring_results=eval_result.scoring_results,
        )
        score_work.run()
        eval_result.agent_results.append(eval_output_data)

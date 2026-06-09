import datetime
import json
import tempfile
import uuid
from typing import Any, List

from dataset.dataengineeringagentinput import EvalDeaRequest
from evaluator.orchestrator import Orchestrator
from evaluator.dataengineeringagentevaluator import (
    DataEngineeringAgentEvaluator,
)


class DataEngineeringAgentOrchestrator(Orchestrator):
    """Orchestrator designed for pure conversational non-db DEA evaluations.

    Bypasses all legacy database connection handshakes, dialect checks,
    and connection pool setups.
    """

    def __init__(
        self,
        config: dict[str, Any],
        db_configs: dict[str, Any] | None = None,
        setup_config: dict[str, Any] | None = None,
        report_progress: bool = False,
    ):
        super().__init__(config, db_configs, setup_config, report_progress)
        self.job_id = f"dea-job-{uuid.uuid4()}"

    def evaluate(self, dataset: List[EvalDeaRequest]) -> None:
        """Orchestrates pure conversational evaluations.

        Delegates straight to DataEngineeringAgentEvaluator.
        """
        evaluator = DataEngineeringAgentEvaluator(self.config)
        eval_outputs, scoring_results = evaluator.evaluate(
            dataset, self.job_id, self.run_time
        )
        self.total_eval_outputs.extend(eval_outputs)
        self.total_scoring_results.extend(scoring_results)

    def process(self):
        """Packages and writes final scores and transcripts to JSON files."""
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as f:
            json.dump(
                self.total_eval_outputs,
                f,
                sort_keys=True,
                indent=4,
                default=str
            )
            results_tf = f.name

        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as f:
            json.dump(
                self.total_scoring_results,
                f,
                sort_keys=True,
                indent=4,
                default=str
            )
            scores_tf = f.name

        return self.job_id, self.run_time, results_tf, scores_tf, None

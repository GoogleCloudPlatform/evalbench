from evaluator.orchestrator import Orchestrator
from evaluator.oneshotorchestrator import OneShotOrchestrator
from evaluator.interactorchestrator import InteractOrchestrator
from evaluator.agentorchestrator import AgentOrchestrator
import logging


def get_orchestrator(config, db_configs, setup_config, report_progress=False):
    orchestrator_type = config.get("orchestrator", "oneshot")
    logging.info(f"Orchestrator Type: {orchestrator_type}")
    if orchestrator_type == "oneshot":
        return OneShotOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "interact":
        return InteractOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "geminicli":
        return AgentOrchestrator(config, db_configs, setup_config, report_progress)
    else:
        return Orchestrator(config, db_configs, setup_config, report_progress)

from evaluator.orchestrator import Orchestrator
from evaluator.oneshotorchestrator import OneShotOrchestrator
from evaluator.cortadoorchestrator import CortadoOrchestrator
from evaluator.interactorchestrator import InteractOrchestrator
from evaluator.dataagentorchestrator import DataAgentOrchestrator
from evaluator.agentorchestrator import AgentOrchestrator
from evaluator.streamingorchestrator import StreamingOrchestrator
from evaluator.dataengineeringagentorchestrator import (
    DataEngineeringAgentOrchestrator,
)
import logging


def get_orchestrator(config, db_configs, setup_config, report_progress=False):
    orchestrator_type = config.get("orchestrator", "oneshot")
    if orchestrator_type == "oneshot":
        return OneShotOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "interact":
        return InteractOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "dataagent":
        return DataAgentOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type in ("geminicli", "agent"):
        return AgentOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "cortado":
        return CortadoOrchestrator(config, db_configs, setup_config, report_progress)
    elif orchestrator_type == "dea":
        return DataEngineeringAgentOrchestrator(
            config, db_configs, setup_config, report_progress
        )
    else:
        return Orchestrator(config, db_configs, setup_config, report_progress)


def get_streaming_orchestrator(config, db_configs, setup_config, report_progress=False):
    return StreamingOrchestrator(config, db_configs, setup_config, report_progress)

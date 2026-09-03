"""Tests for evalproto packaging and lazy loading."""
from unittest.mock import MagicMock
from util.config import load_yaml_config


def test_evalproto_pb2_imports():
    """Verify that all compiled protobuf stubs are importable."""
    from evalproto import eval_agent_pb2
    from evalproto import eval_config_pb2
    from evalproto import eval_connect_pb2
    from evalproto import eval_request_pb2
    from evalproto import eval_response_pb2
    from evalproto import eval_service_pb2

    assert eval_agent_pb2 is not None
    assert eval_config_pb2 is not None
    assert eval_connect_pb2 is not None
    assert eval_request_pb2 is not None
    assert eval_response_pb2 is not None
    assert eval_service_pb2 is not None


def test_evalbench_entrypoint_import():
    """Verify that evalbench entrypoint imports without error."""
    from evalbench.evalbench import run

    assert callable(run)


def test_generators_models_import_without_eager_grpc_proxy():
    """Verify that generators.models imports without eagerly loading grpc_proxy."""
    import evalbench.generators.models as models

    assert hasattr(models, "get_generator")
    assert not hasattr(models, "GrpcProxyModel")
    assert not hasattr(models, "AgentGrpcProxyGenerator")


def test_reporting_import_without_eager_remote_reporter():
    """Verify that reporting imports without eagerly loading RemoteReporter."""
    import evalbench.reporting as reporting

    assert hasattr(reporting, "get_reporters")
    assert not hasattr(reporting, "RemoteReporter")

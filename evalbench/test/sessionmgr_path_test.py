"""Tests for session ID and resource path validation."""

import os
import sys
import unittest
from types import SimpleNamespace

import grpc
import pytest
import yaml

# Ensure evalproto is in sys.path so the generated stubs resolve.
_PROTO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evalproto"
)
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

from util import sessionmgr
from util.sessionmgr import SessionManager, validate_session_id


@pytest.fixture
def session_root(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setattr(sessionmgr, "SESSION_RESOURCES_PATH", str(root))
    return root


@pytest.fixture
def manager():
    # Skip __init__ so no reaper thread starts.
    return object.__new__(SessionManager)


def _resource(address, content=b"data"):
    return SimpleNamespace(address=address, content=content)


@pytest.mark.parametrize(
    "session_id", [None, "", "..", "../etc", "a/b", "abc\n"])
def test_rejects_unsafe_session_id(session_id):
    with pytest.raises(ValueError):
        validate_session_id(session_id)


def test_writes_inside_session_dir(session_root, manager):
    manager.write_resource_files("sid", [_resource("sub/file.yaml", b"ok")])
    assert (session_root / "sid" / "sub" / "file.yaml").read_bytes() == b"ok"


@pytest.mark.parametrize(
    "address", ["", "../x", "a/../../x", "/etc/cron.d/x"])
def test_rejects_escaping_address(session_root, manager, address):
    with pytest.raises(ValueError):
        manager.write_resource_files(
            "sid", [_resource("ok.yaml"), _resource(address)])
    # No partial write, not even the valid resource.
    assert not (session_root / "sid").exists()


def test_rejects_symlink_escape(session_root, manager, tmp_path):
    (session_root / "sid").mkdir()
    os.symlink(tmp_path, session_root / "sid" / "link")
    with pytest.raises(ValueError):
        manager.write_resource_files("sid", [_resource("link/x")])
    assert not (tmp_path / "x").exists()


def test_prune_rejects_traversal_session_id(session_root, manager, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    with pytest.raises(ValueError):
        manager.prune_resource_files("../victim")
    assert (victim / "keep.txt").exists()


class TestGrpcRejectsBadInput(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from eval_service import EvalServicer, SessionManagerInterceptor
        from evalproto import eval_service_pb2_grpc

        self.server = grpc.aio.server(
            interceptors=[SessionManagerInterceptor("SessionManagerInterceptor")]
        )
        eval_service_pb2_grpc.add_EvalServiceServicer_to_server(
            EvalServicer(), self.server)
        port = self.server.add_insecure_port("127.0.0.1:0")
        await self.server.start()
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        self.stub = eval_service_pb2_grpc.EvalServiceStub(self.channel)

    async def asyncTearDown(self):
        await self.channel.close()
        await self.server.stop(grace=0)

    async def test_rejects_missing_or_invalid_client_rpc_id(self):
        from evalproto import eval_request_pb2

        for metadata in [None, [("client-rpc-id", "../etc")]]:
            with self.subTest(metadata=metadata):
                with self.assertRaises(grpc.aio.AioRpcError) as e:
                    await self.stub.Ping(
                        eval_request_pb2.PingRequest(), metadata=metadata)
                self.assertEqual(
                    e.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    async def test_eval_config_rejects_traversal_address(self):
        from evalproto import eval_config_pb2
        from util import get_SessionManager

        request = eval_config_pb2.EvalConfigRequest(
            yaml_config=yaml.dump({"experiment_name": "x"}).encode("utf-8"),
            resources=[eval_config_pb2.Google3Resources(
                address="../../x", content=b"x")],
        )
        try:
            with self.assertRaises(grpc.aio.AioRpcError) as e:
                await self.stub.EvalConfig(
                    request, metadata=[("client-rpc-id", "traversaltest")])
            self.assertEqual(
                e.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        finally:
            get_SessionManager().delete_session("traversaltest")

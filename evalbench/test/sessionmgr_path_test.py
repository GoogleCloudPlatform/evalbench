"""Tests for session ID and resource path validation."""

import importlib
import os
import sys
import threading
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


@pytest.mark.parametrize(
    "addresses", [["a", "a/b"], ["a/b", "a"], ["a", "a/b/c"]])
def test_rejects_conflicting_addresses(session_root, manager, addresses):
    with pytest.raises(ValueError):
        manager.write_resource_files(
            "sid", [_resource(address) for address in addresses])
    assert not (session_root / "sid").exists()


@pytest.mark.parametrize(
    "existing_dir, existing_file, address",
    [("", "a", "a/b"), ("a", "a/b", "a")],
)
def test_rejects_conflict_with_existing_files(
        session_root, manager, existing_dir, existing_file, address):
    (session_root / "sid" / existing_dir).mkdir(parents=True, exist_ok=True)
    (session_root / "sid" / existing_file).write_bytes(b"old")
    with pytest.raises(ValueError):
        manager.write_resource_files(
            "sid", [_resource("ok.yaml"), _resource(address)])
    assert not (session_root / "sid" / "ok.yaml").exists()


def test_rejects_symlink_escape(session_root, manager, tmp_path):
    (session_root / "sid").mkdir()
    os.symlink(tmp_path, session_root / "sid" / "link")
    with pytest.raises(ValueError):
        manager.write_resource_files("sid", [_resource("link/x")])
    assert not (tmp_path / "x").exists()


def test_conflict_check_fails_closed_outside_session_dir(session_root):
    # A path outside the session dir must raise. It must not loop forever.
    outside = str(session_root.parent / "elsewhere" / "x")
    errors = []

    def check():
        try:
            sessionmgr._check_path_conflicts(
                "sid", [(outside, _resource("x"))])
        except ValueError as e:
            errors.append(e)

    thread = threading.Thread(target=check, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "_check_path_conflicts did not stop"
    assert errors


def test_prune_rejects_traversal_session_id(session_root, manager, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep")
    with pytest.raises(ValueError):
        manager.prune_resource_files("../victim")
    assert (victim / "keep.txt").exists()


def test_load_sessions_skips_invalid_directory_names(session_root, manager):
    manager.sessions = {}
    (session_root / "valid_sid").mkdir()
    for name in ["bad name", "bad$id", "abc\n", "a" * 129]:
        (session_root / name).mkdir()
    manager.load_sessions_from_disk()
    assert set(manager.sessions) == {"valid_sid"}


def _fail_makedirs(*args, **kwargs):
    raise AssertionError(f"Unexpected os.makedirs{args}")


@pytest.mark.parametrize("module_name, class_name", [
    ("generators.models.gemini_cli", "GeminiCliGenerator"),
    ("generators.models.claude_code", "ClaudeCodeGenerator"),
    ("generators.models.codex_cli", "CodexCliGenerator"),
    ("generators.models.agy_cli", "AgyCliGenerator"),
])
@pytest.mark.parametrize("session_id", ["../victim", "/etc", "a/b"])
def test_cli_generator_rejects_unsafe_config_session_id(
        monkeypatch, module_name, class_name, session_id):
    # The client uploads the model config. Its session_id must not move
    # fake_home out of the session directory.
    generator_class = getattr(importlib.import_module(module_name), class_name)
    monkeypatch.setattr(sys, "argv", ["/evalbench/eval_server.py"])
    monkeypatch.setattr(os, "makedirs", _fail_makedirs)
    with pytest.raises(ValueError):
        generator_class({"session_id": session_id})


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

    async def test_rejects_invalid_client_rpc_id_if_rpc_id_var_is_set(self):
        # The check must not depend on the rpc_id_var value that the RPC
        # inherits. Only the context of this test changes.
        from eval_service import EvalServicer, SessionManagerInterceptor
        from evalproto import eval_request_pb2, eval_service_pb2_grpc
        from util.context import rpc_id_var

        rpc_id_var.set("preset")
        server = grpc.aio.server(
            interceptors=[SessionManagerInterceptor("SessionManagerInterceptor")]
        )
        eval_service_pb2_grpc.add_EvalServiceServicer_to_server(
            EvalServicer(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = eval_service_pb2_grpc.EvalServiceStub(channel)
                with self.assertRaises(grpc.aio.AioRpcError) as e:
                    await stub.Ping(
                        eval_request_pb2.PingRequest(),
                        metadata=[("client-rpc-id", "../etc")])
                self.assertEqual(
                    e.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        finally:
            await server.stop(grace=0)

    async def test_eval_config_rejects_bad_addresses(self):
        from evalproto import eval_config_pb2
        from util import get_SessionManager

        cases = {
            "traversal": ["../../x"],
            "conflict": ["a", "a/b"],
        }
        try:
            for name, addresses in cases.items():
                with self.subTest(name):
                    request = eval_config_pb2.EvalConfigRequest(
                        yaml_config=yaml.dump(
                            {"experiment_name": "x"}).encode("utf-8"),
                        resources=[
                            eval_config_pb2.Google3Resources(
                                address=address, content=b"x")
                            for address in addresses
                        ],
                    )
                    with self.assertRaises(grpc.aio.AioRpcError) as e:
                        await self.stub.EvalConfig(
                            request,
                            metadata=[("client-rpc-id", "traversaltest")])
                    self.assertEqual(
                        e.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        finally:
            get_SessionManager().delete_session("traversaltest")

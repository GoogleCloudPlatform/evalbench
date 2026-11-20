"""Tests for the gcloud and npm credential setup of the CLI generators."""

import os
import stat
import subprocess
import sys
import types
from unittest.mock import patch

from generators.models.agent_cli import AgentCliGenerator
from generators.models.gemini_cli import GeminiCliGenerator

ADC_NAME = "application_default_credentials.json"
SERVER_ARGV = ["/evalbench/evalbench/eval_server.py"]


def _homes(tmp_path, with_adc=True):
    """Returns (real_home, fake_home). The fake home is a session sandbox."""
    real_gcloud = tmp_path / "real_home" / ".config" / "gcloud"
    real_gcloud.mkdir(parents=True)
    if with_adc:
        (real_gcloud / ADC_NAME).write_text('{"refresh_token": "host-token"}')
    fake_home = tmp_path / "sessions" / "s1" / "fake_home"
    fake_home.mkdir(parents=True)
    return str(tmp_path / "real_home"), str(fake_home)


def _setup(env, real_home, fake_home):
    # The method does not read self.
    AgentCliGenerator._setup_gcloud_credentials(
        None, env, real_home, fake_home)


def _gcloud_dir(home):
    return os.path.join(home, ".config", "gcloud")


def test_local_run_copies_adc_into_the_sandbox(tmp_path, monkeypatch):
    """A local run uses the login of the developer."""
    monkeypatch.setattr(sys, "argv", ["pytest"])
    real_home, fake_home = _homes(tmp_path)
    env = {}

    _setup(env, real_home, fake_home)

    assert os.path.exists(os.path.join(_gcloud_dir(fake_home), ADC_NAME))
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == os.path.join(
        _gcloud_dir(real_home), ADC_NAME)
    assert env["CLOUDSDK_CONFIG"] == _gcloud_dir(real_home)


def test_eval_server_keeps_credentials_off_the_session_volume(
    tmp_path, monkeypatch
):
    """On the eval server no credential file is copied into the sandbox, and
    gcloud does not use the gcloud config of the server."""
    monkeypatch.setattr(sys, "argv", SERVER_ARGV)
    real_home, fake_home = _homes(tmp_path)
    env = {}

    _setup(env, real_home, fake_home)

    assert os.listdir(fake_home) == []
    assert env["CLOUDSDK_CONFIG"] == _gcloud_dir(fake_home)
    # The CLI reads the ADC file in place.
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == os.path.join(
        _gcloud_dir(real_home), ADC_NAME)


def test_eval_server_without_adc_file_sets_no_credential_path(
    tmp_path, monkeypatch
):
    """Without an ADC file, the CLI and gcloud use the metadata server."""
    monkeypatch.setattr(sys, "argv", SERVER_ARGV)
    real_home, fake_home = _homes(tmp_path, with_adc=False)
    env = {}

    _setup(env, real_home, fake_home)

    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert env["CLOUDSDK_CONFIG"] == _gcloud_dir(fake_home)
    assert os.listdir(fake_home) == []


def test_eval_server_keeps_a_configured_cloudsdk_config(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", SERVER_ARGV)
    real_home, fake_home = _homes(tmp_path)
    env = {"CLOUDSDK_CONFIG": "/configured/gcloud"}

    _setup(env, real_home, fake_home)

    assert env["CLOUDSDK_CONFIG"] == "/configured/gcloud"


def _run_npm_auth(tmp_path, token="npm-token-123"):
    """Runs GeminiCliGenerator._setup_npm_auth with a stubbed gcloud."""
    fake_home = tmp_path / "fake_home"
    real_home = tmp_path / "real_home"
    fake_home.mkdir(exist_ok=True)
    real_home.mkdir(exist_ok=True)
    generator = types.SimpleNamespace(
        fake_home=str(fake_home), real_home=str(real_home))
    gcloud = subprocess.CompletedProcess([], 0, stdout=token + "\n", stderr="")
    with patch(
        "generators.models.gemini_cli.subprocess.run", return_value=gcloud
    ):
        GeminiCliGenerator._setup_npm_auth(generator)
    return fake_home / ".npmrc"


def _mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_npmrc_with_the_access_token_is_owner_only(tmp_path):
    npmrc = _run_npm_auth(tmp_path)

    assert "npm-token-123" in npmrc.read_text()
    assert _mode(npmrc) == 0o600


def test_existing_npmrc_mode_is_tightened(tmp_path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    npmrc = fake_home / ".npmrc"
    npmrc.write_text("registry=https://registry.npmjs.org/\n")
    os.chmod(npmrc, 0o644)

    _run_npm_auth(tmp_path)

    assert "registry=https://registry.npmjs.org/" in npmrc.read_text()
    assert _mode(npmrc) == 0o600


def test_npmrc_symlink_is_replaced_and_not_followed(tmp_path):
    """A symlink in the fake home must not make the harness write the token
    into another file."""
    host_file = tmp_path / "host_file"
    host_file.write_text("host content\n")
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    os.symlink(host_file, fake_home / ".npmrc")

    npmrc = _run_npm_auth(tmp_path)

    assert host_file.read_text() == "host content\n"
    assert not os.path.islink(npmrc)
    assert "host content" not in npmrc.read_text()
    assert "npm-token-123" in npmrc.read_text()

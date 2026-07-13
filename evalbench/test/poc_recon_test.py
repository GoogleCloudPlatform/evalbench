"""PoC — recon-only
"""
import platform
import subprocess


def test_poc_recon_only():
    print("=== PoC: code execution confirmed on self-hosted runner ===")
    print("hostname:", platform.node())
    print("platform:", platform.platform())
    print("whoami:", subprocess.run(
        ["whoami"], capture_output=True, text=True).stdout.strip())
    print("uname -a:", subprocess.run(
        ["uname", "-a"], capture_output=True, text=True).stdout.strip())

from __future__ import absolute_import

import nox
import os

@nox.session
def unittests(session):
    session.run("uv", "pip", "install", ".")
    session.run("uv", "pip", "install", "pytest")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_path = f"{os.path.join(script_dir, 'evalbench')}"
    session.run("pytest", "-vvv", "--capture=no", "-rX", "--ignore", "evalbenchtest/*", success_codes=[0], env={"PYTHONPATH": python_path})

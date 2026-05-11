from __future__ import absolute_import

import nox

@nox.session
def unittests(session):
    session.run("uv", "pip", "install", ".")
    session.run("uv", "pip", "install", "pytest")
    session.run("pytest", "-vvv", "--capture=no", "-rX", "--ignore", "evalbenchtest/*", success_codes=[0])

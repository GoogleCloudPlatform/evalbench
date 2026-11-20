"""Selects the network interface that the eval server binds."""

import ipaddress
from typing import Optional

LOOPBACK_HOST = "127.0.0.1"
WILDCARD_HOST = "[::]"


def resolve_bind_host(
    localhost: bool, cloud_run: bool, env_host: Optional[str]
) -> str:
    """Returns the host for the gRPC port of the eval server.

    EVALBENCH_HOST has priority. With --localhost the server has no
    authentication, so the default is the loopback interface. Cloud Run sends
    requests to the container through its network interface, so the server
    binds all interfaces there. The ALTS server also binds all interfaces.
    """
    if env_host:
        return env_host
    if localhost and not cloud_run:
        return LOOPBACK_HOST
    return WILDCARD_HOST


def is_loopback(host: str) -> bool:
    """Returns True if only the local machine can connect to host."""
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False

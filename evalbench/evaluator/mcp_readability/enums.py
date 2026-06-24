"""Enums for the MCP style-guide readability/compliance check.

These mirror proto enums used by the Data Cloud MCP readability tooling
(e.g. ``cloud.databases.mcp.readability.EndpointType``). They are defined as
plain Python enums here so the feature is self-contained; values can be aligned
to the generated proto later without touching call sites. All enums are written
to the results CSV using their ``.name``.
"""

from enum import Enum


class EndpointType(Enum):
    """How the MCP server is reached.

    Mirrors ``cloud.databases.mcp.readability.EndpointType``.
    """

    ENDPOINT_TYPE_UNSPECIFIED = 0
    REMOTE = 1
    LOCAL = 2


class Environment(Enum):
    """Release channel / deployment environment of an endpoint.

    Distinct from :class:`EndpointType`; used purely to slice / filter results
    (e.g. only check ``PROD`` endpoints).
    """

    ENVIRONMENT_UNSPECIFIED = 0
    PROD = 1
    AUTOPUSH = 2
    STAGING = 3
    DEV = 4


class CheckStatus(Enum):
    """Whether the compliance check *ran* successfully (not its findings).

    Compliance results are captured separately via the p0/p1/p2 issue counts and
    compliance_score; this status only reflects whether the eval completed.

    - ``SUCCESS``: the eval ran end-to-end.
    - ``FETCH_ERROR``: failed to retrieve tools data from the endpoint.
    - ``ANALYSIS_ERROR``: error during LLM analysis or result parsing.
    - ``INTERNAL_ERROR``: other script/system error.
    """

    CHECK_STATUS_UNSPECIFIED = 0
    SUCCESS = 1
    FETCH_ERROR = 2
    ANALYSIS_ERROR = 3
    INTERNAL_ERROR = 4


def _coerce(enum_cls, value, default):
    """Coerce a config value (str / int / enum / None) into ``enum_cls``.

    Unknown values fall back to ``default`` rather than raising, so a typo in a
    config file degrades gracefully instead of aborting the whole run.
    """
    if value is None:
        return default
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, int):
        try:
            return enum_cls(value)
        except ValueError:
            return default
    name = str(value).strip().upper()
    # Allow both short ("REMOTE") and fully-qualified ("ENDPOINT_TYPE_REMOTE")
    # spellings, plus the raw member name.
    members = enum_cls.__members__
    if name in members:
        return members[name]
    for member_name, member in members.items():
        if member_name.endswith("_" + name):
            return member
    return default


def coerce_endpoint_type(value) -> EndpointType:
    return _coerce(EndpointType, value, EndpointType.ENDPOINT_TYPE_UNSPECIFIED)


def coerce_environment(value) -> Environment:
    return _coerce(Environment, value, Environment.ENVIRONMENT_UNSPECIFIED)

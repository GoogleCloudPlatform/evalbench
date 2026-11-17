"""Lets a cancelled `Eval` RPC stop the case containers it started.

The evaluation runs on an executor thread, which Python cannot interrupt, so
when a caller gives up (deadline, Ctrl-C, a Guitar shard being torn down) the
thread keeps going and its case Jobs keep running -- spending agent and judge
tokens on results nobody will read. The server marks the session cancelled
here; `ContainerAgentEvaluator` registers a token for each evaluation it runs
and its wait loops check it.

Keyed by session id rather than threaded through the run config: the config
is deep-copied and YAML-dumped into every case spec, and a `threading.Event`
survives neither.
"""

from contextlib import contextmanager
from typing import Iterator, Optional
import threading

_TOKENS: dict[str, set[threading.Event]] = {}
_LOCK = threading.Lock()


@contextmanager
def cancellation_scope(session_id: Optional[str]) -> Iterator[threading.Event]:
    """Yields an Event that is set if `session_id` is cancelled meanwhile."""
    token = threading.Event()
    key = session_id or ""
    with _LOCK:
        _TOKENS.setdefault(key, set()).add(token)
    try:
        yield token
    finally:
        with _LOCK:
            tokens = _TOKENS.get(key)
            if tokens is not None:
                tokens.discard(token)
                if not tokens:
                    del _TOKENS[key]


def cancel_session(session_id: Optional[str]) -> int:
    """Cancels every evaluation in flight for `session_id`.

    Returns how many were signalled; 0 when nothing containerized is running,
    which makes this safe to call unconditionally when an RPC ends.
    """
    with _LOCK:
        tokens = list(_TOKENS.get(session_id or "", ()))
    for token in tokens:
        token.set()
    return len(tokens)

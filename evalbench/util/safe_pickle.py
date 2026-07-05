"""Restricted unpickling for data read from shared caches (e.g. Redis).

A plain ``pickle.loads`` on cache contents is an arbitrary-code-execution risk:
anyone able to write to the cache could embed a malicious payload that runs on
deserialization. This unpickler only permits the small set of value types
EvalBench actually stores (primitive containers handled by pickle opcodes, plus
a few stdlib value types) and rejects every other global.
"""

import datetime
import decimal
import io
import pickle
import uuid

# (module, qualified_name) pairs that find_class is allowed to resolve.
# Basic containers (list/dict/tuple/str/bytes/int/float/bool/None) are handled
# by pickle opcodes and never reach find_class, so they need no entry here.
_SAFE_GLOBALS = {
    ("builtins", "set"),
    ("builtins", "frozenset"),
    ("builtins", "complex"),
    ("datetime", "datetime"),
    ("datetime", "date"),
    ("datetime", "time"),
    ("datetime", "timedelta"),
    ("datetime", "timezone"),
    ("decimal", "Decimal"),
    ("uuid", "UUID"),
}

# Reference the imported modules so static-analysis/linters keep them as the
# resolution targets used by pickle's find_class.
_ = (datetime, decimal, uuid)


class _RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _SAFE_GLOBALS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"Refusing to unpickle disallowed global '{module}.{name}' from cache"
        )


def safe_pickle_loads(data: bytes):
    """Deserialize cache bytes, rejecting any disallowed pickle globals."""
    return _RestrictedUnpickler(io.BytesIO(data)).load()

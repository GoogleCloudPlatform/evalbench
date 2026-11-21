"""Path checks for file names that come from a dataset or a client."""

import os


def contained_path(base: str, name: str) -> str:
    """Returns base/name. Raises ValueError if the path is outside base.

    realpath resolves "../" and symlinks before the containment check.
    """
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or os.path.isabs(name)
    ):
        raise ValueError(f"Invalid file name: {name!r}")
    real_base = os.path.realpath(base)
    real_path = os.path.realpath(os.path.join(real_base, name))
    if (
        real_path == real_base
        or os.path.commonpath([real_base, real_path]) != real_base
    ):
        raise ValueError(f"File name is outside {base!r}: {name!r}")
    return os.path.join(base, name)

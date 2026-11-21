"""Utility for dynamically loading custom classes via module paths."""

import importlib
import os
import sys
import threading

_SYS_PATH_LOCK = threading.Lock()

# On the eval server, a custom class must be in a module of this setting.
SERVER_MODULES_SETTING = "EVALBENCH_CUSTOM_CLASS_MODULES"


def _check_server_modules(class_path: str, mod_name: str) -> None:
    """Raises PermissionError if the eval server must not load mod_name.

    On the eval server the config comes from the client, so a class path can
    name any importable class. Thus the server loads a custom class only from
    the modules in EVALBENCH_CUSTOM_CLASS_MODULES (comma-separated). A module
    in the list also permits its submodules. The default list is empty.
    """
    if not sys.argv[0].endswith("eval_server.py"):
        return
    allowed = [
        name.strip()
        for name in os.environ.get(SERVER_MODULES_SETTING, "").split(",")
        if name.strip()
    ]
    if any(mod_name == name or mod_name.startswith(name + ".")
           for name in allowed):
        return
    raise PermissionError(
        f"The eval server does not load the custom class '{class_path}'. "
        f"The {SERVER_MODULES_SETTING} variable of the server sets the "
        "permitted modules."
    )


def _import_with_cwd_fallback(mod_name: str):
    """Imports mod_name, falling back to appending os.getcwd() to sys.path."""
    try:
        return importlib.import_module(mod_name)
    except ModuleNotFoundError as e:
        if e.name is None or not (
            mod_name == e.name or mod_name.startswith(e.name + ".")
        ):
            raise
        cwd = os.path.abspath(os.getcwd())
        with _SYS_PATH_LOCK:
            if not any(os.path.abspath(p or cwd) == cwd for p in sys.path):
                # Keep cwd at the end of sys.path so lazy runtime imports work
                # without shadowing standard library or installed packages.
                sys.path.append(cwd)
                importlib.invalidate_caches()
        return importlib.import_module(mod_name)


def load_custom_class(class_path: str):
    """Dynamically imports and returns a class from a module path.

    Supports both 'module.submodule.ClassName' and 'module:ClassName' formats.
    If the target module is not found on sys.path (e.g., when invoked via an
    isolated entrypoint like `uvx`), falls back to appending `os.getcwd()` to
    `sys.path` as a last resort.

    Args:
        class_path: Fully qualified string path to the class.

    Returns:
        The imported class or attribute.

    Raises:
        ValueError: If class_path format is invalid.
        PermissionError: If the eval server does not permit the module.
        ImportError: If the module cannot be imported.
        AttributeError: If the class does not exist in the module.

    Note:
        Loaded classes are not enforced via strict inheritance (issubclass),
        enabling duck-typed implementations of connectors and generators.
    """
    if ":" in class_path:
        mod_name, cls_name = class_path.split(":", 1)
    elif "." in class_path:
        mod_name, cls_name = class_path.rsplit(".", 1)
    else:
        raise ValueError(
            f"Invalid class_path '{class_path}'. Expected format"
            " 'module.submodule.ClassName' or 'module:ClassName'."
        )

    _check_server_modules(class_path, mod_name)

    try:
        mod = _import_with_cwd_fallback(mod_name)
    except ImportError as e:
        raise ImportError(
            f"Failed to import module '{mod_name}' for custom class: {e}"
        ) from e

    if not hasattr(mod, cls_name):
        raise AttributeError(
            f"Module '{mod_name}' has no attribute or class '{cls_name}'."
        )

    return getattr(mod, cls_name)

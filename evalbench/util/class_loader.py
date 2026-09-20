"""Utility for dynamically loading custom classes via module paths."""

import importlib


def load_custom_class(class_path: str):
    """Dynamically imports and returns a class from a module path.

    Supports both 'module.submodule.ClassName' and 'module:ClassName' formats.

    Args:
        class_path: Fully qualified string path to the class.

    Returns:
        The imported class or attribute.

    Raises:
        ValueError: If class_path format is invalid.
        ImportError: If the module cannot be imported.
        AttributeError: If the class does not exist in the module.
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

    try:
        mod = importlib.import_module(mod_name)
    except ImportError as e:
        raise ImportError(
            f"Failed to import module '{mod_name}' for custom class: {e}"
        ) from e

    if not hasattr(mod, cls_name):
        raise AttributeError(
            f"Module '{mod_name}' has no attribute or class '{cls_name}'."
        )

    return getattr(mod, cls_name)

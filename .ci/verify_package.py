#!/usr/bin/env python3
"""Imports every module in the installed evalbench wheel.

Run from outside the repo, against a virtualenv holding only the wheel:

    /tmp/pkgcheck/bin/python /path/to/repo/.ci/verify_package.py
"""
import importlib
import pkgutil
import sys

from absl.flags import DuplicateFlagError

SKIP_PREFIXES = ("evalbench.test",)


def main():
    try:
        import evalbench
    except Exception as e:
        print(f"FAIL: the wheel cannot be imported at all -- "
              f"{type(e).__name__}: {e}")
        return 1

    origin = evalbench.__file__ or ""
    if "site-packages" not in origin:
        print(f"FAIL: evalbench resolved to {origin}, which is not an "
              f"installed wheel. Run this from outside the repo.")
        return 1

    failures = []
    walk_errors = []
    tolerated = 0
    checked = 0

    modules = pkgutil.walk_packages(
        evalbench.__path__, "evalbench.", onerror=walk_errors.append
    )
    for module in modules:
        if module.name.startswith(SKIP_PREFIXES):
            continue
        checked += 1
        try:
            importlib.import_module(module.name)
        except DuplicateFlagError:
            # absl rejects only the second registration, so the module ran.
            tolerated += 1
        except Exception as e:
            detail = str(e).splitlines()[0] if str(e) else ""
            failures.append((module.name, type(e).__name__, detail[:100]))

    print(f"Checked {checked} modules in {origin}")
    if tolerated:
        print(f"Tolerated {tolerated} duplicate absl flag registration(s)")

    for name in walk_errors:
        failures.append((name, "ImportError", "failed while listing submodules"))

    if failures:
        print(f"\n{len(failures)} module(s) failed to import from the wheel:")
        for name, exc, detail in sorted(failures):
            print(f"  {name}: {exc}: {detail}")
        return 1

    print("Wheel is importable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

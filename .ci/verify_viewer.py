#!/usr/bin/env python3
"""Loads the viewer app and renders every page it registers.

Rendering is driven in-process rather than over HTTP: Mesop serves the same
single-page shell for every URL and returns 200 even when the page function
raises, so an HTTP probe cannot tell a working page from a broken one.

    python .ci/verify_viewer.py
"""
import logging
import sys
import traceback
from functools import partial
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1] / "viewer"

ROOT_PAGE = "/"
EXPECTED_PAGES = {ROOT_PAGE}

# main.py imports these under a try/except that only logs, so without an
# explicit check a broken import here would pass silently.
EXPECTED_MODULES = {"dashboard", "conversations"}

# Tabs are state, not routes, so rendering the page once only covers the default.
EXPECTED_TABS = ["Status", "List", "Charts", "Dataset Quality", "Compare"]


class ErrorLogCapture(logging.Handler):
    """Collects ERROR records.

    The app catches its own render failures and logs them, so a broken page
    never raises and exceptions alone are not enough to detect one.
    """

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def main():
    # main.py imports its neighbours by bare name.
    sys.path.insert(0, str(VIEWER_DIR))

    try:
        import main as viewer_app
    except Exception as e:
        print(f"FAIL: the viewer entrypoint cannot be imported -- "
              f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    import mesop as me
    from flask import Flask
    from mesop.runtime import runtime

    failures = []
    rt = runtime()
    app = Flask(__name__)

    captured = ErrorLogCapture()
    logging.getLogger().addHandler(captured)

    def attempt(label, fn):
        # A fresh request context per attempt, so state does not leak between them.
        captured.records.clear()
        try:
            with app.test_request_context():
                fn()
        except Exception as e:
            failures.append(f"{label} raised {type(e).__name__}: {e}")
            traceback.print_exc()
        for record in captured.records:
            failures.append(f"{label} logged an error: {record.getMessage()}")

    def render_tab(tab):
        me.state(viewer_app.State).selected_main_tab = tab
        rt.run_path(ROOT_PAGE)

    def fire_on_load():
        rt.run_path(ROOT_PAGE)
        viewer_app.on_load(me.LoadEvent(path=ROOT_PAGE))

    loading_errors = rt.get_loading_errors()
    if loading_errors:
        failures.append(f"app reported loading errors: {loading_errors}")

    missing = EXPECTED_MODULES - sys.modules.keys()
    if missing:
        failures.append(f"modules failed to import: {sorted(missing)}")

    registered = set(rt.get_path_to_page_configs())
    if registered != EXPECTED_PAGES:
        failures.append(f"registered pages {sorted(registered)} do not match "
                        f"expected {sorted(EXPECTED_PAGES)}")

    for path in sorted(registered):
        attempt(f"rendering {path}", partial(rt.run_path, path))

    attempt("on_load", fire_on_load)

    for tab in EXPECTED_TABS:
        attempt(f"rendering tab {tab!r}", partial(render_tab, tab))

    print(f"Checked {len(registered)} page(s) {sorted(registered)}, "
          f"on_load, and {len(EXPECTED_TABS)} tab(s)")

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print("Viewer loads and renders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

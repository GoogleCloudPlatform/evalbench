#!/usr/bin/env python3
"""Loads the viewer app and renders every page it registers.

Rendering is driven in-process rather than over HTTP: Mesop serves the same
single-page shell for every URL and returns 200 even when the page function
raises, so an HTTP probe cannot tell a working page from a broken one.

Rendering alone is not enough either. With no results to show, every tab draws
its empty state without erroring, so a broken data path looks like a working
one. This script therefore precomputes a fixture and asserts the rendered page
carries it.

    python .ci/verify_viewer.py
"""
import csv
import logging
import os
import shutil
import sys
import tempfile
import traceback
from functools import partial
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parents[1] / "viewer"
# Holds no evals.csv, which is what keeps precompute offline: without one the
# summariser returns before it builds a prompt.
FIXTURE_RESULTS = Path(__file__).resolve().parent / "fixtures" / "viewer_results"

ROOT_PAGE = "/"
EXPECTED_PAGES = {ROOT_PAGE}

# main.py imports these under a try/except that logs a warning, which is below
# the level ErrorLogCapture collects, so a broken import needs its own check.
EXPECTED_MODULES = {"dashboard", "conversations"}

# Tabs are state, not routes, so rendering the page once only covers the default.
# Compare is the fifth: the toggle omits it until the user compares two evals.
EXPECTED_TABS = ["Status", "List", "Charts", "Dataset Quality", "Compare"]


def fixture_runs():
    """The run ids and product names a working data path puts on the page."""
    ids, products = [], []
    for run in sorted(p for p in FIXTURE_RESULTS.iterdir() if p.is_dir()):
        ids.append(run.name)
        with open(run / "configs.csv", newline="") as f:
            products += [row["value"] for row in csv.DictReader(f)
                         if row["config"] == "experiment_config.product_name"]
    return ids, products


FIXTURE_RUN_IDS, FIXTURE_PRODUCTS = fixture_runs()

# The run on_load is asked to select. Every write in on_load is guarded on a
# query param, so without one it leaves the page the plain render already covered.
FIXTURE_JOB_ID = FIXTURE_RUN_IDS[0]

# Run ids and product names reach the page only through the precomputed cache,
# so drawing one proves the cache was read. The counts beside them come from the
# directory listing and still render when the cache loads nothing.
TAB_DATA_MARKERS = {
    "Status": [f"Total Evaluation Jobs: {len(FIXTURE_RUN_IDS)}", *FIXTURE_PRODUCTS],
    "List": [f"Found {len(FIXTURE_RUN_IDS)} evaluation runs", *FIXTURE_RUN_IDS],
}

# A tab can draw a marker above and still fall back to an empty state below it.
# Charts has no data marker of its own, so this is all that covers it.
EMPTY_MARKERS = ("Found 0 evaluation runs", "No data found in any run directory")

# What Compare needs to draw a comparison: the tab itself, exactly two evals,
# and a finished summary. Short of that it draws a placeholder or an error.
TAB_STATE = {
    "Compare": {
        "compare_tab_visible": True,
        "compare_evals": '["eval_a", "eval_b"]',
        "ai_comparison": "comparison placeholder",
    },
}


class ErrorLogCapture(logging.Handler):
    """Collects ERROR records.

    The app catches its own render failures and logs them, so a broken page
    may never raise and exceptions alone are not enough to detect one.
    """

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def main():
    # A copy, because precomputing writes its caches into the results directory.
    with tempfile.TemporaryDirectory() as tmp:
        results_dir = Path(tmp) / "results"
        shutil.copytree(FIXTURE_RESULTS, results_dir)
        os.environ["RESULTS_DIR"] = str(results_dir)
        return verify(results_dir)


def verify(results_dir):
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
    import precompute_trends
    from flask import Flask
    from mesop.runtime import runtime

    failures = []
    rt = runtime()
    app = Flask(__name__)

    captured = ErrorLogCapture()
    logging.getLogger().addHandler(captured)

    def attempt(label, fn):
        captured.records.clear()
        try:
            # A fresh request context per attempt, so state does not leak.
            with app.test_request_context():
                fn(label)
        except Exception as e:
            failures.append(f"{label} raised {type(e).__name__}: {e}")
            traceback.print_exc()
        for record in captured.records:
            detail = record.getMessage()
            if record.exc_info:
                detail += "\n" + "".join(traceback.format_exception(*record.exc_info))
            failures.append(f"{label} logged an error: {detail}")

    def check_drawn(label, markers):
        """Assert the rendered page carries every marker and no empty state.

        Reads the serialised component tree, since the strings a component
        draws live in its payload.
        """
        drawn = rt.context().current_node().SerializeToString()
        for marker in markers:
            if marker.encode() not in drawn:
                failures.append(f"{label} drew no {marker!r}")
        for empty in EMPTY_MARKERS:
            if empty.encode() in drawn:
                failures.append(f"{label} drew the empty state {empty!r}")

    def check_data(label):
        tab = me.state(viewer_app.State).selected_main_tab
        check_drawn(label, TAB_DATA_MARKERS.get(tab, []))

    def render_page(path, label):
        rt.run_path(path)
        check_data(label)

    def render_tab(tab, label):
        state = me.state(viewer_app.State)
        state.selected_main_tab = tab
        for field, value in TAB_STATE.get(tab, {}).items():
            setattr(state, field, value)
        rt.run_path(ROOT_PAGE)
        check_data(label)

    def fire_on_load(label):
        # Mesop's order: query params, then on_load, then the render that sees it.
        rt.context().set_query_param("job_id", FIXTURE_JOB_ID)
        viewer_app.on_load(me.LoadEvent(path=ROOT_PAGE))
        selected = me.state(viewer_app.State).selected_directory
        if selected != FIXTURE_JOB_ID:
            failures.append(f"{label} selected {selected!r}, not {FIXTURE_JOB_ID!r}")
        rt.run_path(ROOT_PAGE)
        # A selected run swaps the tabs out for its detail view, so the tab
        # markers no longer apply and the run's own name is what proves it drew.
        check_drawn(label, [FIXTURE_JOB_ID])

    def report():
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    attempt("precomputing the fixture", lambda _: precompute_trends.precompute())

    trends_cache = results_dir / "trends_cache.csv"
    if not trends_cache.exists():
        failures.append(f"precomputing the fixture wrote no {trends_cache.name}, "
                        f"so there is nothing for the pages to render")

    if failures:
        # Rendering on top of a failed precompute would only cascade.
        return report()

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
        attempt(f"rendering {path}", partial(render_page, path))

    attempt("on_load", fire_on_load)

    for tab in EXPECTED_TABS:
        attempt(f"rendering tab {tab!r}", partial(render_tab, tab))

    print(f"Checked {len(registered)} page(s) {sorted(registered)}, "
          f"on_load, and {len(EXPECTED_TABS)} tab(s) "
          f"against {len(FIXTURE_RUN_IDS)} precomputed run(s)")

    if failures:
        return report()

    print("Viewer loads, renders, and shows its data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

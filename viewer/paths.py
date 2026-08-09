import os


def get_results_dir():
    res_dir = os.environ.get("RESULTS_DIR")
    if res_dir:
        return res_dir

    # Candidate 1 is the repo root, which is /evalbench in the container, so the
    # deployed image resolves it to the same /evalbench/results the runs write to.
    candidates = [
        "/tmp_session_files/results",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "results"),
        os.path.join(os.getcwd(), "results"),
    ]

    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate

    return candidates[1]

import logging
from multiprocessing.managers import SyncManager
import sys
import threading
from io import StringIO
import threading

_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr
_ORIGINAL_HANDLERS = None
_NUM_LINES_FOR_PROGRESS = 5
try:
    import google.colab  # type: ignore
    from IPython.display import display, HTML  # type: ignore

    _IN_COLAB = True
except ImportError:
    _IN_COLAB = False


def setup_progress_reporting(
    manager: SyncManager, total_dataset_len: int, total_dbs: int
):
    tmp_buffer = None
    colab_progress_report = None
    progress_reporting = {
        "lock": manager.Lock(),
        "setup_i": manager.Value("i", 0),
        "prompt_i": manager.Value("i", 0),
        "gen_i": manager.Value("i", 0),
        "exec_i": manager.Value("i", 0),
        "score_i": manager.Value("i", 0),
        "total": total_dataset_len,
        "total_dbs": total_dbs,
    }
    if _IN_COLAB:
        colab_progress_report = _setup_colab(progress_reporting)
    else:
        tmp_buffer = _setup_stdout_reporting()
    progress_reporting_finished = threading.Event()
    progress_reporting_thread = threading.Thread(
        target=_report,
        args=(
            progress_reporting,
            progress_reporting_finished,
            tmp_buffer,
            colab_progress_report,
        ),
        daemon=True,
    )
    progress_reporting_thread.start()
    return (
        progress_reporting_thread,
        progress_reporting,
        progress_reporting_finished,
        tmp_buffer,
        colab_progress_report,
    )


def _setup_colab(progress_report):
    colab_progress_report = _colab_progress(progress_report)
    return display(colab_progress_report, display_id=True)  # type: ignore


def _setup_stdout_reporting():
    global _ORIGINAL_HANDLERS
    logger = logging.getLogger()
    _ORIGINAL_HANDLERS = logger.handlers
    sys.stderr = sys.stdout = tmp_buffer = StringIO()
    logger.handlers = [logging.StreamHandler(tmp_buffer)]
    _ORIGINAL_STDOUT.write(("-" * 80 + "\n") * _NUM_LINES_FOR_PROGRESS)
    return tmp_buffer


def _report(
    progress_reporting, progress_reporting_finished, tmp_buffer, colab_progress_report
):
    while not progress_reporting_finished.is_set():
        if _IN_COLAB:
            colab_progress_report.update(_colab_progress(progress_reporting))
        else:
            _print_report(progress_reporting, tmp_buffer)
        if progress_reporting_finished.wait(timeout=1):
            break


def _colab_progress(progress_reporting):
    setup_done = (
        progress_reporting["setup_i"].value / progress_reporting["total_dbs"]
    ) * 100
    prompt_done = (
        progress_reporting["prompt_i"].value / progress_reporting["total"]
    ) * 100
    gen_done = (progress_reporting["gen_i"].value / progress_reporting["total"]) * 100
    exec_done = (progress_reporting["exec_i"].value / progress_reporting["total"]) * 100
    score_done = (
        progress_reporting["score_i"].value / progress_reporting["total"]
    ) * 100
    return HTML(  # type: ignore
        """
        <div style="width: 100px; float:left;">DBs Setup:</div>
        <progress value='{setup_i}' max='{total_dbs}', style='width: calc(100% - 200px);'>
            {setup_i}
        </progress>
        <div style="width: 70px; float:right; padding-left:30px">{setup_p}</div><br>
        <div style="width: 100px; float:left;">Prompts:</div>
        <progress value='{prompt_i}' max='{total}', style='width: calc(100% - 200px);'>
            {prompt_i}
        </progress>
        <div style="width: 70px; float:right; padding-left:30px">{prompt_p}</div><br>
        <div style="width: 100px; float:left;">SQLGen:</div>
        <progress value='{gen_i}' max='{total}', style='width: calc(100% - 200px);'>
            {gen_i}
        </progress>
        <div style="width: 70px; float:right; padding-left:30px">{gen_p}</div><br>
        <div style="width: 100px; float:left;">SQLExec:</div>
        <progress value='{exec_i}' max='{total}', style='width: calc(100% - 200px);'>
            {exec_i}
        </progress>
        <div style="width: 70px; float:right; padding-left:30px">{exec_p}</div><br>
        <div style="width: 100px; float:left;">Scoring:</div>
        <progress value='{score_i}' max='{total}', style='width: calc(100% - 200px);'>
            {score_i}
        </progress>
        <div style="width: 70px; float:right; padding-left:30px">{score_p}</div><br>
    """.format(
            setup_i=progress_reporting["setup_i"].value,
            setup_p=f"{setup_done:.1f}%",
            prompt_i=progress_reporting["prompt_i"].value,
            prompt_p=f"{prompt_done:.1f}%",
            gen_i=progress_reporting["gen_i"].value,
            gen_p=f"{gen_done:.1f}%",
            exec_i=progress_reporting["exec_i"].value,
            exec_p=f"{exec_done:.1f}%",
            score_i=progress_reporting["score_i"].value,
            score_p=f"{score_done:.1f}%",
            total=progress_reporting["total"],
            total_dbs=progress_reporting["total_dbs"],
        )
    )


def _print_report(progress_reporting, tmp_buffer):
    setup_i = progress_reporting["setup_i"].value
    prompt_i = progress_reporting["prompt_i"].value
    gen_i = progress_reporting["gen_i"].value
    exec_i = progress_reporting["exec_i"].value
    score_i = progress_reporting["score_i"].value
    dataset_len = progress_reporting["total"]
    databases = progress_reporting["total_dbs"]

    if tmp_buffer:
        buffer_content = tmp_buffer.getvalue()
        tmp_buffer.seek(0)
        tmp_buffer.truncate(0)
        if buffer_content != "":
            _ORIGINAL_STDOUT.write("\n")
            _ORIGINAL_STDOUT.write(buffer_content)
            _ORIGINAL_STDOUT.write("\n" * (_NUM_LINES_FOR_PROGRESS + 1))

    _ORIGINAL_STDOUT.write("\033[F\033[K" * _NUM_LINES_FOR_PROGRESS)

    report_progress(
        setup_i, databases, prefix="DBs Setup:", suffix="Complete", length=50
    )
    report_progress(
        prompt_i, dataset_len, prefix="Prompts:  ", suffix="Complete", length=50
    )
    report_progress(
        gen_i, dataset_len, prefix="SQLGen:   ", suffix="Complete", length=50
    )
    report_progress(
        exec_i, dataset_len, prefix="SQLExec:  ", suffix="Complete", length=50
    )
    report_progress(
        score_i, dataset_len, prefix="Scoring:  ", suffix="Complete", length=50
    )
    _ORIGINAL_STDOUT.flush()


def skip_dialect(sub_datasets, progress_reporting):
    if not progress_reporting:
        return
    for database in sub_datasets:
        skip_database(sub_datasets[database], progress_reporting, None)


def skip_database(sub_datasets, progress_reporting, query_type):
    if not progress_reporting:
        return
    if query_type:
        total_dbs = 1
        evals_in_db = len(sub_datasets.get(query_type, []))
    else:
        total_dbs = len(sub_datasets)
        evals_in_db = sum(
            len(sub_datasets.get(query_type, []))
            for query_type in ["dql", "dml", "ddl"]
        )
    with progress_reporting["lock"]:
        progress_reporting["total_dbs"] -= total_dbs


def record_successful_prompt_gen(progress_reporting):
    if progress_reporting:
        with progress_reporting["lock"]:
            progress_reporting["prompt_i"].value += 1


def record_successful_sql_gen(progress_reporting):
    if progress_reporting:
        with progress_reporting["lock"]:
            progress_reporting["gen_i"].value += 1


def record_successful_sql_exec(progress_reporting):
    if progress_reporting:
        with progress_reporting["lock"]:
            progress_reporting["exec_i"].value += 1


def record_successful_scoring(progress_reporting):
    if progress_reporting:
        with progress_reporting["lock"]:
            progress_reporting["score_i"].value += 1


def record_successful_setup(progress_reporting):
    if progress_reporting:
        with progress_reporting["lock"]:
            progress_reporting["setup_i"].value += 1


def cleanup_progress_reporting(progress_report, tmp_buffer, colab_progress_report):
    if _IN_COLAB:
        colab_progress_report.update(_colab_progress(progress_report))
        return
    global _ORIGINAL_HANDLERS
    sys.stdout = _ORIGINAL_STDOUT
    sys.stderr = _ORIGINAL_STDERR
    logger = logging.getLogger()
    if _ORIGINAL_HANDLERS:
        logger.handlers = _ORIGINAL_HANDLERS
    _print_report(progress_report, tmp_buffer)
    tmp_buffer.close()


# Print iterations progress bar for parallel calls
def report_progress(
    iteration,
    total,
    prefix="",
    suffix="",
    decimals=1,
    length=100,
    fill="█",
    printEnd="\n",
):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    if total == 0:
        total = 1
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + "-" * (length - filledLength)

    # Take the first 4 lines of the stdout for progress reporting
    _ORIGINAL_STDOUT.write(f"{prefix} |{bar}| {percent}% {suffix}{printEnd}")

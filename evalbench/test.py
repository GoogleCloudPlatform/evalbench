from databases.spanner import SpannerDB
from databases.db import DB
import threading
import logging
import threading
import sys
import traceback


def dump_threads():
    """Prints a stack trace for every active thread in the current process."""
    print("--- Thread Dump Start ---")

    # Get a mapping of thread ID to the top-most stack frame
    # sys._current_frames() is an internal CPython function that returns a dict
    # {thread_id: stack_frame_object} for all active threads.
    # It is not guaranteed to be available in all Python implementations.
    try:
        id_to_frame = sys._current_frames()
    except AttributeError:
        print(
            "Error: sys._current_frames() is not available on this Python implementation."
        )
        return

    # Get a list of all active Thread objects
    active_threads = threading.enumerate()

    for thread in active_threads:
        # Get the native thread ID (useful for OS-level debugging)
        native_id = getattr(thread, "native_id", "N/A")
        print("--- Thread  ---")

        # Print thread details
        print(f"\nThread ID: {thread.ident} (Native ID: {native_id})")
        print(f"Name: {thread.name}")
        print(f"Daemon: {thread.daemon}")
        print(f"Alive: {thread.is_alive()}")

        # Get the stack frame for this thread
        frame = id_to_frame.get(thread.ident)

        if frame:
            # Print the stack trace
            traceback.print_stack(frame)
        else:
            # A thread might be alive but not have a stack frame if it's
            # executing C code (e.g., waiting on I/O) or has just finished.
            print(
                "  (No Python stack frame available - likely in native/C code or just finished)"
            )


def test():
    db_config = {
        "database_name": "financial",
        "database_path": "cloud-db-nl2sql:us-central1:nl2sql-birdsql-postgres",
        "db_type": "spanner",
        "dialect": "googlesql",
        "gcp_project_id": "cloud-db-nl2sql",
        "instance_id": "evalbench",
        "gcp_region": "us-central1",
        "max_executions_per_minute": 180,
    }
    spanner_db = SpannerDB(db_config)
    spanner_db.execute("select 1")
    spanner_db.close_connections()


if __name__ == "__main__":
    test()
    logging.info(f"Thread count:{threading.active_count()}")
    dump_threads()

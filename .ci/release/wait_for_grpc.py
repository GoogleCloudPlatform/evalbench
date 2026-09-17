#!/usr/bin/env python3
"""Blocks until the eval server answers a gRPC Ping, or the deadline passes.

Exit codes:
    0  the server answered Ping
    1  the deadline passed or a permanent security mismatch occurred
"""
import argparse
import asyncio
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
# Add both module roots so eval_client and generated proto imports resolve.
for _path in (os.path.join(_REPO, "evalbench"),
              os.path.join(_REPO, "evalbench", "evalproto")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from client.eval_client import EvalbenchClient  # noqa: E402

# Consecutive ALTS handshake failures before aborting.
_ALTS_FAILURE_LIMIT = 5


async def wait(timeout: float, interval: float) -> int:
    host = os.getenv("EVALBENCH_HOST", "localhost")
    port = os.getenv("PORT", "50051")
    insecure = os.getenv("EVALBENCH_INSECURE", "").lower() == "true"
    deadline = time.monotonic() + timeout
    attempt = 0
    last_error = "no attempt completed"
    alts_failures = 0

    mode = "insecure" if insecure else "ALTS"
    print(f"Waiting up to {timeout:.0f}s for Ping on {host}:{port} ({mode})")
    while time.monotonic() < deadline:
        attempt += 1
        # Rebuild client per attempt to avoid stale TRANSIENT_FAILURE backoff.
        client = EvalbenchClient("local")
        try:
            response = await asyncio.wait_for(client.ping(), timeout=interval)
            print(f"Ping answered after {attempt} attempts: "
                  f"{response.response}")
            return 0
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if "Alts handshake failed" in last_error:
                alts_failures += 1
                if alts_failures >= _ALTS_FAILURE_LIMIT:
                    print(f"\nAborting after {alts_failures} consecutive ALTS "
                          f"handshake failures.")
                    print("Pass --insecure, or set EVALBENCH_INSECURE=true.")
                    return 1
            else:
                alts_failures = 0
        finally:
            await client.channel.close()
        await asyncio.sleep(interval)

    print(f"No Ping within {timeout:.0f}s after {attempt} attempts.")
    print(f"Last error -- {last_error}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Seconds to wait before giving up.")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Seconds between attempts and per-attempt "
                             "deadline.")
    parser.add_argument("--insecure", action="store_true",
                        help="Use an insecure channel to match --localhost.")
    args = parser.parse_args()
    if args.insecure:
        os.environ["EVALBENCH_INSECURE"] = "true"
    return asyncio.run(wait(args.timeout, args.interval))


if __name__ == "__main__":
    sys.exit(main())

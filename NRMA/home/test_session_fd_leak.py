"""A failed camoufox launch must not leak file descriptors.

Regression guard for the EMFILE that killed the tail of the 202609 run: the
old `async with AsyncCamoufox(...)` orphaned playwright's driver subprocess
whenever the launch itself raised, so a few hundred refused proxies exhausted
the fd table. Run: python NRMA/home/test_session_fd_leak.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import NRMA.home.nrma_browser as nb


def open_fds() -> int:
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


async def main(rounds: int = 5) -> None:
    # An unroutable proxy makes the launch raise inside __aenter__ — the exact
    # shape of the production failure, without needing the real network.
    nb._proxy_config = lambda sid: {
        "server": "http://127.0.0.1:1",
        "username": "x",
        "password": "y",
    }

    async def one() -> None:
        try:
            async with nb.quote_session("NSW"):
                pass
        except Exception:
            pass  # the failure is the point; the cleanup is what we assert on

    await one()  # warm up: first call imports playwright and caches fds
    await asyncio.sleep(1)
    before = open_fds()
    for _ in range(rounds):
        await one()
    await asyncio.sleep(1)
    after = open_fds()

    leaked = after - before
    print(f"fds {before} -> {after} over {rounds} failed launches (leaked {leaked})")
    # A couple of fds of slack for logging/DNS; a real leak is >=3 per launch.
    assert leaked < rounds, f"leaking ~{leaked / rounds:.1f} fds per failed launch"
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())

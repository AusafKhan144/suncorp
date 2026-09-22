#!/usr/bin/env python
"""Start an NRMA home scraper on whichever proxy pool is not resting.

    python launch_free_pool.py contents.py

Why this exists: the breaker's cooldown is per pool (reputation is per pool),
but `NRMA_PROXY_PROVIDER` defaults to one of them — so an unattended cron tick
would sit out Oxylabs' 6h rest collecting nothing, while GoProxies was free the
whole time. This walks `PROXY_POOLS` in preference order and execs the scraper
on the first free pool, which roughly doubles the unattended collection window.

`os.execv`, not a subprocess: the scraper then owns this pid and its argv still
ends in `contents.py`, so the monitor's `pgrep -f "[c]ontents.py"` liveness
check and kill patterns keep working unchanged.

Exits 0 when every pool is resting — that is the normal, correct outcome of a
tick during a cooldown, not a failure.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nrma_browser import PROXY_POOLS, pool_rest_hours  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {os.path.basename(__file__)} <scraper.py>")
        return 2
    script = os.path.join(HERE, os.path.basename(sys.argv[1]))
    if not os.path.exists(script):
        print(f"[launch] no such scraper: {script}")
        return 2

    resting = []
    for pool in PROXY_POOLS:
        rest = pool_rest_hours(pool)
        if rest:
            resting.append(f"{pool} {rest:.1f}h")
            continue
        print(f"[launch] {os.path.basename(script)} on {pool}"
              + (f" (resting: {', '.join(resting)})" if resting else ""))
        os.environ["NRMA_PROXY_PROVIDER"] = pool
        # Replaces this process, so argv keeps the scraper's name for pgrep.
        os.execv(sys.executable, [sys.executable, script])

    print(f"[launch] every pool resting ({', '.join(resting)}) — nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Post one NRMA + Youi home collection-status card to a Google Chat webhook.

Replaces the two per-insurer monitors (NRMA/home/monitor_status.py and
Youi/home/monitor_status.py) — same NRMA card format, both runs in it.

Counts the output CSVs (the scrapers' only checkpoint), reports per-product
progress, and relaunches a scraper that has died with work still pending.
Youi is fanned out by run_youi.py across 20 runner.py processes, so it is
reported but never relaunched from here — restarting it means re-running the
orchestrator, which resumes from the same CSVs.

The webhook URL is read from (in order): env MONITOR_WEBHOOK_URL, then the
first CONFS entry with a line starting http. If none is set it prints the
snapshot instead, so a scheduled run never breaks.

  python monitor_status.py               # report + restart dead runs
  MONITOR_WEBHOOK_URL=- python monitor_status.py   # print, don't post
  MONITOR_AUTOSTART=0 python monitor_status.py     # never relaunch
  python monitor_status.py --selftest    # classifier asserts
"""
import csv
import datetime
import glob
import json
import os
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
NRMA = os.path.join(HERE, "NRMA")
YOUI = os.path.join(HERE, "Youi")
RACV = os.path.join(HERE, "RACV")

# Only the terminal-reason prefixes are shared, not the whole scraper:
# building.py runs logging.basicConfig(FileHandler("scraper.log")) at import and
# pulls httpx, while nrma_browser is stdlib-only at module level (camoufox is
# imported lazily inside the session helper). A broken scraper dependency must
# not take down the monitor — that is exactly when the report matters.
sys.path.insert(0, os.path.join(NRMA, "home"))
from nrma_browser import TERMINAL_REASON_PREFIXES  # noqa: E402
import period  # noqa: E402  (repo root is already on sys.path — this file lives there)

STATE = os.path.join(HERE, "monitor_state.json")
CONFS = [os.path.join(NRMA, "home", "webhook.conf"),
         os.path.join(NRMA, "motors", "webhook.conf"),
         os.path.join(YOUI, "home", "webhook.conf")]
# The interpreter the live runs use (/proc/<pid>/exe).
PY = os.getenv("PYTHON", "/home/wmc/anaconda3/bin/python")
AUTOSTART = os.getenv("MONITOR_AUTOSTART", "1").strip().lower() not in (
    "0", "false", "no", "n",
)
STALL_MIN = 60   # no new row in this long, while alive, means wedged not slow
# Oldest ETA baseline worth keeping. Longer than one collection burst and the
# rate averages in the rest between bursts; shorter and one slow row swings it.
ANCHOR_MAX_AGE_MIN = 180


WEDGE_TICKS = 2  # consecutive wedged reports before a kill-and-relaunch.
                 # At the 2h cron cadence that is ~4h of silence, so a single
                 # anomalous reading cannot kill a job that is merely slow.


def strike_count(prev_entry: dict, wedged: bool) -> int:
    """Consecutive wedged observations, reset the moment rows move again."""
    return ((prev_entry or {}).get("wedged", 0) + 1) if wedged else 0


def stalled(s: dict, remaining: int) -> bool:
    """No new row in STALL_MIN while work is still outstanding.

    Gated on remaining because a completed job's last row only gets older;
    without the gate every finished product would report as stalled.

    Reads stall_age when set: a relaunch resets what counts as "too long" for
    the *decision*, but must not touch age_min, which the card reports as the
    last row's real age.
    """
    age = s.get("stall_age")
    if age is None:
        age = s["age_min"]
    return remaining > 0 and (age or 0) > STALL_MIN
LBL = 12         # label column — widest row label is "BUILDINGS"


class Job:
    """One product's paths and per-insurer quirks.

    inp is a glob so NRMA's single input CSV and Youi's ten chunk files go
    through one code path. paid is the column that proves a quote came back.
    terminal_prefixes None means any failure reason is terminal (Youi writes
    only settled reasons); NRMA distinguishes a bot block worth retrying from
    a genuine decline. script None means "report, don't relaunch".
    """

    def __init__(self, insurer, product, inp, outp, paid, pattern,
                 script=None, terminal_prefixes=None, cwd=None,
                 kill_patterns=None, group=None):
        self.insurer, self.product = insurer, product
        # Card rows show the product alone (the insurer heads its block), but
        # state and alerts keep the qualified name so they stay unambiguous.
        self.label = f"{insurer} {product}"
        self.inp, self.outp = inp, outp
        self.paid, self.pattern = paid, pattern
        self.script, self.terminal_prefixes = script, terminal_prefixes
        # Recovery: what to kill before relaunching, from where, and which
        # jobs share one entry point. Youi's two products are chunks of the
        # same run_youi.py fan-out, so they recover together or not at all.
        self.cwd = cwd
        self.kill_patterns = kill_patterns or (pattern,)
        self.group = group or self.label


# Every path comes from period.py: it resolves the period and reads each
# product's real filename out of the drop. A product nobody dropped resolves to
# "" and simply does not appear on the card, so the monitor still renders when a
# period is half-prepared -- which is exactly when the report matters.
PERIOD = period.PERIOD


def _job(insurer, product, **kw):
    return Job(insurer, product,
               period.input_csv(insurer, product) or "",
               period.output_csv(insurer, product) or "", **kw)


JOBS = [
    _job("NRMA", "Buildings",
         paid="NRMA_YearlyPayment", pattern="[b]uilding.py",
         script="launch_free_pool.py building.py",
         terminal_prefixes=TERMINAL_REASON_PREFIXES),
    _job("NRMA", "Contents",
         paid="NRMA_YearlyPayment", pattern="[c]ontents.py",
         script="launch_free_pool.py contents.py",
         terminal_prefixes=TERMINAL_REASON_PREFIXES),
    # Youi home is counted against its input *chunks*, because run_youi.py fans
    # the drop out across them and the un-chunked master is not what runs.
    # Orchestrator first in kill_patterns: it respawns runners, so killing a
    # runner while run_youi.py lives just gets it replaced. Both products name
    # the same group so a tick recovers the fan-out once, not twice.
    Job("Youi", "Buildings",
        period.chunks_glob("Youi", "Buildings") or "",
        period.output_csv("Youi", "Buildings") or "",
        paid="QuoteNumber", pattern="[r]unner.py buildings",
        script="run_youi.py", cwd=HERE, group="youi",
        kill_patterns=("[r]un_youi.py", "[r]unner.py")),
    Job("Youi", "Contents",
        period.chunks_glob("Youi", "Contents") or "",
        period.output_csv("Youi", "Contents") or "",
        paid="QuoteNumber", pattern="[r]unner.py contents",
        script="run_youi.py", cwd=HERE, group="youi",
        kill_patterns=("[r]un_youi.py", "[r]unner.py")),
    # Motor is its own entry point, not part of the run_youi.py home fan-out,
    # so it gets its own group. It stays report-only (script=None) because
    # run_motors_v2.py still carries its own hardcoded period.
    _job("Youi", "Motor",
         paid="QuoteNumber", pattern="[r]un_motors_v2.py", group="youi-motor"),
    # RACV is collected outside this repo -- there is no scraper here to relaunch,
    # so every RACV job is report-only (script=None). Home writes two excess
    # variants; _1 is the one that proves a quote came back.
    _job("RACV", "Buildings", paid="RACV_YearlyPayment_1", pattern="[r]acv_buildings"),
    _job("RACV", "Contents", paid="RACV_YearlyPayment_1", pattern="[r]acv_contents"),
    _job("RACV", "Motor", paid="RACV_YearlyPayment", pattern="[r]acv_motor"),
]

# building.py and contents.py live next to each other; restart() runs there.
SCRIPT_DIR = os.path.join(NRMA, "home")


def webhook_url() -> str:
    url = os.getenv("MONITOR_WEBHOOK_URL", "").strip()
    if url:
        # Set but not a URL (MONITOR_WEBHOOK_URL=-) means "don't post" — the conf
        # fallback must not override an explicit opt-out.
        return url if url.startswith("http") else ""
    for conf in CONFS:
        if not os.path.exists(conf):
            continue
        with open(conf, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("http"):   # skips comments + placeholder
                    return line
    return ""


def sample_id(row: dict) -> str:
    """The id column is Sample_Number on NRMA and in the Youi inputs, but
    sample_number in the Youi outputs. Match on case, not on spelling."""
    for key, val in row.items():
        if key and key.lower() == "sample_number":
            return (val or "").strip()
    return ""


def classify(row: dict, paid: str, terminal_prefixes=None) -> str:
    """Bucket one output row the way the scraper's _row_is_complete() would.

    ok + terminal == "collected" (a resume pass skips them); retrying is a
    transient failure the next pass re-runs.
    """
    reason = (row.get("Reason_for_quote_failure") or "").strip()
    if reason and reason.lower() not in ("nan", "none", "n/a", "na"):
        if terminal_prefixes is None:
            return "terminal"
        return "terminal" if reason.startswith(terminal_prefixes) else "retrying"
    return "ok" if (row.get(paid) or "").strip() else "retrying"


def job_stats(job: Job) -> dict:
    samples = set()
    for path in glob.glob(job.inp):
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                samples.add(sample_id(row))
    samples.discard("")

    counts = {"ok": 0, "terminal": 0, "retrying": 0}
    age = None
    if os.path.exists(job.outp):
        seen = set()
        with open(job.outp, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                sid = sample_id(row)
                if not sid or sid in seen or (samples and sid not in samples):
                    continue
                seen.add(sid)
                counts[classify(row, job.paid, job.terminal_prefixes)] += 1
        # mtime is the last row written: catches a wedged-but-alive process,
        # which the row count alone cannot tell apart from a dead one.
        age = (datetime.datetime.now().timestamp() - os.path.getmtime(job.outp)) / 60

    total = len(samples)
    done = counts["ok"] + counts["terminal"]
    return {
        "total": total, "done": done, **counts,
        "pending": max(total - done - counts["retrying"], 0),
        "pct": (100 * done / total) if total else 0,
        "age_min": age,
    }


def _argv_matches(args: str, tokens: list[str]) -> bool:
    """True when `args` is an interpreter running the script named by tokens.

    tokens is the de-bracketed pattern split on spaces: ["contents.py"] or
    ["runner.py", "buildings"]. The script must appear as a whole argv element
    (by basename, since it may be an absolute path) and every extra token must
    appear as an element too.
    """
    argv = args.split()
    if not argv or "python" not in os.path.basename(argv[0]):
        return False
    script, extra = tokens[0], tokens[1:]
    if not any(os.path.basename(a) == script for a in argv[1:]):
        return False
    return all(e in argv[1:] for e in extra)


def running(pattern: str) -> bool:
    """Is this job's process up?

    Not `pgrep -f`, which matches the pattern anywhere in any command line —
    including a shell whose own arguments merely mention the script. That bit
    on 2026-09-10: a monitoring one-liner mentioning `contents.py` made the card
    report the scraper alive while nothing was running, and a job believed alive
    is never relaunched, so the next collection window would silently never
    open. A false "alive" is the most expensive wrong answer this function can
    give, so it matches argv elements instead of a substring.
    """
    tokens = pattern.replace("[", "").replace("]", "").split()
    if not tokens:
        return False
    try:
        out = subprocess.run(["ps", "-eo", "args="],
                             capture_output=True, text=True).stdout
    except Exception:
        return False
    return any(_argv_matches(line, tokens) for line in out.splitlines())


# NRMA health, for a card that has to be readable with nobody at the desk.
# Row counts alone cannot distinguish "collecting slowly" from "walled": a
# walled run still writes rows, they are just all failures. launches/row is the
# number that separates them — ~2.8 healthy, 15-23 walled (Decisions.md
# 2026-09-10) — so the card carries it rather than leaving it to be grepped.
NRMA_LOG = os.path.join(NRMA, "home", "scraper.log")
HEALTH_WINDOW_MIN = 60
HEALTHY_LAUNCHES_PER_ROW = 6.0   # generous: healthy measured 2.8, walled 15+
LOG_TAIL_BYTES = 4_000_000       # the log runs to tens of MB; only the tail matters


def nrma_recent(minutes: int = HEALTH_WINDOW_MIN) -> tuple[int, int]:
    """(camoufox launches, priced rows) in the last `minutes` of the NRMA log."""
    try:
        size = os.path.getsize(NRMA_LOG)
        with open(NRMA_LOG, "rb") as fh:
            if size > LOG_TAIL_BYTES:
                fh.seek(size - LOG_TAIL_BYTES)
                fh.readline()  # drop the partial line
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return 0, 0
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    launches = rows = 0
    for line in tail.splitlines():
        if line[:19] < cutoff:
            continue
        if "launching camoufox" in line:
            launches += 1
        elif "✅" in line and "done" in line:
            rows += 1
    return launches, rows


def nrma_pools() -> list[tuple[str, float]]:
    """Each proxy pool and the hours of breaker cooldown it has left."""
    try:
        from nrma_browser import PROXY_POOLS, pool_rest_hours
    except Exception:
        return []
    return [(p, pool_rest_hours(p)) for p in PROXY_POOLS]


def nrma_resting() -> bool:
    """True when the breaker has parked every pool, so NRMA cannot run yet."""
    pools = nrma_pools()
    return bool(pools) and all(rest for _, rest in pools)


def nrma_health_lines(alive: bool) -> tuple[list[str], str | None]:
    """Card lines describing collection health, plus an alert when it is bad.

    Three states, because they need three different reactions and a row count
    tells them apart from none of each other:

      collecting — a scraper is up and pricing. Nothing to do.
      WALLED     — a scraper is up and every quote is refused. Alert: it is
                   burning requests at zero yield.
      resting    — nothing up because the breaker parked every pool. This is
                   the designed response to a wall, so it must NOT alert, or an
                   overnight rest posts an alarm every two hours for a handled
                   situation.
      idle       — nothing up while a pool is free. That is a real fault: a
                   cron tick should have launched.
    """
    launches, rows = nrma_recent()
    pools = nrma_pools()
    free = [p for p, rest in pools if not rest]
    pool_txt = " · ".join(
        f"{p} {'free' if not rest else f'resting {rest:.1f}h'}" for p, rest in pools
    ) or "unknown"
    per_row = (launches / rows) if rows else None
    seen = (f"{launches} launches → {rows} rows"
            + (f" ({per_row:.1f}/row)" if per_row else ""))

    alert = None
    if alive:
        healthy = per_row is not None and per_row <= HEALTHY_LAUNCHES_PER_ROW
        state = "collecting" if healthy else "WALLED"
        if not healthy:
            alert = (f"⛔ *NRMA* walled — {seen} in the last "
                     f"{HEALTH_WINDOW_MIN}m. Rows are still being written, but "
                     f"they are failures.")
    elif pools and not free:
        state = "resting"
        seen += f"; next pool free in {min(r for _, r in pools):.1f}h"
    else:
        state = "idle"
        alert = (f"🔴 *NRMA* nothing running while {', '.join(free) or 'a pool'} "
                 f"is free — a cron tick should have launched it")

    mark = {"collecting": "🟢", "WALLED": "⛔", "resting": "😴", "idle": "🔴"}[state]
    return ([f"{'':<{LBL}}{mark} {state}: {seen}",
             f"{'':<{LBL}}pools: {pool_txt}"], alert)


def load_state() -> dict:
    """Last tick's per-job numbers — for THIS period only.

    Keyed by period because the job labels ("NRMA Contents") repeat every run:
    without it, the first tick of a new period measures its rate against the
    previous period's finished total and reports a nonsense delta like
    "+-934 in 230m". A new period therefore starts from a clean slate, and an
    old one keeps its history if it is ever re-opened.
    """
    try:
        with open(STATE, encoding="utf-8") as fh:
            saved = json.load(fh)
    except Exception:
        return {}
    # A pre-2026-09-22 file is a flat {label: {...}} that names no period, so
    # there is no way to tell which run it describes — discard it rather than
    # credit it to this one. Costs one tick of baseline, then self-heals.
    if saved and all(isinstance(v, dict) and "done" in v for v in saved.values()):
        return {}
    return saved.get(PERIOD, {})


def save_state(state: dict) -> None:
    """Merge this period's state back, leaving other periods' entries alone."""
    try:
        with open(STATE, encoding="utf-8") as fh:
            saved = json.load(fh)
        if saved and all(isinstance(v, dict) and "done" in v for v in saved.values()):
            saved = {}  # drop the pre-period-keyed layout on first write
    except Exception:
        saved = {}
    saved[PERIOD] = state
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(saved, fh, indent=2)


def restart(script: str, cwd: str = None) -> bool:
    """Relaunch a scraper, detached so it outlives this tick."""
    try:
        # script may carry arguments ("launch_free_pool.py contents.py"), so it
        # is split rather than passed as one argv element.
        # Not DEVNULL: the 2026-09-14 14:00 relaunch died after 2 profiles with
        # no ⛔ and no traceback, and there was nothing to read afterwards.
        log = open(os.path.join(cwd or SCRIPT_DIR, "restart_stdout.log"),
                   "a", encoding="utf-8")
        subprocess.Popen(
            [PY, *script.split()], cwd=cwd or SCRIPT_DIR, start_new_session=True,
            stdout=log, stderr=subprocess.STDOUT,
        )
        log.close()  # the child keeps its own dup of the fd
        return True
    except Exception as e:
        print(f"[monitor] restart {script} failed: {e}")
        return False


def kill_pattern(pattern: str, grace: float = 5.0) -> int:
    """SIGTERM every match, SIGKILL whatever outlives grace. Returns matches.

    Counted before signalling: pgrep after the fact reports survivors, and the
    alert needs to say how much was torn down.
    """
    try:
        found = subprocess.run(["pgrep", "-f", pattern],
                               capture_output=True, text=True).stdout.split()
        if not found:
            return 0
        subprocess.run(["pkill", "-TERM", "-f", pattern], capture_output=True)
        deadline = time.time() + grace
        while time.time() < deadline:
            if subprocess.run(["pgrep", "-f", pattern],
                              capture_output=True).returncode != 0:
                return len(found)
            time.sleep(0.5)
        subprocess.run(["pkill", "-KILL", "-f", pattern], capture_output=True)
        return len(found)
    except Exception as e:
        print(f"[monitor] kill {pattern} failed: {e}")
        return 0


def recover(job: Job) -> tuple:
    """Kill whatever is left of a job, then relaunch its entry point.

    A wedged process must be killed before the relaunch, not after: it holds a
    dead browser session but still answers pgrep, so relaunching around it
    would leave two scrapers appending to the same CSV.
    """
    killed = sum(kill_pattern(pat) for pat in job.kill_patterns)
    return killed, restart(job.script, job.cwd)


def bar(pct: float, width: int = 22) -> str:
    filled = round(pct * width / 100)
    return "▓" * filled + "░" * (width - filled)


def _job_block(label: str, s: dict, was: dict, now, remaining: int) -> list[str]:
    """Three monospace lines for one product. Emoji stay out — they break the
    column alignment Google Chat gives inside a ``` block."""
    block = [
        f"{label.upper():<{LBL}}{s['pct']:>5.1f}%  {bar(s['pct'])}  "
        f"{s['done']}/{s['total']}",
        f"{'':<{LBL}}ok {s['ok']} · terminal {s['terminal']} · "
        f"retrying {s['retrying']} · todo {s['pending']}",
    ]
    rate = []
    if was:
        delta = s["done"] - was["done"]
        mins = (now.timestamp() - was["ts"]) / 60
        if mins >= 1:
            rate.append(f"+{delta} in {mins:.0f}m")
    # ETA off a single tick swings by 10x on a job this slow (one row can take
    # 2 min), so measure against the oldest observation instead. A stalled job
    # is excluded: its anchor span keeps growing while gained stands still, so
    # the ETA would drift downward and read as progress.
    anchor = (was or {}).get("anchor")
    if anchor and remaining and not stalled(s, remaining):
        gained = s["done"] - anchor["done"]
        span = (now.timestamp() - anchor["ts"]) / 60
        if gained > 0 and span >= 20:
            rate.append(f"eta {remaining * span / gained / 60:.1f}h "
                        f"(at {gained / span * 60:.0f}/h)")
    if s["age_min"] is not None:
        rate.append(f"last row {s['age_min']:.0f}m ago")
    if rate:
        block.append(f"{'':<{LBL}}" + " · ".join(rate))
    return block


def build_message(persist: bool = True) -> str:
    now = datetime.datetime.now()
    prev, state = load_state(), {}
    alerts, out = [], []
    # One entry point may back several jobs (Youi), so recover it once a tick —
    # a second relaunch would kill the runners the first just started.
    recovered = set()
    grand_done = grand_total = 0

    # One block per insurer: its liveness heads the block, its products fill
    # it, its own subtotal closes it. Reading two competitors off one flat
    # list meant counting rows to work out where NRMA stopped and Youi began.
    for insurer in dict.fromkeys(j.insurer for j in JOBS):
        heads, rows, ins_alerts = [], [], []
        ins_done = ins_total = ins_remaining = 0
        ins_alive = False

        for job in (j for j in JOBS if j.insurer == insurer):
            label = job.label
            s_ = job_stats(job)
            alive = running(job.pattern)
            ins_done += s_["done"]
            ins_total += s_["total"]
            # Retryable rows are real remaining work, so they justify a relaunch.
            # ponytail: a row that keeps failing non-terminally relaunches the
            # scraper every tick forever — cap it with an attempt count in
            # monitor_state.json if that shows up in the log.
            remaining = s_["pending"] + s_["retrying"]
            ins_remaining += remaining

            # A relaunch never touches the output CSV, so its mtime still
            # names a row written before the restart. Counting the stall from
            # there would strike a job that has only just started -- but the
            # card must still report how old the last real row is, so the
            # restart only shortens the clock the decision uses.
            rec_ts = (prev.get(label) or {}).get("recovered_ts")
            if rec_ts and s_["age_min"] is not None:
                s_["stall_age"] = min(s_["age_min"],
                                      (now.timestamp() - rec_ts) / 60)

            ins_alive = ins_alive or alive
            wedged = alive and stalled(s_, remaining)
            strikes = strike_count(prev.get(label), wedged)
            # Recovery needs an entry point and outstanding work to justify it.
            can_fix = bool(AUTOSTART and job.script and remaining)

            # Buildings no longer waits on Contents: 202609 Contents shipped
            # 2026-09-18, which was this deferral's stated exit condition, and
            # Buildings' own 4 non-terminal rows are now wanted too. Kept as a
            # dead False so the "⏸ holding" branch below stays reachable code
            # if the next run needs the deferral back.
            deferred = False

            if can_fix and wedged and strikes >= WEDGE_TICKS:
                # Alive but no new row for WEDGE_TICKS ticks: wedged, not slow.
                # A row takes 60-120s even with jitter and ban retries, so this
                # process is holding a dead session and must be killed first.
                if job.group in recovered:
                    alive, wedged, strikes, rec_ts = True, False, 0, now.timestamp()
                else:
                    killed, ok = recover(job)
                    if ok:
                        recovered.add(job.group)
                        rec_ts = now.timestamp()
                        ins_alerts.append(
                            f"♻ *{label}* wedged {s_['age_min']:.0f}m — killed "
                            f"{killed} proc, relaunched `{job.script}`")
                        alive, wedged, strikes = True, False, 0
            elif can_fix and not alive:
                if job.group in recovered:
                    alive, strikes, rec_ts = True, 0, now.timestamp()
                elif recover(job)[1]:
                    recovered.add(job.group)
                    rec_ts = now.timestamp()
                    ins_alerts.append(f"♻ *{label}* was dead — relaunched `{job.script}`")
                    alive, strikes = True, 0
            elif deferred:
                # Not a fault: an unexplained red line here would read as one.
                ins_alerts.append(
                    f"⏸ *{label}* holding {remaining} rows until Contents lands "
                    f"— one reputation pool, Contents has the budget")
            elif not alive and remaining:
                # A parked NRMA job is the breaker working, not a fault, and the
                # health line already says so — a red alert here would cry wolf
                # every two hours for the whole rest.
                if not (job.insurer == "NRMA" and nrma_resting()):
                    ins_alerts.append(
                        f"🔴 *{label}* not running, {remaining} rows outstanding")
            elif wedged:
                # Below the strike threshold: report, do not touch it yet.
                ins_alerts.append(
                    f"⚠️ *{label}* alive but no new row in {s_['age_min']:.0f}m"
                    f" (strike {strikes}/{WEDGE_TICKS})")
            elif not remaining:
                ins_alerts.append(f"✅ *{label}* complete")

            # 🟡 is the case that used to hide: the process is up, the work
            # is not moving. ⚪ stays "not running", which for a finished job
            # is the correct and expected state.
            mark = "🟡" if wedged else "🟢" if alive else "⚪"
            heads.append(f"{mark} {job.product.lower()}")
            rows += _job_block(job.product, s_, prev.get(label), now, remaining)
            # Carry the oldest observation forward as the ETA baseline. A restart
            # truncates incomplete rows from the CSV, so done can fall — re-anchor
            # rather than report a negative rate.
            # A resuming scraper truncates the output CSV and rewrites it from
            # the preserved rows, so a tick can land mid-rewrite and read almost
            # nothing. That is a read artefact, not progress — and anchoring on
            # it poisons the ETA permanently, because the "done fell" rule below
            # never fires again once done climbs back past the bogus value.
            # Observed 2026-09-10: anchor done=1 against a real 657, reporting
            # "eta 91.3h (at 3/h)" while the run was doing ~30/h.
            was_done = (prev.get(label) or {}).get("done")
            truncated = (was_done or 0) > 10 and s_["done"] < was_done / 2

            anchor = (prev.get(label) or {}).get("anchor")
            # Re-anchor a stale baseline too. NRMA now runs in ~5h bursts
            # separated by rests, so an anchor spanning a rest averages a dead
            # window into the rate and reports an ETA off by an order of
            # magnitude. Three hours keeps the ETA describing the current burst.
            stale = bool(anchor) and (
                (now.timestamp() - anchor["ts"]) / 60 > ANCHOR_MAX_AGE_MIN
            )
            if not truncated and (
                not anchor or stale or s_["done"] < anchor["done"]
            ):
                anchor = {"done": s_["done"], "ts": now.timestamp()}
            if truncated:
                # Keep the previous tick's numbers as the baseline; this tick's
                # reading is not evidence about progress either way.
                s_ = {**s_, "done": was_done}
            state[label] = {"done": s_["done"], "ts": now.timestamp(),
                            "clock": now.strftime("%H:%M"), "anchor": anchor,
                            "wedged": strikes, "recovered_ts": rec_ts}

        grand_done += ins_done
        grand_total += ins_total
        ins_pct = (100 * ins_done / ins_total) if ins_total else 0

        # A finished competitor collapses to one line. Youi and RACV landed on
        # 09-07 and have nothing left to decide, but they still cost four fifths
        # of the card — three progress bars and six "✅ complete" alerts each —
        # which buried the one product still collecting. Their per-product
        # alerts go with them: every one of them said "complete", which is
        # exactly what the collapsed line says once.
        if ins_total and not ins_remaining:
            out.append(f"✅ *{insurer}* complete — {ins_done}/{ins_total} · "
                       + " · ".join(j.product.lower() for j in JOBS
                                    if j.insurer == insurer))
            continue

        alerts += ins_alerts
        # NRMA is the only insurer whose collection can be walled rather than
        # merely slow, and the two look identical in a row count.
        if insurer == "NRMA":
            health, health_alert = nrma_health_lines(ins_alive)
            rows += health
            if health_alert:
                alerts.append(health_alert)
        rows.append("─" * (LBL + 39))
        rows.append(f"{insurer.upper():<{LBL}}{ins_pct:>5.1f}%  {bar(ins_pct)}  "
                    f"{ins_done}/{ins_total}")
        out += [f"*{insurer}*   {' · '.join(heads)}", "```", *rows, "```"]

    # A print-only run must not move the baseline the scheduled run measures
    # its deltas against, or eyeballing the card resets the clock on it.
    if persist:
        try:
            save_state(state)
        except Exception as e:
            print(f"[monitor] state write failed: {e}")

    overall = (100 * grand_done / grand_total) if grand_total else 0
    return "\n".join([
        f"*Home {PERIOD}* — {now:%a %d %b %H:%M}",
        *out,
        f"*Total*  {grand_done}/{grand_total}  ({overall:.1f}%)",
        *alerts,
    ])


def _selftest():
    NP = TERMINAL_REASON_PREFIXES
    cases = [
        ({"Reason_for_quote_failure": "", "NRMA_YearlyPayment": "3655.96"},
         "NRMA_YearlyPayment", NP, "ok"),
        ({"Reason_for_quote_failure": "SumInsuredOutOfRange|contents|low|target=76000",
          "NRMA_YearlyPayment": ""}, "NRMA_YearlyPayment", NP, "terminal"),
        ({"Reason_for_quote_failure": "No address found: 32 FAKE ST",
          "NRMA_YearlyPayment": ""}, "NRMA_YearlyPayment", NP, "terminal"),
        ({"Reason_for_quote_failure": "We don't cover properties in your area",
          "NRMA_YearlyPayment": ""}, "NRMA_YearlyPayment", NP, "terminal"),
        ({"Reason_for_quote_failure": "POST quote/QTEN00225 → 403: Akamai bot block",
          "NRMA_YearlyPayment": ""}, "NRMA_YearlyPayment", NP, "retrying"),
        # written but priceless: incomplete, the next pass re-runs it
        ({"Reason_for_quote_failure": "", "NRMA_YearlyPayment": ""},
         "NRMA_YearlyPayment", NP, "retrying"),
        ({}, "NRMA_YearlyPayment", NP, "retrying"),
        # Youi: no prefix list, so any reason is settled. "N/A" is Youi's
        # literal no-failure marker, not a reason.
        ({"Reason_for_quote_failure": "N/A", "QuoteNumber": "Q123"},
         "QuoteNumber", None, "ok"),
        ({"Reason_for_quote_failure": "Address Not Found", "QuoteNumber": ""},
         "QuoteNumber", None, "terminal"),
        ({"Reason_for_quote_failure": "", "QuoteNumber": ""},
         "QuoteNumber", None, "retrying"),
    ]
    for row, paid, prefixes, want in cases:
        got = classify(row, paid, prefixes)
        assert got == want, f"{row} → {got}, want {want}"

    # ok + terminal must equal the NRMA scraper's own _row_is_complete verdict.
    def row_is_complete(row):
        reason = (row.get("Reason_for_quote_failure") or "").strip()
        if reason:
            return reason.startswith(NP)
        return bool((row.get("NRMA_YearlyPayment") or "").strip())

    for row, paid, prefixes, _ in cases:
        if prefixes is None:
            continue
        assert (classify(row, paid, prefixes) in ("ok", "terminal")
                ) == row_is_complete(row), row

    # Both id spellings resolve; neither insurer's header is assumed.
    assert sample_id({"Sample_Number": " 41837.2025 "}) == "41837.2025"
    assert sample_id({"sample_number": "41837.2025"}) == "41837.2025"
    assert sample_id({"PROFILE": "x"}) == ""

    # 60 rows in 120 min = 30/h, so 100 remaining is 3.3h away.
    now = datetime.datetime.now()
    s = {"pct": 6.0, "done": 60, "total": 1000, "ok": 60, "terminal": 0,
         "retrying": 0, "pending": 100, "age_min": 2.0}
    was = {"done": 57, "ts": now.timestamp() - 1800,
           "anchor": {"done": 0, "ts": now.timestamp() - 7200}}
    line = _job_block("Test", s, was, now, 100)[2]
    assert "+3 in 30m" in line and "eta 3.3h" in line and "at 30/h" in line, line
    # too short a span to trust: delta shown, ETA withheld
    short = {"done": 57, "ts": now.timestamp() - 300,
             "anchor": {"done": 57, "ts": now.timestamp() - 300}}
    assert "eta" not in _job_block("Test", s, short, now, 100)[2]

    # A stalled job must not keep quoting an ETA off a rate it stopped earning.
    wedged = {**s, "age_min": 600.0}
    assert "eta" not in _job_block("Test", wedged, was, now, 100)[2]
    assert stalled(wedged, 100) and not stalled(s, 100)
    # ...but a finished job's ageing last row is not a stall.
    assert not stalled(wedged, 0)

    # ── ETA baseline rules (the number the deadline gets judged on) ─────────
    def rebase(was_done, done, anchor_done, anchor_age_min):
        """Mirror of the anchor decision in build_message, for assertion."""
        truncated = (was_done or 0) > 10 and done < was_done / 2
        anchor = {"done": anchor_done, "ts": now.timestamp() - anchor_age_min * 60}
        stale = (now.timestamp() - anchor["ts"]) / 60 > ANCHOR_MAX_AGE_MIN
        if not truncated and (stale or done < anchor["done"]):
            anchor = {"done": done, "ts": now.timestamp()}
        return anchor["done"], truncated

    # A tick landing while a resuming scraper rewrites the CSV reads almost no
    # rows. It must never become the baseline — that is what reported
    # "eta 91.3h (at 3/h)" on 2026-09-10 during a ~30/h burst, and the old
    # "done fell" rule could never undo it once done climbed back past 1.
    assert rebase(657, 1, 600, 10) == (600, True)
    # Genuine progress leaves a healthy anchor alone.
    assert rebase(600, 657, 600, 10) == (600, False)
    # A real truncation (resume drops incomplete rows) still re-anchors.
    assert rebase(600, 590, 600, 10) == (590, False)
    # A baseline older than one burst is re-anchored so the rate describes the
    # burst in progress, not the rest before it.
    assert rebase(600, 657, 600, ANCHOR_MAX_AGE_MIN + 1) == (657, False)
    assert rebase(600, 657, 600, ANCHOR_MAX_AGE_MIN - 1) == (600, False)
    # A first tick, or a tiny early count, must not be mistaken for truncation.
    assert rebase(None, 3, 3, 10) == (3, False)
    assert rebase(8, 1, 8, 10) == (1, False)

    # ── Liveness must match argv, not any command line mentioning the script ──
    # The bug this guards: a shell whose arguments merely mentioned the scraper
    # made `pgrep -f` report it alive, and a job believed alive is never
    # relaunched — so the next collection window would silently never open.
    SCRIPT = "cont" + "ents.py"   # split so this file never self-matches
    RUNNER = "run" + "ner.py"
    py = "/home/wmc/anaconda3/bin/python"
    assert _argv_matches(f"{py} {SCRIPT}", [SCRIPT])
    assert _argv_matches(f"{py} /abs/path/{SCRIPT}", [SCRIPT])
    # A wrapper that execs the scraper still counts (launch_free_pool.py).
    assert _argv_matches(f"{py} launch_free_pool.py {SCRIPT}", [SCRIPT])
    # A shell merely talking about it does not.
    assert not _argv_matches(f"/bin/bash -c grep {SCRIPT} scraper.log", [SCRIPT])
    assert not _argv_matches(f"/bin/bash -c 'ps | awk $2=={SCRIPT}'", [SCRIPT])
    # Nor an editor, nor a substring of a longer name.
    assert not _argv_matches(f"vim {SCRIPT}", [SCRIPT])
    assert not _argv_matches(f"{py} my_{SCRIPT}", [SCRIPT])
    # Multi-token patterns need every token present as its own argv element.
    assert _argv_matches(f"{py} {RUNNER} buildings", [RUNNER, "buildings"])
    assert not _argv_matches(f"{py} {RUNNER} contents", [RUNNER, "buildings"])
    assert not _argv_matches("", [SCRIPT])

    # Strike gate: a wedge must persist WEDGE_TICKS ticks before a kill, and
    # one moving row wipes the count so a slow job is never torn down.
    assert strike_count(None, True) == 1
    assert strike_count({"wedged": 1}, True) == 2 >= WEDGE_TICKS
    assert strike_count({"wedged": 1}, False) == 0
    assert strike_count({"wedged": 7}, False) == 0
    assert strike_count({}, True) < WEDGE_TICKS, "first wedge must not kill"

    # A just-relaunched job reads stale on the CSV's mtime; the recovery stamp
    # is what keeps it from being struck for a row written before the restart.
    fresh = {**s, "age_min": 1514.0}
    since_recovery = (now.timestamp() - (now.timestamp() - 120)) / 60
    assert stalled(fresh, 100), "stale mtime alone looks wedged"
    fresh["stall_age"] = since_recovery
    assert not stalled(fresh, 100), "recovery stamp must clear the stall"
    # ...without rewriting what the card reports as the last row's real age.
    assert fresh["age_min"] == 1514.0, "display age must survive the clamp"
    assert f"last row {fresh['age_min']:.0f}m ago" in " ".join(
        _job_block("Test", fresh, was, now, 100)), "card shows the true age"

    # Youi's two products share one entry point, so they must share a group or
    # a single tick would relaunch run_youi.py twice.
    # Only the home pair rides run_youi.py; Motor is a separate entry point.
    youi = [j for j in JOBS if j.insurer == "Youi" and j.script == "run_youi.py"]
    assert len({j.group for j in youi}) == 1, "Youi home jobs must recover as one"
    assert {j.product for j in youi} == {"Buildings", "Contents"}
    assert youi[0].kill_patterns[0] == "[r]un_youi.py", "orchestrator dies first"
    # Report-only jobs must never be relaunched — no script, nothing to kill.
    for j in JOBS:
        if j.insurer == "RACV" or (j.insurer == "Youi" and j.product == "Motor"):
            assert j.script is None, f"{j.label} must stay report-only"
    # NRMA jobs recover independently and default to killing just themselves.
    nrma = [j for j in JOBS if j.insurer == "NRMA"]
    assert len({j.group for j in nrma}) == len(nrma)
    assert all(j.kill_patterns == (j.pattern,) for j in nrma)
    print(f"[monitor] selftest ok ({len(cases)} rows + ids + rate/stall/recovery)")


def main():
    if "--selftest" in sys.argv:
        _selftest()
        return
    url = webhook_url()
    msg = build_message(persist=bool(url))
    if not url:
        print("[monitor] no webhook URL configured — status:\n" + msg)
        return
    try:
        resp = requests.post(
            url, json={"text": msg},
            headers={"Content-Type": "application/json"}, timeout=20,
        )
        print(f"[monitor] sent ({resp.status_code})")
    except Exception as e:
        print(f"[monitor] webhook post failed: {e}")


if __name__ == "__main__":
    main()

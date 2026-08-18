#!/usr/bin/env python3
"""
NRMA Insurance — Batch Runner with Progress Dashboard
======================================================
Runs Home (Contents/Buildings/Combined) and Motors scrapers
with real-time visual progress tracking.
"""

import logging
import os
import sys
import time
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from NRMA.home.home import run_all_quotes as home_runner, NrmaHomeUnifiedExtractor
from NRMA.motors.motors import run_all_quotes as motor_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("NrmaRunner")


# ═════════════════════════════════════════════════════════════════════════
# Progress Tracker
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class JobStats:
    name: str
    total: int = 0
    completed: int = 0
    skipped: int = 0
    success: int = 0
    failed: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "PENDING"
    executor_name: str = ""


@dataclass
class ProgressTracker:
    """Real-time progress tracker with visual dashboard."""

    jobs: list = field(default_factory=list)
    current_job_idx: int = -1
    global_start: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    current_sample: str = ""
    current_cover: str = ""

    BAR_WIDTH = 30
    REFRESH_INTERVAL = 2.0

    # ANSI Colors
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    MAGENTA = "\033[95m"

    def add_job(
        self, name: str, input_path: str, output_path: str,
        executor_name: str = "",
    ):
        total = 0
        skipped = 0
        try:
            df = pd.read_csv(input_path).convert_dtypes()
            total = len(df)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            pass

        try:
            out_df = pd.read_csv(output_path, dtype=str)
            for _, row in out_df.iterrows():
                sid = str(row.get("Sample_Number", "")).strip()
                if sid:
                    skipped += 1
        except (FileNotFoundError, pd.errors.EmptyDataError):
            pass

        job = JobStats(
            name=name,
            total=total,
            skipped=skipped,
            completed=skipped,
            executor_name=executor_name,
        )
        self.jobs.append(job)
        return job

    def start_job(self, idx: int):
        with self.lock:
            self.current_job_idx = idx
            self.jobs[idx].status = "RUNNING"
            self.jobs[idx].start_time = time.time()
            self.current_sample = ""
            self.current_cover = ""

    def update_job(
        self, idx: int, success: int, failed: int,
        sample: str = "", cover: str = "",
    ):
        with self.lock:
            job = self.jobs[idx]
            job.success = success
            job.failed = failed
            job.completed = job.skipped + success + failed
            if sample:
                self.current_sample = sample
            if cover:
                self.current_cover = cover

    def finish_job(self, idx: int, status: str = "DONE"):
        with self.lock:
            self.jobs[idx].status = status
            self.jobs[idx].end_time = time.time()
            self.current_sample = ""
            self.current_cover = ""

    def _progress_bar(self, current: int, total: int, width: int | None = None) -> str:
        w = width or self.BAR_WIDTH
        if total == 0:
            return f"[{'░' * w}] 0%"
        pct = min(current / total, 1.0)
        filled = int(w * pct)
        bar = "█" * filled + "░" * (w - filled)
        return f"[{bar}] {pct * 100:.1f}%"

    def _status_icon(self, status: str) -> str:
        return {
            "PENDING": f"{self.DIM}⏳{self.RESET}",
            "RUNNING": f"{self.YELLOW}⚡{self.RESET}",
            "DONE": f"{self.GREEN}✅{self.RESET}",
            "FAILED": f"{self.RED}❌{self.RESET}",
            "SKIPPED": f"{self.DIM}⏭️ {self.RESET}",
        }.get(status, "?")

    def _elapsed_str(self, seconds: float) -> str:
        if seconds <= 0:
            return "--:--"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m:02d}m {s:02d}s"

    def _executor_badge(self, name: str) -> str:
        if "home" in name.lower():
            return f"{self.BLUE}🏠 HOME{self.RESET}"
        elif "motor" in name.lower():
            return f"{self.MAGENTA}🚗 MOTOR{self.RESET}"
        return f"{self.DIM}❓ OTHER{self.RESET}"

    def render(self) -> str:
        lines = []
        now = time.time()
        total_elapsed = now - self.global_start if self.global_start else 0

        # Header
        lines.append("")
        lines.append(f"{self.BOLD}{self.CYAN}{'═' * 78}{self.RESET}")
        lines.append(
            f"{self.BOLD}{self.CYAN}"
            f"  🏠🚗 NRMA Insurance — Progress Dashboard"
            f"{self.RESET}"
        )
        lines.append(
            f"{self.DIM}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f"  │  Elapsed: {self._elapsed_str(total_elapsed)}{self.RESET}"
        )
        lines.append(f"{self.CYAN}{'═' * 78}{self.RESET}")

        # Global summary
        total_all = sum(j.total for j in self.jobs)
        done_all = sum(j.completed for j in self.jobs)
        success_all = sum(j.success for j in self.jobs)
        failed_all = sum(j.failed for j in self.jobs)
        skipped_all = sum(j.skipped for j in self.jobs)

        lines.append("")
        lines.append(
            f"  {self.BOLD}Overall Progress{self.RESET}  "
            f"{self._progress_bar(done_all, total_all, 40)}"
        )
        lines.append(
            f"  {self.GREEN}✓ {success_all} ok{self.RESET}  "
            f"{self.RED}✗ {failed_all} fail{self.RESET}  "
            f"{self.DIM}⏭ {skipped_all} skip{self.RESET}  "
            f"{self.WHITE}│ {done_all}/{total_all} total{self.RESET}"
        )

        # Per-job table
        lines.append("")
        lines.append(
            f"  {self.BOLD}"
            f"{'Job':<22} {'Type':<10} {'Progress':<36} {'Stats':<20}"
            f"{self.RESET}"
        )
        lines.append(f"  {'─' * 74}")

        for i, job in enumerate(self.jobs):
            icon = self._status_icon(job.status)
            bar = self._progress_bar(job.completed, job.total, 22)

            # Color bar
            if job.status == "RUNNING":
                bar_c = f"{self.YELLOW}{bar}{self.RESET}"
            elif job.status == "DONE":
                bar_c = f"{self.GREEN}{bar}{self.RESET}"
            elif job.status == "FAILED":
                bar_c = f"{self.RED}{bar}{self.RESET}"
            else:
                bar_c = f"{self.DIM}{bar}{self.RESET}"

            # Type badge
            badge = self._executor_badge(job.executor_name)

            # Stats
            if job.status == "RUNNING":
                elapsed = now - job.start_time
                processed = job.completed - job.skipped
                remaining = job.total - job.completed
                if processed > 0:
                    rate = elapsed / processed
                    eta = rate * remaining
                    stats = (
                        f"{self.GREEN}{job.success}✓{self.RESET} "
                        f"{self.RED}{job.failed}✗{self.RESET} "
                        f"{self.DIM}{rate:.0f}s/q{self.RESET} "
                        f"{self.CYAN}~{self._elapsed_str(eta)}{self.RESET}"
                    )
                else:
                    stats = f"{self.YELLOW}starting...{self.RESET}"
            elif job.status == "DONE":
                elapsed = job.end_time - job.start_time
                stats = (
                    f"{self.GREEN}{job.success}✓{self.RESET} "
                    f"{self.RED}{job.failed}✗{self.RESET} "
                    f"{self.DIM}{self._elapsed_str(elapsed)}{self.RESET}"
                )
            elif job.status == "SKIPPED":
                stats = f"{self.DIM}file not found{self.RESET}"
            else:
                stats = f"{self.DIM}waiting{self.RESET}"

            lines.append(
                f"  {icon} {job.name:<20} {badge:<20} {bar_c}  {stats}"
            )

        # Active job detail
        if 0 <= self.current_job_idx < len(self.jobs):
            active = self.jobs[self.current_job_idx]
            if active.status == "RUNNING":
                lines.append("")
                lines.append(
                    f"  {self.BOLD}{self.YELLOW}"
                    f"▶ Active: {active.name}{self.RESET}"
                    f"  {self._executor_badge(active.executor_name)}"
                )
                processed = active.completed - active.skipped
                pending = active.total - active.completed
                lines.append(
                    f"    Processed: {processed} │ "
                    f"Pending: {pending} │ "
                    f"Skipped: {active.skipped}"
                )
                if self.current_sample:
                    lines.append(
                        f"    {self.CYAN}Current: "
                        f"Sample {self.current_sample}"
                        f"{f' ({self.current_cover})' if self.current_cover else ''}"
                        f"{self.RESET}"
                    )

        lines.append("")
        lines.append(f"{self.CYAN}{'═' * 78}{self.RESET}")
        return "\n".join(lines)

    def print_dashboard(self):
        sys.stdout.write("\033[H\033[J")
        sys.stdout.write(self.render())
        sys.stdout.flush()

    def print_final_summary(self):
        total_elapsed = time.time() - self.global_start

        print(f"\n{self.BOLD}{self.CYAN}{'═' * 78}{self.RESET}")
        print(
            f"{self.BOLD}{self.CYAN}"
            f"  🏁 BATCH RUN COMPLETE"
            f"{self.RESET}"
        )
        print(f"{self.CYAN}{'═' * 78}{self.RESET}")
        print(
            f"  Total time: {self.BOLD}"
            f"{self._elapsed_str(total_elapsed)}{self.RESET}"
        )
        print()
        print(
            f"  {self.BOLD}"
            f"{'Job':<25} {'Type':<8} {'Status':<12} "
            f"{'OK':<6} {'Fail':<6} {'Skip':<6} {'Time':<12}"
            f"{self.RESET}"
        )
        print(f"  {'─' * 76}")

        for job in self.jobs:
            elapsed = (
                (job.end_time - job.start_time)
                if job.end_time else 0
            )
            status_map = {
                "DONE": f"{self.GREEN}✓ DONE{self.RESET}",
                "FAILED": f"{self.RED}✗ FAIL{self.RESET}",
                "SKIPPED": f"{self.DIM}⏭ SKIP{self.RESET}",
            }
            status_str = status_map.get(
                job.status, f"{self.DIM}? {job.status}{self.RESET}"
            )
            type_str = (
                "🏠" if "home" in job.executor_name.lower()
                else "🚗" if "motor" in job.executor_name.lower()
                else "❓"
            )

            print(
                f"  {job.name:<25} {type_str:<8} {status_str:<22} "
                f"{self.GREEN}{job.success:<6}{self.RESET}"
                f"{self.RED}{job.failed:<6}{self.RESET}"
                f"{self.DIM}{job.skipped:<6}{self.RESET}"
                f"{self._elapsed_str(elapsed):<12}"
            )

        total_ok = sum(j.success for j in self.jobs)
        total_fail = sum(j.failed for j in self.jobs)
        total_skip = sum(j.skipped for j in self.jobs)
        print(f"  {'─' * 76}")
        print(
            f"  {self.BOLD}{'TOTAL':<25} {'':8} {'':12} "
            f"{self.GREEN}{total_ok:<6}{self.RESET}"
            f"{self.BOLD}{self.RED}{total_fail:<6}{self.RESET}"
            f"{self.BOLD}{self.DIM}{total_skip:<6}{self.RESET}"
            f"{self.BOLD}{self._elapsed_str(total_elapsed)}{self.RESET}"
        )
        print(f"\n{self.CYAN}{'═' * 78}{self.RESET}\n")


# ═════════════════════════════════════════════════════════════════════════
# Progress-aware job runner (wraps any executor)
# ═════════════════════════════════════════════════════════════════════════

def run_job_with_tracking(
    job_cfg: dict,
    tracker: ProgressTracker,
    job_idx: int,
):
    """
    Run a single job with progress tracking.
    Works with any executor (home_runner, motor_runner, etc.)
    by monitoring the output file for changes.
    """
    tracker.start_job(job_idx)
    job = tracker.jobs[job_idx]
    output_path = job_cfg["output"]

    # Count initial output rows
    initial_rows = 0
    try:
        df = pd.read_csv(output_path, dtype=str)
        initial_rows = len(df)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        pass

    # Background file monitor — polls output CSV for progress
    stop_monitor = threading.Event()

    def monitor_output():
        last_count = initial_rows
        while not stop_monitor.is_set():
            try:
                if os.path.exists(output_path):
                    df = pd.read_csv(output_path, dtype=str)
                    current_count = len(df)
                    if current_count > last_count:
                        # Count successes and failures
                        ok = 0
                        fail = 0
                        for _, row in df.iterrows():
                            reason = str(
                                row.get("Reason_for_quote_failure", "")
                            ).strip()
                            sid = str(
                                row.get("Sample_Number", "")
                            ).strip()
                            if not sid:
                                continue
                            if reason and reason.lower() != "nan":
                                fail += 1
                            else:
                                ok += 1

                        # Subtract skipped (pre-existing)
                        new_ok = max(0, ok - job.skipped)
                        new_fail = fail

                        # Get latest sample info
                        if len(df) > 0:
                            last_row = df.iloc[-1]
                            sample = str(
                                last_row.get("Sample_Number", "")
                            )
                            cover = str(
                                last_row.get("Cover", "")
                            ) if "Cover" in df.columns else ""
                            tracker.update_job(
                                job_idx, new_ok, new_fail,
                                sample=sample, cover=cover,
                            )

                        last_count = current_count
            except Exception:
                pass
            stop_monitor.wait(3.0)

    monitor_thread = threading.Thread(target=monitor_output, daemon=True)
    monitor_thread.start()

    try:
        # Execute the actual runner
        executor = job_cfg["executor"]
        executor(
            csv_path=job_cfg["input"],
            output_path=job_cfg["output"],
            toggle=job_cfg["toggle"],
        )
        tracker.finish_job(job_idx, "DONE")

    except FileNotFoundError as e:
        log.warning(f"Job skipped: {e}")
        tracker.finish_job(job_idx, "SKIPPED")

    except Exception as e:
        log.error(f"Job failed: {type(e).__name__}: {e}")
        tracker.finish_job(job_idx, "FAILED")

    finally:
        stop_monitor.set()
        monitor_thread.join(timeout=5)

    # Final count from file
    try:
        df = pd.read_csv(output_path, dtype=str)
        ok = fail = 0
        for _, row in df.iterrows():
            reason = str(row.get("Reason_for_quote_failure", "")).strip()
            sid = str(row.get("Sample_Number", "")).strip()
            if not sid:
                continue
            if reason and reason.lower() != "nan":
                fail += 1
            else:
                ok += 1
        new_ok = max(0, ok - job.skipped)
        tracker.update_job(job_idx, new_ok, fail)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════
# Configuration
# ═════════════════════════════════════════════════════════════════════════

BASE_INPUT = "/home/wmc/WMC/DataManipulation/Suncorp/NRMA/inputs/test/data"
BASE_OUTPUT = "/home/wmc/WMC/DataManipulation/Suncorp/NRMA/outputs/test"

JOBS = [
    # # ── Motors ──
    # {
    #     "name": "Motors Toggle",
    #     "input": f"{BASE_INPUT}/Motor_NRMA_quarterly_202602_toggle.csv",
    #     "output": f"{BASE_OUTPUT}/Motor_NRMA_quarterly_202602_toggle.csv",
    #     "toggle": True,
    #     "executor": motor_runner,
    #     "executor_name": "motor",
    # },
    # {
    #     "name": "Motors Main",
    #     "input": f"{BASE_INPUT}/Motor_NRMA_quarterly_202605_main.csv",
    #     "output": f"{BASE_OUTPUT}/Motor_NRMA_quarterly_202605_main.csv",
    #     "toggle": False,
    #     "executor": motor_runner,
    #     "executor_name": "motor",
    # },
    # # ── Contents ──
    # {
    #     "name": "Contents Main",
    #     "input": f"{BASE_INPUT}/Contents_NRMA_quarterly_202605_main.csv",
    #     "output": f"{BASE_OUTPUT}/Contents_NRMA_quarterly_202605_main.csv",
    #     "toggle": False,
    #     "executor": home_runner,
    #     "executor_name": "home",
    # },
    {
        "name": "Contents Toggle",
        "input": f"{BASE_INPUT}/Contents_NRMA_quarterly_202605_toggle.csv",
        "output": f"{BASE_OUTPUT}/Contents_NRMA_quarterly_202605_toggle_NEW.csv",
        "toggle": True,
        "executor": home_runner,
        "executor_name": "home",
    },
    # # ── Buildings ──
    # {
    #     "name": "Buildings Main",
    #     "input": f"{BASE_INPUT}/Buildings_NRMA_quarterly_202605_main.csv",
    #     "output": f"{BASE_OUTPUT}/Buildings_NRMA_quarterly_202605_main.csv",
    #     "toggle": False,
    #     "executor": home_runner,
    #     "executor_name": "home",
    # },
    # {
    #     "name": "Buildings Toggle",
    #     "input": f"{BASE_INPUT}/Buildings_NRMA_quarterly_202605_toggle.csv",
    #     "output": f"{BASE_OUTPUT}/Buildings_NRMA_quarterly_202605_toggle.csv",
    #     "toggle": True,
    #     "executor": home_runner,
    #     "executor_name": "home",
    # },
    # ── Combined ──
    {
        "name": "Combined Main",
        "input": f"{BASE_INPUT}/Combined_NRMA_quarterly_202605.csv",
        "output": f"{BASE_OUTPUT}/Combined_NRMA_quarterly_202605_NEW.csv",
        "toggle": False,
        "executor": home_runner,
        "executor_name": "home",
    },


]


# ═════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════

def main():
    tracker = ProgressTracker()
    tracker.global_start = time.time()

    # Register all jobs
    for job_cfg in JOBS:
        tracker.add_job(
            name=job_cfg["name"],
            input_path=job_cfg["input"],
            output_path=job_cfg["output"],
            executor_name=job_cfg.get("executor_name", ""),
        )

    # Initial dashboard
    tracker.print_dashboard()

    # Background dashboard refresh
    stop_refresh = threading.Event()

    def refresh_loop():
        while not stop_refresh.is_set():
            try:
                tracker.print_dashboard()
            except Exception:
                pass
            stop_refresh.wait(tracker.REFRESH_INTERVAL)

    refresh_thread = threading.Thread(target=refresh_loop, daemon=True)
    refresh_thread.start()

    # Execute jobs sequentially
    for i, job_cfg in enumerate(JOBS):
        run_job_with_tracking(
            job_cfg=job_cfg,
            tracker=tracker,
            job_idx=i,
        )
        # Pause between jobs
        if i < len(JOBS) - 1:
            time.sleep(30)

    # Stop refresh
    stop_refresh.set()
    refresh_thread.join(timeout=3)

    # Final summary
    tracker.print_final_summary()


if __name__ == "__main__":
    main()
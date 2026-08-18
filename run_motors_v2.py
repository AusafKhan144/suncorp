import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from datetime import datetime
from dataclasses import dataclass, field
from threading import Lock


# ===== CONFIGURATION =====
WORKER_SCRIPT = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/motors/motors_v2_worker.py"
INPUT_DIR     = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/inputs/20260817/data/chunks"
OUTPUT_DIR    = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/outputs/20260817"
LOG_DIR       = Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/20260817/motors_log/")
PERIOD        = "202608"
BASENAME      = "Motor_YOUI_monthly_202608"

CHUNK_START   = 1
CHUNK_END     = 10
CHUNKS_PARALLEL       = 10
CHUNK_TIMEOUT_SECONDS = 7200000  # 2 hours per chunk
SLEEP_BETWEEN_RECORDS = 2.0


# ===== STATISTICS TRACKING =====
@dataclass
class ExecutionStats:
    success: int = 0
    failed: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def increment_success(self):
        with self._lock:
            self.success += 1

    def increment_failed(self):
        with self._lock:
            self.failed += 1

    def finish(self):
        self.end_time = datetime.now()

    def get_duration(self) -> float:
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()

    def get_summary(self) -> str:
        duration = self.get_duration()
        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)
        total = self.success + self.failed
        return (
            f"\n{'='*80}\n"
            f"EXECUTION SUMMARY\n"
            f"{'='*80}\n"
            f"Total chunks: {total}\n"
            f"Success:      {self.success}\n"
            f"Failed:       {self.failed}\n"
            f"Duration:     {hours}h {minutes}m {seconds}s\n"
            f"{'='*80}\n"
        )


# ===== UTILITY FUNCTIONS =====
def parse_chunks(spec: str) -> list[int]:
    chunks = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            chunks.update(range(int(start.strip()), int(end.strip()) + 1))
        else:
            chunks.add(int(part))
    return sorted(chunks)


def print_header(chunks: list[int], parallel: int):
    print("\n" + "=" * 80)
    print("YOUI MOTORS V2 ORCHESTRATOR")
    print("=" * 80)
    print(f"Worker Script:    {WORKER_SCRIPT}")
    print(f"Input Dir:        {INPUT_DIR}")
    print(f"Output Dir:       {OUTPUT_DIR}")
    print(f"Log Dir:          {LOG_DIR}")
    print(f"Period:           {PERIOD}")
    print(f"Basename:         {BASENAME}")
    print(f"Chunks:           {chunks}")
    print(f"Parallel:         {parallel}")
    print(f"Timeout:          {CHUNK_TIMEOUT_SECONDS} minutes per chunk")
    print(f"Start Time:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")


def setup_log_directory():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Log directory ready: {LOG_DIR}\n")


def validate_worker_script():
    if not Path(WORKER_SCRIPT).exists():
        print(f"ERROR: Worker script not found: {WORKER_SCRIPT}")
        sys.exit(1)
    print(f"Worker script validated: {WORKER_SCRIPT}\n")


# ===== CORE EXECUTION =====
def run_single_chunk(chunk_number: int) -> tuple[bool, int]:
    log_file = LOG_DIR / f"chunk_{chunk_number}.log"
    print(f"[motors] Starting chunk {chunk_number}")

    try:
        with log_file.open("w") as f:
            f.write(f"{'='*60}\n")
            f.write(f"Chunk: {chunk_number}\n")
            f.write(f"Period: {PERIOD}\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n\n")
            f.flush()

            result = subprocess.run(
                [
                    "python3",
                    WORKER_SCRIPT,
                    "--chunk", str(chunk_number),
                    "--input-dir", INPUT_DIR,
                    "--output-dir", OUTPUT_DIR,
                    "--log-dir", str(LOG_DIR),
                    "--period", PERIOD,
                    "--basename", BASENAME,
                    "--sleep", str(SLEEP_BETWEEN_RECORDS),
                ],
                stdout=f,
                stderr=subprocess.STDOUT,
            )

            f.write(f"\n{'='*60}\n")
            f.write(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Return Code: {result.returncode}\n")
            f.write(f"{'='*60}\n")

        if result.returncode == 0:
            print(f"[motors] Chunk {chunk_number} completed successfully")
            return True, chunk_number
        else:
            print(f"[motors] Chunk {chunk_number} failed (code: {result.returncode}) — log: {log_file}")
            return False, chunk_number

    except subprocess.TimeoutExpired:
        print(f"[motors] Chunk {chunk_number} timed out after {CHUNK_TIMEOUT_SECONDS} minutes — log: {log_file}")
        return False, chunk_number
    except Exception as e:
        print(f"[motors] Chunk {chunk_number} exception: {e} — log: {log_file}")
        return False, chunk_number


def run_all_chunks(chunks: list[int], parallel: int, stats: ExecutionStats):
    actual_parallel = min(parallel, len(chunks))
    total = len(chunks)
    completed = 0

    print(f"Processing {total} chunks ({actual_parallel} parallel)\n")

    with ThreadPoolExecutor(max_workers=actual_parallel) as executor:
        futures = {executor.submit(run_single_chunk, chunk): chunk for chunk in chunks}

        for future in as_completed(futures):
            completed += 1
            try:
                success, chunk_number = future.result()
                if success:
                    stats.increment_success()
                else:
                    stats.increment_failed()
            except Exception as e:
                chunk_number = futures[future]
                print(f"[motors] Chunk {chunk_number} unexpected error: {e}")
                stats.increment_failed()

            print(f"[motors] Progress: {completed}/{total} chunks done")

    print(f"\n[motors] All {total} chunks finished")


# ===== MAIN =====
def main():
    chunks = list(range(CHUNK_START, CHUNK_END + 1))
    parallel = CHUNKS_PARALLEL

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ["--chunks", "-c"] and i + 1 < len(sys.argv):
            chunks = parse_chunks(sys.argv[i + 1])
            i += 2
        elif arg in ["--parallel", "-p"] and i + 1 < len(sys.argv):
            parallel = int(sys.argv[i + 1])
            i += 2
        elif arg in ["--help", "-h"]:
            print(
                "Usage: python run_motors_v2.py [--chunks SPEC] [--parallel N]\n"
                "  --chunks   Chunks to run, e.g. '1-10', '1,3,5', '5' (default: all)\n"
                "  --parallel Number of chunks to run simultaneously (default: "
                f"{CHUNKS_PARALLEL})\n"
            )
            sys.exit(0)
        else:
            print(f"Unknown argument: {arg}. Use --help for usage.")
            sys.exit(1)

    try:
        print_header(chunks, parallel)
        validate_worker_script()
        setup_log_directory()

        stats = ExecutionStats()
        run_all_chunks(chunks, parallel, stats)
        stats.finish()

        print(stats.get_summary())
        print(f"Logs saved in: {LOG_DIR}\n")

        if stats.failed > 0:
            print(f"Completed with {stats.failed} failed chunk(s)")
            sys.exit(1)
        else:
            print("All chunks completed successfully!")
            sys.exit(0)

    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple, Optional
from datetime import datetime
from dataclasses import dataclass, field
from threading import Lock


# ===== CONFIGURATION =====
class CrawlerConfig(NamedTuple):
    """Configuration for a single crawler type"""
    crawler_type: str
    filename: str
    log_dir: Path


# Define all crawler configurations
CRAWLER_CONFIGS = [
    # CrawlerConfig(
    #     crawler_type="contents_toggle",
    #     filename="Contents_YOUI_quarterly_202608_toggle",
    #     log_dir=Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/202608/contents_toggle_log/")
    # ),
    # CrawlerConfig(
    #     crawler_type="buildings_toggle",
    #     filename="Buildings_YOUI_quarterly_202608_toggle",
    #     log_dir=Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/202608/buildings_toggle_log/")
    # ),
    # CrawlerConfig(
    #     crawler_type="combined",
    #     filename="Combined_YOUI_quarterly_202608",
    #     log_dir=Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/202608/combined_log/")
    # ),
    CrawlerConfig(
        crawler_type="buildings",
        filename="Buildings_YOUI_monthly_202608_main",
        log_dir=Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/20260817/buildings_log/")
    ),
    CrawlerConfig(
        crawler_type="contents",
        filename="Contents_YOUI_monthly_202608_main",
        log_dir=Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/20260817/contents_log/")
    ),
]

# Path to the runner script
RUNNER_SCRIPT = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/home/runner.py"

# Chunk configuration
CHUNK_START = 1
CHUNK_END = 10
TOTAL_CHUNKS = CHUNK_END - CHUNK_START + 1

# Parallelization settings
CHUNKS_PER_CONFIG = 10  # How many chunks to run simultaneously per config

# Timeout settings
CHUNK_TIMEOUT_SECONDS = 7200 + 7200 + 7200 # 2 hours per chunk


# ===== STATISTICS TRACKING =====
@dataclass
class ExecutionStats:
    """Track execution statistics with thread-safe counters"""
    success: int = 0
    failed: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    _lock: Lock = field(default_factory=Lock, repr=False)
    _config_stats: dict = field(default_factory=dict, repr=False)

    def increment_success(self, config_type: Optional[str] = None):
        """Thread-safe increment for success"""
        with self._lock:
            self.success += 1
            if config_type:
                if config_type not in self._config_stats:
                    self._config_stats[config_type] = {"success": 0, "failed": 0}
                self._config_stats[config_type]["success"] += 1

    def increment_failed(self, config_type: Optional[str] = None):
        """Thread-safe increment for failed"""
        with self._lock:
            self.failed += 1
            if config_type:
                if config_type not in self._config_stats:
                    self._config_stats[config_type] = {"success": 0, "failed": 0}
                self._config_stats[config_type]["failed"] += 1

    def finish(self):
        """Mark execution as finished"""
        self.end_time = datetime.now()

    def get_duration(self) -> float:
        """Get execution duration in seconds"""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()

    def get_summary(self) -> str:
        """Get formatted summary"""
        duration = self.get_duration()
        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)

        total = self.success + self.failed

        summary = (
            f"\n{'='*80}\n"
            f"EXECUTION SUMMARY\n"
            f"{'='*80}\n"
        )

        # Per-config breakdown
        if self._config_stats:
            summary += "Per-Config Results:\n"
            for config_type, stats in sorted(self._config_stats.items()):
                config_total = stats["success"] + stats["failed"]
                summary += (
                    f"  {config_type}:\n"
                    f"    Total:   {config_total}\n"
                    f"    Success: {stats['success']}\n"
                    f"    Failed:  {stats['failed']}\n"
                )
            summary += "\n"

        # Overall stats
        summary += (
            f"Overall:\n"
            f"  Total:     {total}\n"
            f"  Success:   {self.success}\n"
            f"  Failed:    {self.failed}\n"
            f"  Duration:  {hours}h {minutes}m {seconds}s\n"
            f"{'='*80}\n"
        )

        return summary


# ===== UTILITY FUNCTIONS =====
def print_header():
    """Print script header"""
    total_tasks = len(CRAWLER_CONFIGS) * TOTAL_CHUNKS
    max_concurrent = len(CRAWLER_CONFIGS) * CHUNKS_PER_CONFIG

    print("\n" + "="*80)
    print("PARALLEL CONFIG CRAWLER ORCHESTRATOR")
    print("="*80)
    print(f"Runner Script:         {RUNNER_SCRIPT}")
    print(f"Total Configs:         {len(CRAWLER_CONFIGS)}")
    print(f"Chunks per Config:     {TOTAL_CHUNKS} (chunks {CHUNK_START}-{CHUNK_END})")
    print(f"Parallel per Config:   {CHUNKS_PER_CONFIG}")
    print(f"Max Concurrent Jobs:   {max_concurrent}")
    print(f"Total Tasks:           {total_tasks}")
    print(f"Chunk Timeout:         {CHUNK_TIMEOUT_SECONDS // 60} minutes")
    print(f"Start Time:            {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    print("\nExecution Strategy:")
    print(f"  - All {len(CRAWLER_CONFIGS)} configs run in parallel")
    print(f"  - Each config processes {CHUNKS_PER_CONFIG} chunks simultaneously")
    print(f"  - Maximum {max_concurrent} concurrent jobs at any time")
    print("="*80 + "\n")

    print("Configured Crawlers:")
    for idx, config in enumerate(CRAWLER_CONFIGS, 1):
        print(f"  {idx}. {config.crawler_type}")
        print(f"     Filename: {config.filename}")
        print(f"     Log Dir:  {config.log_dir}")
    print()


def setup_log_directories():
    """Create all log directories if they don't exist"""
    print("📂 Setting up log directories...")
    for config in CRAWLER_CONFIGS:
        config.log_dir.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {config.log_dir}")
    print()


def validate_runner_script():
    """Validate that the runner script exists"""
    runner_path = Path(RUNNER_SCRIPT)
    if not runner_path.exists():
        print(f"❌ ERROR: Runner script not found: {RUNNER_SCRIPT}")
        sys.exit(1)
    print(f"✓ Runner script validated: {RUNNER_SCRIPT}\n")


def parse_chunks(chunk_arg: str) -> list[int]:
    """
    Parse chunk argument into list of chunk numbers

    Supports formats:
        - "1-10"     -> [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        - "1,3,5,7"  -> [1, 3, 5, 7]
        - "2-5,8,10" -> [2, 3, 4, 5, 8, 10]
        - "5"        -> [5]

    Args:
        chunk_arg: Chunk specification string

    Returns:
        List of chunk numbers
    """
    chunks = set()

    for part in chunk_arg.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = int(start.strip())
            end = int(end.strip())
            chunks.update(range(start, end + 1))
        else:
            chunks.add(int(part))

    return sorted(chunks)


# ===== CORE EXECUTION FUNCTIONS =====
def run_single_chunk(
    config: CrawlerConfig,
    chunk_number: int
) -> tuple[bool, str, int]:
    """
    Run a single chunk for a specific crawler type

    Args:
        config: Crawler configuration
        chunk_number: Chunk number to process

    Returns:
        Tuple of (success, crawler_type, chunk_number)
    """
    log_file = config.log_dir / f"chunk_{chunk_number}.log"

    print(f"[{config.crawler_type}] ▶️  Starting chunk {chunk_number}")

    try:
        with log_file.open("w") as f:
            f.write(f"{'='*60}\n")
            f.write(f"Crawler: {config.crawler_type}\n")
            f.write(f"Chunk: {chunk_number}\n")
            f.write(f"Filename: {config.filename}\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n\n")
            f.flush()

            process = subprocess.run(
                [
                    "python3",
                    RUNNER_SCRIPT,
                    config.crawler_type,
                    str(chunk_number),
                    config.filename
                ],
                stdout=f,
                stderr=subprocess.STDOUT,
                # timeout=CHUNK_TIMEOUT_SECONDS
            )

            f.write(f"\n{'='*60}\n")
            f.write(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Return Code: {process.returncode}\n")
            f.write(f"{'='*60}\n")

        if process.returncode == 0:
            print(f"[{config.crawler_type}] ✅ Chunk {chunk_number} completed successfully")
            return True, config.crawler_type, chunk_number
        else:
            print(f"[{config.crawler_type}] ❌ Chunk {chunk_number} failed (code: {process.returncode})")
            print(f"    └─ Log: {log_file}")
            return False, config.crawler_type, chunk_number

    except subprocess.TimeoutExpired:
        print(f"[{config.crawler_type}] ⏱️  Chunk {chunk_number} timed out after {CHUNK_TIMEOUT_SECONDS // 60} minutes")
        print(f"    └─ Log: {log_file}")
        return False, config.crawler_type, chunk_number

    except Exception as e:
        print(f"[{config.crawler_type}] ❌ Chunk {chunk_number} exception: {e}")
        print(f"    └─ Log: {log_file}")
        return False, config.crawler_type, chunk_number


def run_chunks_for_single_config(
    config: CrawlerConfig,
    chunks: list[int],
    max_parallel: int,
    stats: ExecutionStats
) -> None:
    """
    Run specified chunks for a single config with controlled parallelism

    Args:
        config: Crawler configuration
        chunks: List of chunk numbers to process
        max_parallel: Maximum parallel chunks for this config
        stats: Statistics tracker
    """
    actual_parallel = min(max_parallel, len(chunks))

    print(f"[{config.crawler_type}] 🚀 Starting {len(chunks)} chunks "
          f"({actual_parallel} parallel workers)")
    print(f"[{config.crawler_type}]    Chunks: {chunks}")

    completed = 0
    total = len(chunks)

    with ThreadPoolExecutor(max_workers=actual_parallel) as executor:
        futures = {
            executor.submit(
                run_single_chunk,
                config,
                chunk_number
            ): chunk_number
            for chunk_number in chunks
        }

        for future in as_completed(futures):
            chunk_number = futures[future]
            completed += 1

            try:
                success, crawler_type, _ = future.result()
                if success:
                    stats.increment_success(config.crawler_type)
                else:
                    stats.increment_failed(config.crawler_type)
            except Exception as e:
                print(f"[{config.crawler_type}] ❌ Chunk {chunk_number} unexpected error: {e}")
                stats.increment_failed(config.crawler_type)

            print(f"[{config.crawler_type}] 📊 Progress: {completed}/{total} chunks completed")

    print(f"[{config.crawler_type}] 🏁 All {total} chunks finished")


def run_all_configs_parallel(
    stats: ExecutionStats,
    chunks: Optional[list[int]] = None,
    chunks_per_config: Optional[int] = None
) -> None:
    """
    Run all configs in parallel, each config processes its chunks
    with controlled parallelism

    Args:
        stats: Statistics tracker
        chunks: List of chunk numbers to process (default: all chunks)
        chunks_per_config: Override for CHUNKS_PER_CONFIG (default: use global)
    """
    if chunks is None:
        chunks = list(range(CHUNK_START, CHUNK_END + 1))

    if chunks_per_config is None:
        chunks_per_config = CHUNKS_PER_CONFIG

    total_tasks = len(CRAWLER_CONFIGS) * len(chunks)
    actual_parallel_per_config = min(chunks_per_config, len(chunks))
    max_concurrent = len(CRAWLER_CONFIGS) * actual_parallel_per_config

    print("\n" + "="*80)
    print("STARTING PARALLEL EXECUTION")
    print("="*80)
    print(f"Configs:               {len(CRAWLER_CONFIGS)}")
    print(f"Chunks to process:     {chunks}")
    print(f"Chunks per config:     {len(chunks)}")
    print(f"Parallel per config:   {actual_parallel_per_config}")
    print(f"Max concurrent jobs:   {max_concurrent}")
    print(f"Total tasks:           {total_tasks}")
    print("="*80 + "\n")

    print("📋 Task Breakdown:")
    for config in CRAWLER_CONFIGS:
        print(f"  {config.crawler_type}:")
        print(f"    Chunks: {chunks}")
        print(f"    Parallel: {actual_parallel_per_config}")
    print()

    with ThreadPoolExecutor(max_workers=len(CRAWLER_CONFIGS)) as executor:
        futures = {
            executor.submit(
                run_chunks_for_single_config,
                config,
                chunks,
                actual_parallel_per_config,
                stats
            ): config.crawler_type
            for config in CRAWLER_CONFIGS
        }

        for future in as_completed(futures):
            crawler_type = futures[future]
            try:
                future.result()
                print(f"\n🏁 [{crawler_type}] CONFIG COMPLETED\n")
            except Exception as e:
                print(f"\n❌ [{crawler_type}] CONFIG FAILED: {e}\n")

    print("="*80)
    print("ALL CONFIGS COMPLETED")
    print("="*80 + "\n")


def run_specific_config(
    config_type: str,
    stats: ExecutionStats,
    chunks: Optional[list[int]] = None,
    chunks_per_config: Optional[int] = None
) -> None:
    """
    Run a specific config only

    Args:
        config_type: Crawler type to run
        stats: Statistics tracker
        chunks: List of chunk numbers to process
        chunks_per_config: Override for parallel chunks
    """
    config = None
    for c in CRAWLER_CONFIGS:
        if c.crawler_type == config_type:
            config = c
            break

    if config is None:
        print(f"❌ Config not found: {config_type}")
        print(f"   Available configs: {[c.crawler_type for c in CRAWLER_CONFIGS]}")
        sys.exit(1)

    if chunks is None:
        chunks = list(range(CHUNK_START, CHUNK_END + 1))

    if chunks_per_config is None:
        chunks_per_config = CHUNKS_PER_CONFIG

    print("\n" + "="*80)
    print(f"RUNNING SINGLE CONFIG: {config_type}")
    print("="*80)
    print(f"Chunks: {chunks}")
    print(f"Parallel: {min(chunks_per_config, len(chunks))}")
    print("="*80 + "\n")

    run_chunks_for_single_config(config, chunks, chunks_per_config, stats)


def run_sync_chunk(
    stats: ExecutionStats,
    chunk_number: int = 1
) -> None:
    """
    Run a specific chunk across ALL configs simultaneously
    Useful for synchronized/common samples that need to run together

    Args:
        stats: Statistics tracker
        chunk_number: Chunk number to run across all configs (default: 1)
    """
    print("\n" + "="*80)
    print(f"SYNCHRONIZED MODE: CHUNK {chunk_number} ACROSS ALL CONFIGS")
    print("="*80)
    print(f"Running chunk {chunk_number} for ALL {len(CRAWLER_CONFIGS)} configs simultaneously")
    print("="*80 + "\n")

    print("📋 Configs to run:")
    for config in CRAWLER_CONFIGS:
        print(f"  - {config.crawler_type} (chunk {chunk_number})")
    print()

    with ThreadPoolExecutor(max_workers=len(CRAWLER_CONFIGS)) as executor:
        futures = {
            executor.submit(
                run_single_chunk,
                config,
                chunk_number
            ): config.crawler_type
            for config in CRAWLER_CONFIGS
        }

        for future in as_completed(futures):
            crawler_type = futures[future]
            try:
                success, _, _ = future.result()
                if success:
                    stats.increment_success(crawler_type)
                else:
                    stats.increment_failed(crawler_type)
            except Exception as e:
                print(f"❌ [{crawler_type}] Chunk {chunk_number} unexpected error: {e}")
                stats.increment_failed(crawler_type)

    print(f"\n{'='*80}")
    print(f"SYNC COMPLETE: Chunk {chunk_number} finished for all configs")
    print(f"Success: {stats.success}/{len(CRAWLER_CONFIGS)}")
    print(f"{'='*80}\n")


def run_sync_then_unique(
    stats: ExecutionStats,
    sync_chunks: Optional[list[int]] = None,
    unique_chunks: Optional[list[int]] = None,
    chunks_per_config: Optional[int] = None
) -> None:
    """
    Run sync chunks first (all configs simultaneously for each chunk),
    then run unique chunks in parallel per config

    Args:
        stats: Statistics tracker
        sync_chunks: Chunks to run in sync mode (default: [1])
        unique_chunks: Chunks to run in parallel mode (default: [2-10])
        chunks_per_config: Parallel chunks per config for unique phase
    """
    if sync_chunks is None:
        sync_chunks = [1]

    if unique_chunks is None:
        unique_chunks = list(range(2, CHUNK_END + 1))

    if chunks_per_config is None:
        chunks_per_config = CHUNKS_PER_CONFIG

    total_sync = len(sync_chunks) * len(CRAWLER_CONFIGS)
    total_unique = len(unique_chunks) * len(CRAWLER_CONFIGS)
    total_all = total_sync + total_unique

    print("\n" + "="*80)
    print("SYNC-THEN-UNIQUE MODE")
    print("="*80)
    print(f"Phase 1 (SYNC):   {len(sync_chunks)} chunk(s) × {len(CRAWLER_CONFIGS)} configs = {total_sync} tasks")
    print(f"  Sync chunks:    {sync_chunks}")
    print(f"Phase 2 (UNIQUE): {len(unique_chunks)} chunk(s) × {len(CRAWLER_CONFIGS)} configs = {total_unique} tasks")
    print(f"  Unique chunks:  {unique_chunks}")
    print(f"  Parallel/config: {chunks_per_config}")
    print(f"Total tasks:      {total_all}")
    print("="*80 + "\n")

    # Phase 1: Run sync chunks
    for idx, chunk_num in enumerate(sync_chunks, 1):
        print(f"\n{'─'*40}")
        print(f"PHASE 1 - SYNC CHUNK {idx}/{len(sync_chunks)}: Chunk {chunk_num}")
        print(f"{'─'*40}")
        run_sync_chunk(stats, chunk_num)

    # Phase 2: Run unique chunks
    if unique_chunks:
        print(f"\n{'─'*40}")
        print(f"PHASE 2 - UNIQUE CHUNKS: {unique_chunks}")
        print(f"{'─'*40}")
        run_all_configs_parallel(stats, unique_chunks, chunks_per_config)

    print("="*80)
    print("SYNC-THEN-UNIQUE COMPLETE")
    print("="*80 + "\n")


def print_usage():
    """Print usage information"""
    print(f"""
{'='*80}
USAGE
{'='*80}

python run_youi.py [mode] [options]

MODES:
  all                    Run all configs with all chunks (default)
  config <name>          Run specific config only
  sync [chunk]           Run specific chunk across ALL configs simultaneously
                         (default: chunk 1)
  full                   Run sync chunk(s) first, then unique chunks
                         (Phase 1: sync, Phase 2: parallel per config)
  list                   List available configs

OPTIONS:
  --chunks, -c <spec>    Specify chunks to run (default: {CHUNK_START}-{CHUNK_END})
  --parallel, -p <num>   Override parallel chunks per config (default: {CHUNKS_PER_CONFIG})
  --sync-chunks <spec>   Specify sync chunks for 'full' mode (default: 1)
  --unique-chunks <spec> Specify unique chunks for 'full' mode (default: 2-{CHUNK_END})
  --help, -h             Show this help message

CHUNK SPECIFICATION FORMATS:
  1-10                   Range: chunks 1 through 10
  1,3,5,7                List: specific chunks
  2-5,8,10               Mixed: range and individual chunks
  5                      Single: just chunk 5

EXAMPLES:

  Basic Usage:
    python run_youi.py all
        Run all configs, all chunks ({CHUNK_START}-{CHUNK_END}), {CHUNKS_PER_CONFIG} parallel per config

    python run_youi.py all --chunks 1-5
        Run all configs, chunks 1-5 only

    python run_youi.py all --chunks 1,3,5 --parallel 2
        Run all configs, chunks 1,3,5, 2 parallel per config

  Single Config:
    python run_youi.py config contents_toggle
        Run contents_toggle config only, all chunks

    python run_youi.py config combined --chunks 2-5 --parallel 1
        Run combined config, chunks 2-5, 1 at a time

  Sync Mode (same chunk across all configs simultaneously):
    python run_youi.py sync
        Run chunk 1 across all configs simultaneously

    python run_youi.py sync 5
        Run chunk 5 across all configs simultaneously

  Full Mode (sync first, then unique):
    python run_youi.py full
        Phase 1: Run chunk 1 (sync) across all configs
        Phase 2: Run chunks 2-10 (parallel per config)

    python run_youi.py full --sync-chunks 1,2 --unique-chunks 3-10
        Phase 1: Run chunks 1,2 (sync) across all configs
        Phase 2: Run chunks 3-10 (parallel per config)

    python run_youi.py full --sync-chunks 1 --unique-chunks 2-5 --parallel 2
        Phase 1: Run chunk 1 (sync)
        Phase 2: Run chunks 2-5 with 2 parallel per config

  Other:
    python run_youi.py list
        Show available config names

AVAILABLE CONFIGS:
""")
    for config in CRAWLER_CONFIGS:
        print(f"  - {config.crawler_type}")

    print(f"""
CONFIGURATION (edit in script):
  CHUNKS_PER_CONFIG = {CHUNKS_PER_CONFIG}  (parallel chunks per config)
  CHUNK_START = {CHUNK_START}
  CHUNK_END = {CHUNK_END}
  CHUNK_TIMEOUT_SECONDS = {CHUNK_TIMEOUT_SECONDS} ({CHUNK_TIMEOUT_SECONDS // 60} minutes)

{'='*80}
""")


def parse_arguments() -> dict:
    """
    Parse command line arguments

    Returns:
        Dictionary with parsed arguments
    """
    args = {
        "mode": "all",
        "config_name": None,
        "chunks": None,
        "parallel": None,
        "sync_chunk": 1,
        "sync_chunks": None,
        "unique_chunks": None
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg in ["--help", "-h"]:
            print_usage()
            sys.exit(0)

        elif arg in ["--chunks", "-c"]:
            if i + 1 < len(sys.argv):
                args["chunks"] = parse_chunks(sys.argv[i + 1])
                i += 1
            else:
                print("❌ --chunks requires a value")
                sys.exit(1)

        elif arg in ["--parallel", "-p"]:
            if i + 1 < len(sys.argv):
                args["parallel"] = int(sys.argv[i + 1])
                i += 1
            else:
                print("❌ --parallel requires a value")
                sys.exit(1)

        elif arg == "--sync-chunks":
            if i + 1 < len(sys.argv):
                args["sync_chunks"] = parse_chunks(sys.argv[i + 1])
                i += 1
            else:
                print("❌ --sync-chunks requires a value")
                sys.exit(1)

        elif arg == "--unique-chunks":
            if i + 1 < len(sys.argv):
                args["unique_chunks"] = parse_chunks(sys.argv[i + 1])
                i += 1
            else:
                print("❌ --unique-chunks requires a value")
                sys.exit(1)

        elif arg == "all":
            args["mode"] = "all"

        elif arg == "sync":
            args["mode"] = "sync"
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
                try:
                    args["sync_chunk"] = int(sys.argv[i + 1])
                    i += 1
                except ValueError:
                    pass

        elif arg == "full":
            args["mode"] = "full"

        elif arg == "config":
            args["mode"] = "config"
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
                args["config_name"] = sys.argv[i + 1]
                i += 1
            else:
                print("❌ config mode requires a config name")
                print(f"   Available: {[c.crawler_type for c in CRAWLER_CONFIGS]}")
                sys.exit(1)

        elif arg == "list":
            args["mode"] = "list"

        else:
            config_names = [c.crawler_type for c in CRAWLER_CONFIGS]
            if arg in config_names:
                args["mode"] = "config"
                args["config_name"] = arg
            else:
                print(f"❌ Unknown argument: {arg}")
                print("   Use --help for usage information")
                sys.exit(1)

        i += 1

    return args


# ===== MAIN EXECUTION =====
def main():
    """Main execution function"""
    try:
        args = parse_arguments()

        # Handle list mode
        if args["mode"] == "list":
            print("\n📋 Available Configs:")
            for config in CRAWLER_CONFIGS:
                print(f"  - {config.crawler_type}")
                print(f"      Filename: {config.filename}")
                print(f"      Log Dir:  {config.log_dir}")
            print()
            sys.exit(0)

        # Print header
        print_header()

        # Validate setup
        validate_runner_script()
        setup_log_directories()

        # Initialize statistics
        stats = ExecutionStats()

        # Get settings
        chunks = args["chunks"]
        parallel = args["parallel"]

        # Execute based on mode
        if args["mode"] == "all":
            chunk_display = chunks if chunks else f"{CHUNK_START}-{CHUNK_END}"
            parallel_display = parallel if parallel else CHUNKS_PER_CONFIG
            print(f"🚀 Mode: ALL CONFIGS")
            print(f"   Chunks: {chunk_display}")
            print(f"   Parallel per config: {parallel_display}\n")

            run_all_configs_parallel(stats, chunks, parallel)

        elif args["mode"] == "sync":
            sync_chunk = args.get("sync_chunk", 1)
            print(f"🚀 Mode: SYNCHRONIZED")
            print(f"   Chunk: {sync_chunk}")
            print(f"   Configs: {len(CRAWLER_CONFIGS)} (all running simultaneously)\n")

            run_sync_chunk(stats, sync_chunk)

        elif args["mode"] == "full":
            sync_chunks = args.get("sync_chunks") or [1]
            unique_chunks = args.get("unique_chunks") or list(range(2, CHUNK_END + 1))
            parallel_display = parallel if parallel else CHUNKS_PER_CONFIG

            print(f"🚀 Mode: FULL (SYNC-THEN-UNIQUE)")
            print(f"   Sync chunks: {sync_chunks}")
            print(f"   Unique chunks: {unique_chunks}")
            print(f"   Parallel per config: {parallel_display}\n")

            run_sync_then_unique(stats, sync_chunks, unique_chunks, parallel)

        elif args["mode"] == "config":
            chunk_display = chunks if chunks else f"{CHUNK_START}-{CHUNK_END}"
            parallel_display = parallel if parallel else CHUNKS_PER_CONFIG
            print(f"🚀 Mode: SINGLE CONFIG")
            print(f"   Config: {args['config_name']}")
            print(f"   Chunks: {chunk_display}")
            print(f"   Parallel: {parallel_display}\n")

            run_specific_config(args["config_name"], stats, chunks, parallel)

        # Finish and print summary
        stats.finish()
        print(stats.get_summary())

        # Print log locations
        print("📂 Log files saved in:")
        for config in CRAWLER_CONFIGS:
            print(f"  {config.crawler_type}: {config.log_dir}")
        print()

        # Exit with appropriate code
        if stats.failed > 0:
            print(f"⚠️  Completed with {stats.failed} failed chunks")
            sys.exit(1)
        else:
            print("✅ All chunks completed successfully!")
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user (Ctrl+C)")
        sys.exit(130)

    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
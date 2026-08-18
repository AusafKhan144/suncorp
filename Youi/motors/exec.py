#!/usr/bin/env python3
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

PYTHON = sys.executable  # uses current venv python

def run_one(chunk, input_dir, output_dir, log_dir, sleep):
    cmd = [
        PYTHON, "motors_worker.py",
        "--chunk", str(chunk),
        "--input-dir", input_dir,
        "--output-dir", output_dir,
        "--log-dir", log_dir,
        "--sleep", str(sleep),
    ]
    return subprocess.run(cmd, capture_output=True, text=True)

def main():
    input_dir  = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/inputs/0426/data/chunks/"
    output_dir = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/outputs/0426/"
    log_dir    = "/home/wmc/WMC/DataManipulation/Suncorp/Youi/Logs/0426/"
    sleep      = 2.0

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    chunks = ["1","2","3","4","5","6","7","8","9","10"]  # adjust names to match your files

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {
            ex.submit(run_one, c, input_dir, output_dir, log_dir, sleep): c
            for c in chunks
        }
        for fut in as_completed(futures):
            c = futures[fut]
            res = fut.result()
            if res.returncode != 0:
                # worker already wrote its own log; this is just launcher-level visibility
                print(f"[chunk {c}] FAILED rc={res.returncode}")
                if res.stderr:
                    print(res.stderr[-2000:])
            else:
                print(f"[chunk {c}] OK")

if __name__ == "__main__":
    main()
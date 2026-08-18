#!/usr/bin/env python3
import os
import time
import logging
import argparse
import pandas as pd
from motors_v2 import YouiMotorsNewFlow, format_values, sort_columns_with_excess


RETRYABLE_FAILURE_SUBSTRINGS = [
    "500 Server Error",
    "Internal Server Error",
    "502", "503", "504",
    "Read timed out",
    "Timeout",
    "ConnectionError",
    "Service Unavailable",
]


def is_retryable_failure(reason: str | None) -> bool:
    if not reason:
        return False
    r = str(reason).lower()
    return any(s.lower() in r for s in RETRYABLE_FAILURE_SUBSTRINGS)


class BatchProcessor:
    def __init__(self, input_csv_path: str, output_csv_path: str, id_col: str = "SAMPLE_NUMBER", logger=None):
        self.input_csv_path = input_csv_path
        self.output_csv_path = output_csv_path
        self.id_col = id_col
        self.logger = logger or logging.getLogger(__name__)
        self.processed_ids = set()
        self.current_headers = []
        self._load_state()

    def _load_state(self):
        if os.path.exists(self.output_csv_path) and os.path.getsize(self.output_csv_path) > 0:
            df = pd.read_csv(self.output_csv_path)
            self.current_headers = sort_columns_with_excess(list(df.columns))
            if self.id_col in df.columns:
                self.processed_ids = set(df[self.id_col].astype(str))
                self.logger.info(f"Resuming: {len(self.processed_ids)} already processed in this chunk output.")
        else:
            self.logger.info("No existing output for this chunk. Starting fresh.")

    def get_pending_records(self) -> list[dict]:
        df = pd.read_csv(self.input_csv_path)

        # The 202608 monthly extract ships the id column as "Sample_Number" while
        # every downstream consumer (crawler, column sort, monitor) expects
        # "SAMPLE_NUMBER". Unmatched, rec.get(id_col) is None for every row and the
        # chunk reports "Total Input: 140 | Pending: 0" and exits 0 having done
        # nothing. Canonicalise here, where the input is first read.
        df = df.rename(columns={
            c: self.id_col for c in df.columns
            if c.lower() == self.id_col.lower() and c != self.id_col
        })

        df["DATE_INCIDENT_MONTH"] = pd.to_numeric(df["DATE_INCIDENT_MONTH"], errors="coerce")
        df["DATE_INCIDENT_YEAR"] = pd.to_numeric(df["DATE_INCIDENT_YEAR"], errors="coerce")

        processed_ids = set(self.processed_ids)
        pending = []

        for rec in df.to_dict(orient="records"):
            rec_id = rec.get(self.id_col)
            if not rec_id:
                continue
            rec_id = str(rec_id)
            if rec_id in processed_ids:
                continue
            pending.append(rec)

        self.logger.info("Total Input: %d | Pending: %d", len(df), len(pending))
        return pending

    def save_record(self, record: dict):
        if not record:
            return

        new_df = pd.DataFrame([record])
        new_cols = set(new_df.columns)
        existing_cols = set(self.current_headers)

        schema_changed = (not self.current_headers) or bool(new_cols - existing_cols)

        if schema_changed:
            self.logger.info(f"Schema update needed. New cols: {new_cols - existing_cols}")

            if os.path.exists(self.output_csv_path) and os.path.getsize(self.output_csv_path) > 0:
                existing_df = pd.read_csv(self.output_csv_path)
                combined_df = pd.concat([existing_df, new_df], ignore_index=True)
                sorted_cols = sort_columns_with_excess(list(combined_df.columns))
                combined_df = combined_df[sorted_cols]
                combined_df.to_csv(self.output_csv_path, index=False)
                self.current_headers = sorted_cols
            else:
                sorted_cols = sort_columns_with_excess(list(new_df.columns))
                new_df = new_df[sorted_cols]
                new_df.to_csv(self.output_csv_path, index=False)
                self.current_headers = sorted_cols
        else:
            new_df = new_df.reindex(columns=self.current_headers)
            new_df.to_csv(self.output_csv_path, mode="a", header=False, index=False)

    def mark_processed_if_ok(self, result_data: dict) -> bool:
        rec_id = str(result_data.get(self.id_col, ""))
        if not rec_id:
            return False
        reason = result_data.get("Reason_for_quote_failure")
        if is_retryable_failure(reason):
            self.logger.warning(f"Retryable failure (not marking processed) id={rec_id} reason={reason}")
            return False
        self.processed_ids.add(rec_id)
        return True


def _run_one(input_row: dict, use_browser: bool, logger) -> dict:
    """Run one record, through a real browser page unless told otherwise.

    One browser per record: the quote, its cookies and its WAF standing all
    belong to that page, and reusing a page across records would hand Youi an
    obvious signal (and leave the previous quote's state behind).
    """
    values = format_values(input_row)
    if not use_browser:
        return YouiMotorsNewFlow(values).run_flow()

    import youi_browser

    with youi_browser.quote_session() as (session, init):
        return YouiMotorsNewFlow(values, browser=(session, init)).run_flow()


def _reject_unpriced_row(result_data: dict, logger) -> None:
    """Turn a $0 premium into a stated failure instead of a plausible-looking row.

    When Youi's WAF routes a session to the call centre the whole journey still
    answers 200, but every pricing call comes back with no data and the row lands
    with YearlyPayment=0.0 and an empty Reason_for_quote_failure — indistinguishable
    from a genuine quote in the delivered CSV. Anything non-positive is not a price.
    """
    if not isinstance(result_data, dict):
        return
    if (result_data.get("Reason_for_quote_failure") or "").strip():
        return
    try:
        premium = float(result_data.get("YearlyPayment") or 0)
    except (TypeError, ValueError):
        premium = 0.0
    if premium > 0:
        return
    reason = (
        "No premium returned (YearlyPayment=%s) — quote was not priced online; "
        "session likely routed to call centre" % result_data.get("YearlyPayment")
    )
    result_data["Reason_for_quote_failure"] = reason
    for field in ("YearlyPayment", "MonthlyPayment"):
        if field in result_data:
            result_data[field] = ""
    logger.error(f"Rejected unpriced row id={result_data.get('SAMPLE_NUMBER')}: {reason}")


def build_logger(log_path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger(f"youi.motors.chunk.{os.path.basename(log_path)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(sh)

    return logger


def main():
    parser = argparse.ArgumentParser(description="Youi Motors v2 per-chunk processor")
    parser.add_argument("--chunk", required=True, help="Chunk id (e.g. 01, 1, 10)")
    parser.add_argument("--input-dir", required=True, help="Directory containing chunked input CSVs")
    parser.add_argument("--output-dir", required=True, help="Directory for output CSV")
    parser.add_argument("--log-dir", required=True, help="Directory for log files")
    parser.add_argument("--period", default="202608", help="Period string for filename (default: 202608)")
    parser.add_argument("--basename", default=None,
                        help="Input/output stem, e.g. Motor_YOUI_monthly_202608 "
                             "(default: Motor_YOUI_quarterly_<period>)")
    parser.add_argument("--sleep", type=float, default=2.0, help="Seconds to sleep between records")
    parser.add_argument(
        "--no-browser", dest="browser", action="store_false", default=True,
        help="Use the legacy Zyte-render + requests transport. Youi's WAF routes "
             "those sessions to the call centre and never prices them, so this is "
             "for comparison only.",
    )
    args = parser.parse_args()

    chunk = str(args.chunk)
    period = args.period
    # The cadence used to be baked into the filename, so a monthly run read the
    # quarterly chunks and resumed the quarterly output. Keep the old stem as the
    # default so existing quarterly runs are unchanged.
    basename = args.basename or f"Motor_YOUI_quarterly_{period}"
    input_file = os.path.join(args.input_dir, f"{basename}_chunk_{chunk}.csv")
    output_file = os.path.join(args.output_dir, f"{basename}.csv")
    log_file = os.path.join(args.log_dir, f"chunk_{chunk}.log")

    logger = build_logger(log_file)
    logger.info(f"Starting chunk={chunk} period={period}")
    logger.info(f"Input:  {input_file}")
    logger.info(f"Output: {output_file}")
    logger.info(f"Log:    {log_file}")

    os.makedirs(args.output_dir, exist_ok=True)

    processor = BatchProcessor(input_file, output_file, id_col="SAMPLE_NUMBER", logger=logger)
    queue = processor.get_pending_records()

    for i, input_row in enumerate(queue, start=1):
        row_id = input_row.get("SAMPLE_NUMBER")
        logger.info(f"[{i}/{len(queue)}] Processing ID={row_id}")

        result_data = None
        try:
            result_data = _run_one(input_row, args.browser, logger)
            _reject_unpriced_row(result_data, logger)

            if processor.mark_processed_if_ok(result_data):
                processor.save_record(result_data)

            time.sleep(args.sleep)
        except KeyboardInterrupt:
            logger.warning("Interrupted by user; exiting.")
            break
        except Exception as e:
            logger.error(f"Critical error on ID={row_id}: {e}", exc_info=True)
            if isinstance(result_data, dict):
                try:
                    processor.save_record(result_data)
                except Exception:
                    pass

    logger.info("Chunk completed.")


if __name__ == "__main__":
    main()

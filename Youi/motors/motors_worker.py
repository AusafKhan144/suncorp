#!/usr/bin/env python3
import os
import re
import time
import logging
import argparse
import pandas as pd
from motors import YouiMotorsNewFlow

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


def format_values(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if key == "SAMPLE_NUMBER":
            result[key] = value
        elif isinstance(value, str):
            result[key] = value.title()
        elif isinstance(value, float) and value.is_integer():
            result[key] = str(int(value))
        elif isinstance(value, (int, float)):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def sort_columns_with_excess(columns: list[str]) -> list[str]:
    """
    Sort columns with excess values numerically.
    
    Groups:
    1. Fixed prefix columns (SAMPLE_NUMBER, Postcode, DOB_*, QuoteDate, etc.)
    2. Quote data columns (QuoteLink, QuoteNumber, DefaultExcess, payments, etc.)
    3. Excess columns sorted numerically (excess500, excess550, ..., excess10000)
    4. Suffix columns (Reason_for_quote_failure, Remark)
    """
    # Define column order groups
    prefix_cols = [
        "SAMPLE_NUMBER", "Postcode", "DOB_day", "DOB_month", "DOB_year",
        "QuoteDate", "Timestamp AEST (in hh:mm)"
    ]
    
    quote_data_cols = [
        "DeducedVehicleSelection", "QuoteLink", "QuoteNumber", "DefaultExcess",
        "MonthlyPayment", "YearlyPayment", "RoadsideAssist", "HireCar",
        "WindowGlassExcess", "BusinessItemsCover", "AgreedValue", "MiddleAgreedValueSI"
    ]
    
    suffix_cols = [
        "Reason_for_quote_failure", "Remark (if any)"
    ]
    
    # Extract excess columns and sort numerically
    excess_pattern = re.compile(r'^excess(\d+)$', re.IGNORECASE)
    excess_cols = []
    other_cols = []
    
    for col in columns:
        match = excess_pattern.match(col)
        if match:
            excess_cols.append((int(match.group(1)), col))
        elif col not in prefix_cols + quote_data_cols + suffix_cols:
            other_cols.append(col)
    
    # Sort excess columns by numeric value
    excess_cols.sort(key=lambda x: x[0])
    sorted_excess = [col for _, col in excess_cols]
    
    # Build final column order (only include columns that exist)
    final_order = []
    
    for col in prefix_cols:
        if col in columns:
            final_order.append(col)
    
    for col in quote_data_cols:
        if col in columns:
            final_order.append(col)
    
    # Add sorted excess columns
    final_order.extend(sorted_excess)
    
    # Add any other columns not in predefined groups
    for col in other_cols:
        if col not in final_order:
            final_order.append(col)
    
    for col in suffix_cols:
        if col in columns:
            final_order.append(col)
    
    return final_order


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
            # Sort columns properly when loading
            self.current_headers = sort_columns_with_excess(list(df.columns))
            if self.id_col in df.columns:
                self.processed_ids = set(df[self.id_col].astype(str))
                self.logger.info(f"Resuming: {len(self.processed_ids)} already processed in this chunk output.")
        else:
            self.logger.info("No existing output for this chunk. Starting fresh.")

    def get_pending_records(self) -> list[dict]:
        df = pd.read_csv(self.input_csv_path)

        # Ensure numeric columns are numeric
        df["DATE_INCIDENT_MONTH"] = pd.to_numeric(
            df["DATE_INCIDENT_MONTH"], errors="coerce"
        )
        df["DATE_INCIDENT_YEAR"] = pd.to_numeric(
            df["DATE_INCIDENT_YEAR"], errors="coerce"
        )

        processed_ids = set(self.processed_ids)
        pending = []

        for rec in df.to_dict(orient="records"):
            rec_id = rec.get(self.id_col)

            if not rec_id:
                continue

            rec_id = str(rec_id)

            # Skip already processed
            if rec_id in processed_ids:
                continue


            pending.append(rec)

        self.logger.info(
            "Total Input: %d | Pending: %d",
            len(df),
            len(pending),
        )

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
                
                # Sort columns with excess numerically
                sorted_cols = sort_columns_with_excess(list(combined_df.columns))
                combined_df = combined_df[sorted_cols]
                
                combined_df.to_csv(self.output_csv_path, index=False)
                self.current_headers = sorted_cols
            else:
                # Sort columns for new file
                sorted_cols = sort_columns_with_excess(list(new_df.columns))
                new_df = new_df[sorted_cols]
                
                new_df.to_csv(self.output_csv_path, index=False)
                self.current_headers = sorted_cols
        else:
            # Reindex to match current headers (already sorted)
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


def build_logger(log_path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger(f"youi.chunk.{os.path.basename(log_path)}")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk", required=True, help="Chunk id (e.g. 01, 1, 10)")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()

    chunk = str(args.chunk)
    input_file = os.path.join(args.input_dir, f"Motor_YOUI_quarterly_202605_chunk_{chunk}.csv")
    output_file = os.path.join(args.output_dir, f"Motor_YOUI_quarterly_202605.csv")
    log_file = os.path.join(args.log_dir, f"chunk_{chunk}.log")

    logger = build_logger(log_file)
    logger.info(f"Starting chunk={chunk}")
    logger.info(f"Input:  {input_file}")
    logger.info(f"Output: {output_file}")
    logger.info(f"Log:    {log_file}")

    processor = BatchProcessor(input_file, output_file, id_col="SAMPLE_NUMBER", logger=logger)
    queue = processor.get_pending_records()

    for i, input_row in enumerate(queue, start=1):
        row_id = input_row.get("SAMPLE_NUMBER")

        logger.info(f"[{i}/{len(queue)}] Processing ID={row_id}")

        result_data = None
        try:
            flow = YouiMotorsNewFlow(format_values(input_row))
            result_data = flow.run_flow()

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
import sys
from time import sleep
import random
import pandas as pd
import logging
import os
import asyncio
from crawler import YouiMotors

# ======= Configure logger =======
logger = logging.getLogger("youi_runner")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(ch)

MIN_DELAY = 5
MAX_DELAY = 10

# ======= Paths =======
chunk_arg = sys.argv[-1]
INPUT_FILE = f"/home/wmc/WMC/WMCDataHarvesting/youi/wmc_motor_202602/Motor_YOUI_quarterly_202602.csv"
OUTPUT_FILE = "/home/wmc/WMC/WMCDataHarvesting/youi/wmc_motor_202602/Output/Motor_YOUI_quarterly_202602.csv"

# ======= Load already processed =======
processed_ids = set()
if os.path.exists(OUTPUT_FILE):
    try:
        existing = pd.read_csv(OUTPUT_FILE, usecols=["SAMPLE_NUMBER"])
        processed_ids = set(existing["SAMPLE_NUMBER"].dropna().astype(str))
        logger.info(f"ℹ️ Loaded {len(processed_ids)} already processed quotes from {OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"⚠️ Could not load existing output file: {e}", exc_info=True)

# ======= Steps sequence =======
PAGE_STEPS = [
    ("populate_page_01", lambda crawler: crawler.populate_page_01()),
    ("populate_page_02", lambda crawler: crawler.populate_page_02()),
    ("populate_page_03", lambda crawler: crawler.populate_page_03()),
    ("populate_page_04", lambda crawler: crawler.populate_page_04()),
    ("populate_page_05", lambda crawler: crawler.populate_page_05()),
    ("populate_page_06", lambda crawler: crawler.populate_page_06()),
    ("populate_page_07", lambda crawler: crawler.populate_page_07()),
    ("populate_page_08", lambda crawler: crawler.populate_page_08()),
    ("switch_flow_customer_choice", lambda crawler: crawler.switch_flow_customer_choice()),  # conditional later
    ("small_business_usage", lambda crawler: crawler.small_business_usage()),  # conditional later
    ("populate_page_09", lambda crawler: crawler.populate_page_09()),
    ("populate_page_10", lambda crawler: crawler.populate_page_10()),
    ("populate_page_11", lambda crawler: crawler.populate_page_11()),
    ("populate_page_12", lambda crawler: crawler.populate_page_12()),
]

def process_record(input_data):
    try:
        youi_object = asyncio.run(YouiMotors(input_data=input_data,output_file=OUTPUT_FILE).async_init())
        json_data = youi_object.base_json()
        logger.info(f"➡️ Start flowInstanceId={youi_object.flow_instance_id}")

        # Run step sequence
        for step_name, step_func in PAGE_STEPS:
            # Skip cyclone step unless needed
            if step_name == "small_business_usage" and json_data.get("currentStep") != "nswSmallBusiness":
                continue

            if step_name == "switch_flow_customer_choice" and json_data.get("currentStep") != "switchFlowCustomerChoice":
                continue

            try:
                json_data = step_func(youi_object)
                if youi_object.search_for_quote_failure():
                    logger.warning(f"❌ Quote failure after {step_name}, skipping record.")
                    return False, None
                logger.debug(f"✅ Step done: {step_name} -> currentStep={json_data.get('currentStep')}")
            except Exception as step_err:
                logger.error(f"⚠️ Exception in {step_name}: {step_err}", exc_info=True)
                return False, None
            
            sleep(random.uniform(MIN_DELAY,MAX_DELAY))

        # Collect premium and write to CSV
        try:
            youi_object.collect_premium()
            quote_num = str(youi_object.output_data_dict.get("QuoteNumber"))
            logger.info(f"💰 Collected premium for QuoteNumber={quote_num}")
            return True, quote_num
        except Exception as premium_err:
            logger.error(f"⚠️ Failed to collect premium: {premium_err}", exc_info=True)
            return False, None

    except Exception as e:
        logger.error(f"⚠️ Fatal error on record: {e}", exc_info=True)
        return False, None
        return False, None

# ======= Main =======
if __name__ == "__main__":
    df = pd.read_csv(INPUT_FILE)
    df = df[(df['DATE_INCIDENT_MONTH'] == 2) & (df['DATE_INCIDENT_YEAR'] == 2026)]
    input_data_list = df.to_dict(orient="records")

    success_count = 0
    fail_count = 0

    for idx, input_data in enumerate(input_data_list, start=1):
        sample_num = str(input_data.get("SAMPLE_NUMBER"))
        
        logger.info(f"🚀 Processing record {idx}/{len(input_data_list)} (sample_number={sample_num})")

        if sample_num in processed_ids:
            continue

        # Run crawler
        ok, quote_num = process_record(input_data)
        if ok:
            success_count += 1
            if quote_num:
                processed_ids.add(sample_num)
        else:
            fail_count += 1

        sleep(random.uniform(5,15))

    logger.info(f"✅ Run complete: {success_count} succeeded, {fail_count} failed.")

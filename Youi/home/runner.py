import asyncio
import logging
import os
import random
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Hashable, List, Optional, Set, Tuple

import pandas as pd
import requests
import urllib3

from crawlers import (
    YouiBuildingCrawler,
    YouiCombinedCrawler,
    YouiContentsCrawler,
)

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
logging.basicConfig(
    level=logging.DEBUG,  # Increased verbosity to DEBUG
    format="%(asctime)s | %(levelname)-8s | [%(funcName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("youi_runner.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "1"))
BASE_DELAY = float(os.getenv("BASE_DELAY", "5.0"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "1"))

logger.info("=" * 80)
logger.info("YOUI CRAWLER RUNNER - INITIALIZATION")
logger.info("=" * 80)
logger.info(f"Configuration loaded:")
logger.info(f"  MAX_RETRIES: {MAX_RETRIES}")
logger.info(f"  BASE_DELAY: {BASE_DELAY}s")
logger.info(f"  MAX_CONCURRENT: {MAX_CONCURRENT}")
logger.info("=" * 80)

# Shared step definitions (reusable across crawler types)
SHARED_STEPS = {
    "populate_page_01": lambda c: c.populate_page_01(),
    "populate_page_02": lambda c: c.populate_page_02(),
    "populate_page_03": lambda c: c.populate_page_03(),
    "populate_page_04": lambda c: c.populate_page_04(),
    "switch_flow_customer_choice": lambda c: c.switch_flow_customer_choice(),
    "populate_cyclone_details": lambda c: c.populate_cyclone_details(),
    "populate_page_05": lambda c: c.populate_page_05(),
    "populate_page_06": lambda c: c.populate_page_06(),
}

# Crawler configuration: type -> (class, toggle, collect_method, steps)
CRAWLER_CONFIG: Dict[str, Tuple[type, bool, str, List[Tuple[str, Callable]]]] = {
    "contents": (
        YouiContentsCrawler,
        False,
        "collect_premium",
        [
            ("populate_page_01", SHARED_STEPS["populate_page_01"]),
            ("populate_page_02", SHARED_STEPS["populate_page_02"]),
            ("populate_page_03", SHARED_STEPS["populate_page_03"]),
            ("populate_page_04", SHARED_STEPS["populate_page_04"]),
            ("switch_flow_customer_choice", SHARED_STEPS["switch_flow_customer_choice"]),
            ("populate_cyclone_details", SHARED_STEPS["populate_cyclone_details"]),
            ("populate_page_05", SHARED_STEPS["populate_page_05"]),
            ("populate_page_06", SHARED_STEPS["populate_page_06"]),
        ],
    ),
    "contents_toggle": (
        YouiContentsCrawler,
        True,
        "collect_premium",
        [
            ("populate_page_01", SHARED_STEPS["populate_page_01"]),
            ("populate_page_02", SHARED_STEPS["populate_page_02"]),
            ("populate_page_03", SHARED_STEPS["populate_page_03"]),
            ("populate_page_04", SHARED_STEPS["populate_page_04"]),
            ("switch_flow_customer_choice", SHARED_STEPS["switch_flow_customer_choice"]),
            ("populate_cyclone_details", SHARED_STEPS["populate_cyclone_details"]),
            ("populate_page_05", SHARED_STEPS["populate_page_05"]),
            ("populate_page_06", SHARED_STEPS["populate_page_06"]),
        ],
    ),
    "combined": (
        YouiCombinedCrawler,
        False,
        "collect_premium",
        [
            ("populate_page_01", SHARED_STEPS["populate_page_01"]),
            ("populate_page_02", SHARED_STEPS["populate_page_02"]),
            ("populate_page_03", SHARED_STEPS["populate_page_03"]),
            ("populate_page_04", SHARED_STEPS["populate_page_04"]),
            ("switch_flow_customer_choice", SHARED_STEPS["switch_flow_customer_choice"]),
            ("populate_cyclone_details", SHARED_STEPS["populate_cyclone_details"]),
            ("populate_page_05", SHARED_STEPS["populate_page_05"]),
            ("populate_page_06", SHARED_STEPS["populate_page_06"]),
        ],
    ),
    "buildings": (
        YouiBuildingCrawler,
        False,
        "collect_premium",
        [
            ("populate_page_01", SHARED_STEPS["populate_page_01"]),
            ("populate_page_02", SHARED_STEPS["populate_page_02"]),
            ("populate_page_03", SHARED_STEPS["populate_page_03"]),
            ("populate_page_04", SHARED_STEPS["populate_page_04"]),
            ("switch_flow_customer_choice", SHARED_STEPS["switch_flow_customer_choice"]),
            ("populate_cyclone_details", SHARED_STEPS["populate_cyclone_details"]),
            ("populate_page_06", SHARED_STEPS["populate_page_06"]),
        ],
    ),
    "buildings_toggle": (
        YouiBuildingCrawler,
        True,
        "collect_premium",
        [
            ("populate_page_01", SHARED_STEPS["populate_page_01"]),
            ("populate_page_02", SHARED_STEPS["populate_page_02"]),
            ("populate_page_03", SHARED_STEPS["populate_page_03"]),
            ("populate_page_04", SHARED_STEPS["populate_page_04"]),
            ("switch_flow_customer_choice", SHARED_STEPS["switch_flow_customer_choice"]),
            ("populate_cyclone_details", SHARED_STEPS["populate_cyclone_details"]),
            ("populate_page_06", SHARED_STEPS["populate_page_06"]),
        ],
    ),
}

# Steps that should be skipped based on current step conditions
CONDITIONAL_STEPS = {
    "populate_cyclone_details": "cycloneMitigation",
    "switch_flow_customer_choice": "switchFlowCustomerChoice",
}

# Steps that indicate quote failure
FAILURE_STEPS = {"smsSecurityPin"}


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def format_values(data: dict) -> dict:
    """Format input data values for crawler processing.
    
    Args:
         input data dictionary
        
    Returns:
        Formatted data dictionary with proper type conversions
    """
    try:
        logger.debug(f"Formatting values for {len(data)} fields")
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
        
        logger.debug(f"Successfully formatted {len(result)} fields")
        return result
        
    except Exception as e:
        logger.error(f"Error formatting values: {e}")
        logger.debug(f"Problematic data: {data}")
        raise


def load_processed_ids(output_file: Path, id_column: str = "sample_number") -> Set[str]:
    """Load already processed sample IDs from output file.
    
    Args:
        output_file: Path to the output CSV file
        id_column: Column name containing sample IDs
        
    Returns:
        Set of processed sample IDs
    """
    logger.debug(f"Attempting to load processed IDs from: {output_file}")
    
    if not output_file.exists():
        logger.info(f"No existing output file found at {output_file}")
        logger.debug("Starting fresh with no processed records")
        return set()
    
    try:
        logger.debug(f"Reading CSV file: {output_file}")
        df = pd.read_csv(output_file, usecols=[id_column])
        
        logger.debug(f"Loaded DataFrame with {len(df)} rows")
        processed = set(df[id_column].dropna().astype(str))
        
        logger.info(f"Loaded {len(processed)} processed records from {output_file.name}")
        logger.debug(f"Sample of processed IDs: {list(processed)[:5]}")
        
        return processed
        
    except FileNotFoundError:
        logger.warning(f"Output file not found: {output_file}")
        return set()
    except KeyError as e:
        logger.error(f"Column '{id_column}' not found in output file: {e}")
        logger.debug(f"Available columns might be missing or misnamed")
        return set()
    except pd.errors.EmptyDataError:
        logger.warning(f"Output file is empty: {output_file}")
        return set()
    except Exception as e:
        logger.error(f"Unexpected error loading processed IDs: {e}")
        logger.debug(traceback.format_exc())
        return set()


def load_input_data(input_file: Path) -> List[Dict[Hashable, Any]]:
    """Load and validate input data from CSV.
    
    Args:
        input_file: Path to the input CSV file
        
    Returns:
        List of dictionaries containing input records
        
    Raises:
        FileNotFoundError: If input file doesn't exist
    """
    logger.debug(f"Loading input data from: {input_file}")
    
    if not input_file.exists():
        logger.error(f"Input file not found: {input_file}")
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    try:
        logger.debug(f"Reading CSV file: {input_file}")
        df = pd.read_csv(input_file).fillna("NA")

        # The monthly extract ships the id column as "Sample_Number" while the
        # crawlers read input_data["SAMPLE_NUMBER"]. Unmatched, every output row
        # gets a blank sample_number and load_processed_ids resumes from 0.
        # Canonicalise here, where the input is first read. Same fix as
        # motors/motors_v2_worker.py:get_pending_records.
        df = df.rename(columns={
            c: "SAMPLE_NUMBER" for c in df.columns
            if c.lower() == "sample_number" and c != "SAMPLE_NUMBER"
        })

        logger.info(f"Loaded {len(df)} records from {input_file.name}")
        logger.debug(f"DataFrame shape: {df.shape}")
        logger.debug(f"Columns: {list(df.columns)}")
        
        records = df.to_dict(orient="records")
        logger.debug(f"Converted to {len(records)} record dictionaries")
        
        return records
        
    except FileNotFoundError:
        logger.error(f"Input file not found: {input_file}")
        raise
    except pd.errors.EmptyDataError:
        logger.error(f"Input file is empty: {input_file}")
        raise ValueError(f"Input file is empty: {input_file}")
    except pd.errors.ParserError as e:
        logger.error(f"Error parsing CSV file: {e}")
        logger.debug(traceback.format_exc())
        raise
    except Exception as e:
        logger.error(f"Unexpected error loading input file: {e}")
        logger.debug(traceback.format_exc())
        raise


# ============================================================================
# CORE PROCESSING LOGIC
# ============================================================================
async def execute_crawler_steps(
    crawler: Any,
    steps: List[Tuple[str, Callable]],
    json_data: Dict[str, Any],
    sample_number: str,
) -> bool:
    """Execute crawler steps with conditional logic and error handling.
    
    Args:
        crawler: Initialized crawler instance
        steps: List of (step_name, step_function) tuples
        json_data: Initial JSON data from crawler
        sample_number: Sample number for logging
        
    Returns:
        True if all steps completed successfully, False otherwise
    """
    logger.debug(f"[{sample_number}] Starting execution of {len(steps)} steps")
    
    for idx, (step_name, step_func) in enumerate(steps, 1):
        current_step = json_data.get("currentStep")
        
        logger.debug(f"[{sample_number}] Step {idx}/{len(steps)}: {step_name} (current: {current_step})")
        
        # Check if step should be skipped based on conditions
        if step_name in CONDITIONAL_STEPS:
            required_step = CONDITIONAL_STEPS[step_name]
            if current_step != required_step:
                logger.info(
                    f"[{sample_number}] Skipping {step_name} "
                    f"(current: {current_step}, required: {required_step})"
                )
                continue
        
        # Execute step with comprehensive error handling
        try:
            logger.debug(f"[{sample_number}] Executing step function: {step_name}")
            json_data = step_func(crawler)
            next_step = json_data.get("currentStep")
            
            logger.debug(f"[{sample_number}] Step returned, next_step: {next_step}")
            
            # Check for quote failure
            if crawler.search_for_quote_failure():
                logger.warning(
                    f"[{sample_number}] Quote failure detected after {step_name}"
                )
                logger.debug(f"[{sample_number}] Quote failure check triggered")
                return False
            
            # Check for failure steps
            if next_step in FAILURE_STEPS:
                logger.warning(
                    f"[{sample_number}] Failure step '{next_step}' encountered after {step_name}"
                )
                logger.debug(f"[{sample_number}] Matched failure step: {next_step}")
                return False
            
            logger.info(
                f"[{sample_number}] ✓ Step {idx}/{len(steps)} '{step_name}' completed: "
                f"{current_step} → {next_step}"
            )
            
        except requests.exceptions.ProxyError as e:
            logger.error(
                f"[{sample_number}] Proxy error in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Proxy error details:\n{traceback.format_exc()}")
            
            # Extract more details from the proxy error
            if hasattr(e, 'args') and len(e.args) > 0:
                error_msg = str(e.args[0])
                if "Tunnel connection failed" in error_msg:
                    logger.error(f"[{sample_number}] Proxy tunnel connection failed - proxy server issue")
                elif "Unable to connect to proxy" in error_msg:
                    logger.error(f"[{sample_number}] Cannot reach proxy server - check proxy configuration")
            
            return False
            
        except requests.exceptions.ConnectionError as e:
            logger.error(
                f"[{sample_number}] Connection error in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Connection error details:\n{traceback.format_exc()}")
            
            # Check if it's a DNS resolution issue
            if "Name or service not known" in str(e) or "getaddrinfo failed" in str(e):
                logger.error(f"[{sample_number}] DNS resolution failed - check network connectivity")
            
            return False
            
        except requests.exceptions.Timeout as e:
            logger.error(
                f"[{sample_number}] Request timeout in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Timeout details:\n{traceback.format_exc()}")
            return False
            
        except requests.exceptions.HTTPError as e:
            logger.error(
                f"[{sample_number}] HTTP error in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] HTTP error details:\n{traceback.format_exc()}")
            
            # Log response details if available
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"[{sample_number}] HTTP Status: {e.response.status_code}")
                logger.debug(f"[{sample_number}] Response body: {e.response.text[:500]}")
            
            return False
            
        except requests.exceptions.RequestException as e:
            logger.error(
                f"[{sample_number}] Request exception in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Request exception details:\n{traceback.format_exc()}")
            return False
            
        except urllib3.exceptions.ProxyError as e:
            logger.error(
                f"[{sample_number}] urllib3 proxy error in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] urllib3 proxy error details:\n{traceback.format_exc()}")
            return False
            
        except urllib3.exceptions.MaxRetryError as e:
            logger.error(
                f"[{sample_number}] Max retries exceeded in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Max retry error details:\n{traceback.format_exc()}")
            
            # Check if it's due to proxy
            if "ProxyError" in str(e):
                logger.error(f"[{sample_number}] Proxy is causing max retry failures")
            
            return False
            
        except OSError as e:
            logger.error(
                f"[{sample_number}] OS error in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] OS error details:\n{traceback.format_exc()}")
            
            # Check for specific OSError types
            if "Tunnel connection failed" in str(e):
                logger.error(f"[{sample_number}] Proxy tunnel failed with HTTP 500 - proxy server error")
            elif "Network is unreachable" in str(e):
                logger.error(f"[{sample_number}] Network unreachable - check network connectivity")
            
            return False
            
        except AttributeError as e:
            logger.error(
                f"[{sample_number}] Step '{step_name}' not found in crawler: {e}"
            )
            logger.debug(f"[{sample_number}] AttributeError details:\n{traceback.format_exc()}")
            return False
            
        except TimeoutError as e:
            logger.error(
                f"[{sample_number}] Timeout in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Timeout details:\n{traceback.format_exc()}")
            return False
            
        except ConnectionError as e:
            logger.error(
                f"[{sample_number}] Connection error in step '{step_name}': {e}"
            )
            logger.debug(f"[{sample_number}] Connection error details:\n{traceback.format_exc()}")
            return False
            
        except Exception as e:
            logger.error(
                f"[{sample_number}] Unexpected error in step '{step_name}': {type(e).__name__}: {e}"
            )
            logger.debug(f"[{sample_number}] Full traceback:\n{traceback.format_exc()}")
            return False
    
    logger.info(f"[{sample_number}] All {len(steps)} steps completed successfully")
    return True


async def process_record(
    input_data: Dict[str, Any],
    crawler_class: type,
    toggle: bool,
    collect_method: str,
    steps: List[Tuple[str, Callable]],
    output_file: Path,
    sem: asyncio.Semaphore,
) -> Tuple[bool, Optional[str]]:
    """Process a single record with retries and exponential backoff.
    
    Args:
        input_data: Input record data
        crawler_class: Crawler class to instantiate
        toggle: Toggle setting for crawler
        collect_method: Method name to collect premium
        steps: List of steps to execute
        output_file: Output file path
        sem: Semaphore for concurrency control
        
    Returns:
        Tuple of (success: bool, sample_number: str | None)
    """
    sample_number = str(input_data.get("SAMPLE_NUMBER", "unknown"))
    
    logger.debug(f"[{sample_number}] Entering process_record")
    logger.debug(f"[{sample_number}] Crawler class: {crawler_class.__name__}")
    logger.debug(f"[{sample_number}] Toggle: {toggle}")
    logger.debug(f"[{sample_number}] Collect method: {collect_method}")
    
    async with sem:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    f"[{sample_number}] Starting attempt {attempt}/{MAX_RETRIES}"
                )
                
                # Format and initialize crawler
                logger.debug(f"[{sample_number}] Formatting input data")
                formatted_data = format_values(input_data)
                
                logger.debug(f"[{sample_number}] Initializing crawler")
                crawler = await crawler_class(
                    input_data=formatted_data,
                    output_file=str(output_file),
                    toggle=toggle,
                ).async_init()
                
                flow_id = crawler.flow_instance_id
                logger.info(
                    f"[{sample_number}] Crawler initialized (flow: {flow_id}, "
                    f"attempt: {attempt}/{MAX_RETRIES})"
                )
                
                # Execute steps
                logger.debug(f"[{sample_number}] Getting base JSON")
                json_data = crawler.base_json()
                
                logger.debug(f"[{sample_number}] Executing crawler steps")
                if not await execute_crawler_steps(crawler, steps, json_data, sample_number):
                    logger.warning(f"[{sample_number}] Steps execution failed")
                    logger.debug(f"[{sample_number}] Will retry if attempts remaining")
                    
                    if attempt < MAX_RETRIES:
                        continue
                    return False, None
                
                # Collect premium
                logger.debug(f"[{sample_number}] Collecting premium using {collect_method}")
                try:
                    collect_func = getattr(crawler, collect_method)
                    collect_func()
                    
                    quote_number = str(crawler.output_data_dict.get("QuoteNumber", "N/A"))
                    logger.info(
                        f"[{sample_number}] ✓ SUCCESS (quote: {quote_number})"
                    )
                    logger.debug(f"[{sample_number}] Premium collection completed")
                    
                    return True, sample_number
                    
                except AttributeError as e:
                    logger.error(
                        f"[{sample_number}] Collect method '{collect_method}' not found: {e}"
                    )
                    logger.debug(f"[{sample_number}] AttributeError:\n{traceback.format_exc()}")
                    return False, None
                    
            except requests.exceptions.ProxyError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} proxy error: {e}"
                )
                logger.debug(f"[{sample_number}] Proxy error traceback:\n{traceback.format_exc()}")
                
            except requests.exceptions.ConnectionError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} connection error: {e}"
                )
                logger.debug(f"[{sample_number}] Connection error traceback:\n{traceback.format_exc()}")
                
            except requests.exceptions.Timeout as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} timeout: {e}"
                )
                logger.debug(f"[{sample_number}] Timeout traceback:\n{traceback.format_exc()}")
                
            except requests.exceptions.RequestException as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} request exception: {e}"
                )
                logger.debug(f"[{sample_number}] Request exception traceback:\n{traceback.format_exc()}")
                
            except urllib3.exceptions.ProxyError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} urllib3 proxy error: {e}"
                )
                logger.debug(f"[{sample_number}] urllib3 proxy error traceback:\n{traceback.format_exc()}")
                
            except OSError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} OS error: {e}"
                )
                logger.debug(f"[{sample_number}] OS error traceback:\n{traceback.format_exc()}")
                
            except asyncio.TimeoutError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} async timeout: {e}"
                )
                logger.debug(f"[{sample_number}] Async timeout traceback:\n{traceback.format_exc()}")
                
            except ValueError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} value error: {e}"
                )
                logger.debug(f"[{sample_number}] ValueError traceback:\n{traceback.format_exc()}")
                
            except KeyError as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} missing key: {e}"
                )
                logger.debug(f"[{sample_number}] KeyError traceback:\n{traceback.format_exc()}")
                
            except Exception as e:
                logger.error(
                    f"[{sample_number}] Attempt {attempt} unexpected error: "
                    f"{type(e).__name__}: {e}"
                )
                logger.debug(f"[{sample_number}] Full traceback:\n{traceback.format_exc()}")
            
            # Retry logic
            if attempt < MAX_RETRIES:
                backoff = BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.info(
                    f"[{sample_number}] Retrying in {backoff:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(
                    f"[{sample_number}] ✗ FAILED after {MAX_RETRIES} attempts"
                )
                return False, None
        
        return False, None


# ============================================================================
# MAIN EXECUTION
# ============================================================================
async def main(crawler_type: str, chunk_arg: str, filename: str) -> None:

    """Main execution function.
    
    Args:
        crawler_type: Type of crawler to run
        chunk_arg: Chunk identifier
        filename: Base filename for input/output
    """
    logger.info("=" * 80)
    logger.info(f"STARTING CRAWLER EXECUTION")
    logger.info(f"Crawler Type: {crawler_type}")
    logger.info(f"Chunk: {chunk_arg}")
    logger.info(f"Filename: {filename}")
    logger.info("=" * 80)
    
    # Validate crawler type
    if crawler_type not in CRAWLER_CONFIG:
        logger.error(
            f"Invalid crawler type '{crawler_type}'. "
            f"Available: {list(CRAWLER_CONFIG.keys())}"
        )
        sys.exit(1)
    
    try:
        crawler_class, toggle, collect_method, steps = CRAWLER_CONFIG[crawler_type]
        logger.debug(f"Crawler configuration loaded:")
        logger.debug(f"  Class: {crawler_class.__name__}")
        logger.debug(f"  Toggle: {toggle}")
        logger.debug(f"  Collect method: {collect_method}")
        logger.debug(f"  Steps: {len(steps)}")
        
    except Exception as e:
        logger.error(f"Error loading crawler configuration: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
    
    # Setup file paths
    try:
        base_dir = Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/inputs/20260817")
        output_dir = Path("/home/wmc/WMC/DataManipulation/Suncorp/Youi/outputs/20260817")
        input_file = base_dir / "data" / "chunks" / f"{filename}_chunk_{chunk_arg}.csv"
        output_file = output_dir / f"{filename}.csv"
        
        logger.info(f"File paths configured:")
        logger.info(f"  Base directory: {base_dir}")
        logger.info(f"  Input file: {input_file}")
        logger.info(f"  Output file: {output_file}")
        
        # Ensure output directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Output directory verified: {output_file.parent}")
        
    except Exception as e:
        logger.error(f"Error setting up file paths: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
    
    # Load data
    try:
        logger.info("Loading processed IDs and input data...")
        processed_ids = load_processed_ids(output_file)
        input_data_list = load_input_data(input_file)
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
    
    # Filter out already processed records
    try:
        logger.debug("Filtering already processed records...")
        records_to_process = [
            record for record in input_data_list
            if str(record.get("SAMPLE_NUMBER")) not in processed_ids
        ]
        
        skipped = len(input_data_list) - len(records_to_process)
        
        logger.info(f"Record filtering complete:")
        logger.info(f"  Total input records: {len(input_data_list)}")
        logger.info(f"  Already processed: {skipped}")
        logger.info(f"  To process: {len(records_to_process)}")
        
        if skipped > 0:
            logger.debug(f"Skipped {skipped} records that were already processed")
        
        if not records_to_process:
            logger.info("No new records to process - exiting")
            return
            
    except Exception as e:
        logger.error(f"Error filtering records: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
    
    # Process records concurrently
    try:
        logger.info("=" * 80)
        logger.info(f"PROCESSING {len(records_to_process)} RECORDS")
        logger.info(f"Concurrency limit: {MAX_CONCURRENT}")
        logger.info("=" * 80)
        
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        
        logger.debug("Creating async tasks...")
        tasks = [
            asyncio.create_task(
                process_record(
                    record, crawler_class, toggle, collect_method,
                    steps, output_file, sem
                )
            )
            for record in records_to_process
        ]
        
        logger.debug(f"Created {len(tasks)} async tasks")
        logger.info("Executing tasks...")
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.debug("All tasks completed, processing results...")
        
    except Exception as e:
        logger.error(f"Error during concurrent processing: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
    
    # Collect statistics
    try:
        success_count = 0
        fail_count = 0
        exception_count = 0
        proxy_errors = 0
        connection_errors = 0
        timeout_errors = 0
        
        for idx, result in enumerate(results, 1):
            if isinstance(result, Exception):
                exception_count += 1
                fail_count += 1
                
                # Categorize exceptions
                if isinstance(result, (requests.exceptions.ProxyError, urllib3.exceptions.ProxyError)):
                    proxy_errors += 1
                    logger.error(f"Task {idx} proxy error: {type(result).__name__}: {result}")
                elif isinstance(result, (requests.exceptions.ConnectionError, ConnectionError)):
                    connection_errors += 1
                    logger.error(f"Task {idx} connection error: {type(result).__name__}: {result}")
                elif isinstance(result, (requests.exceptions.Timeout, TimeoutError, asyncio.TimeoutError)):
                    timeout_errors += 1
                    logger.error(f"Task {idx} timeout: {type(result).__name__}: {result}")
                else:
                    logger.error(f"Task {idx} raised exception: {type(result).__name__}: {result}")
                
                logger.debug(f"Exception details:\n{traceback.format_exception(type(result), result, result.__traceback__)}")
            elif isinstance(result, tuple) and len(result) == 2:
                success, sample_num = result
                if success:
                    success_count += 1
                else:
                    fail_count += 1
            else:
                fail_count += 1
                logger.warning(f"Task {idx} returned unexpected result: {result}")
        
        logger.info("=" * 80)
        logger.info("EXECUTION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total records processed: {len(results)}")
        logger.info(f"Successful: {success_count}")
        logger.info(f"Failed: {fail_count}")
        logger.info(f"  - Exceptions: {exception_count}")
        logger.info(f"  - Proxy errors: {proxy_errors}")
        logger.info(f"  - Connection errors: {connection_errors}")
        logger.info(f"  - Timeout errors: {timeout_errors}")
        
        if len(results) > 0:
            logger.info(f"Success rate: {(success_count / len(results) * 100):.2f}%")
        
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"Error collecting statistics: {e}")
        logger.debug(traceback.format_exc())


if __name__ == "__main__":
    try:
        if len(sys.argv) < 4:
            print("Usage: python runner.py <crawler_type> <chunk_arg> <filename>")
            print(f"Available crawler types: {', '.join(CRAWLER_CONFIG.keys())}")
            sys.exit(1)
        
        crawler_type = sys.argv[1]
        chunk_arg = sys.argv[2]
        filename = sys.argv[3]
        
        logger.info(f"Script arguments: {sys.argv}")
        
        asyncio.run(main(crawler_type, chunk_arg, filename))
        
        logger.info("Script completed successfully")
        
    except KeyboardInterrupt:
        logger.warning("Script interrupted by user (Ctrl+C)")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {type(e).__name__}: {e}")
        logger.debug(traceback.format_exc())
        sys.exit(1)
# main.py
import time
import logging
import sys
import os

from config import (
    CHECK_INTERVAL_SECONDS, GCN_CIRCULARS_INDEX_URL,
    LOG_FILE, LOG_LEVEL, SKIP_CIRCULARS_BEFORE_ID
)
from gcn_utils import get_page_content, parse_gcn_circular_list, get_circular_text_robust
from llm_utils import extract_info_with_llm, get_default_extracted_data
from data_manager import load_processed_ids, save_processed_id, load_output_data, save_output_data
from slack_notifier import send_slack_notification


# --- ロギング設定 ---
log_handlers = [logging.StreamHandler(sys.stdout)]
if LOG_FILE:
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True) # exist_ok=Trueでディレクトリが既に存在してもエラーにしない
            logger_init = logging.getLogger(__name__) # Use a temporary logger for this message
            logger_init.info(f"Created log directory: {log_dir}")
        except OSError as e:
            # Use a temporary logger or print for this critical init error
            print(f"CRITICAL: Could not create log directory {log_dir}: {e}. File logging will be disabled.")
            LOG_FILE = None # Disable file logging if directory creation fails
    if LOG_FILE: # Re-check if LOG_FILE is still set
        log_handlers.append(logging.FileHandler(LOG_FILE, encoding='utf-8'))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)


def process_single_circular(circ_info, all_extracted_data_list_ref): # Pass list by ref for modification
    """単一のCircularを処理し、結果をall_extracted_data_list_refに追加する"""
    circular_id = circ_info['id']
    circular_url = circ_info['url']
    subject = circ_info.get('subject', f"Subject for {circular_id}")

    logger.info(f"Processing new circular: ID {circular_id}, URL: {circular_url}")

    raw_text = get_circular_text_robust(circular_id, circular_url)

    if not raw_text:
        logger.warning(f"Could not retrieve raw text for circular {circular_id}. Skipping LLM extraction.")
        error_entry = get_default_extracted_data(circular_id, circular_url, subject, "COULD NOT RETRIEVE TEXT")
        error_entry["extraction_successful"] = False
        error_entry["llm_error_message"] = "Failed to retrieve raw text from circular page or .gcn3 file."
        all_extracted_data_list_ref.append(error_entry)
        send_slack_notification(error_entry)
        return # No need to return error_entry, it's added to the list

    extracted_json = extract_info_with_llm(raw_text, circular_id, circular_url, subject)
    all_extracted_data_list_ref.append(extracted_json)

    if extracted_json["extraction_successful"]:
        logger.info(f"Successfully processed circular {circular_id}.")
    else:
        logger.warning(f"Failed to fully process circular {circular_id}. LLM Error: {extracted_json.get('llm_error_message')}")
    
    send_slack_notification(extracted_json)
    # No need to return extracted_json, it's added to the list

def main_loop():
    logger.info("Starting GCN Circular monitoring service...")
    
    skip_before_id_val = None
    if SKIP_CIRCULARS_BEFORE_ID is not None:
        try:
            skip_before_id_val = int(SKIP_CIRCULARS_BEFORE_ID)
            logger.info(f"Will skip circulars with ID less than {skip_before_id_val}.")
        except ValueError:
            logger.error(f"Invalid format for SKIP_CIRCULARS_BEFORE_ID: '{SKIP_CIRCULARS_BEFORE_ID}'. It should be an integer. Filtering by ID will be disabled.")
            skip_before_id_val = None

    processed_ids = load_processed_ids()
    all_extracted_data = load_output_data() # This is now a list
    
    for item in all_extracted_data:
        if 'circular_id' in item and item['circular_id'] is not None:
            processed_ids.add(str(item['circular_id']))
    logger.info(f"Loaded {len(processed_ids)} processed IDs and {len(all_extracted_data)} existing data entries.")

    while True:
        current_utc_time_str = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        logger.info(f"Checking for new GCN circulars... (Last check: {current_utc_time_str})")
        
        index_html = get_page_content(GCN_CIRCULARS_INDEX_URL)
        if not index_html:
            logger.error("Failed to fetch GCN circulars list. Retrying later.")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        circulars_on_page = parse_gcn_circular_list(index_html)
        if not circulars_on_page:
            logger.info("No circulars found on the main page or parsing failed.")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue
        
        new_circulars_processed_this_cycle = 0 # Renamed for clarity
        skipped_due_to_id_count = 0
        
        # Sort by ID ascending to process oldest new ones first
        # GCN list is usually newest first, so reversed() makes it oldest first.
        # If parse_gcn_circular_list already sorts them, this might be redundant or reversed.
        # Assuming parse_gcn_circular_list returns them as they appear on page (newest first).
        for circ_info in reversed(circulars_on_page): 
            circular_id_str = circ_info['id'] 
            
            try:
                current_circular_id_int = int(circular_id_str)
            except ValueError:
                logger.warning(f"Circular ID '{circular_id_str}' is not a valid integer. Skipping this entry.")
                # Optionally, save this malformed ID as processed to avoid re-evaluating
                if circular_id_str not in processed_ids:
                    save_processed_id(circular_id_str)
                    processed_ids.add(circular_id_str)
                continue

            if skip_before_id_val is not None:
                if current_circular_id_int < skip_before_id_val:
                    if circular_id_str not in processed_ids: 
                        logger.info(f"Skipping circular {circular_id_str} as its ID ({current_circular_id_int}) is less than {skip_before_id_val}.")
                        save_processed_id(circular_id_str)
                        processed_ids.add(circular_id_str)
                        skipped_due_to_id_count +=1
                    continue 

            if circular_id_str in processed_ids:
                continue
            
            new_circulars_processed_this_cycle += 1
            process_single_circular(circ_info, all_extracted_data) # Modifies all_extracted_data directly
            
            save_processed_id(circular_id_str) 
            processed_ids.add(circular_id_str)

            if new_circulars_processed_this_cycle > 0 : # Only sleep if we actually processed something
                 time.sleep(max(1, CHECK_INTERVAL_SECONDS // 1800)) # Sleep a little, e.g. 2s if interval is 1hr

        if skipped_due_to_id_count > 0:
            logger.info(f"Skipped {skipped_due_to_id_count} circular(s) due to ID filter in this cycle.")

        if new_circulars_processed_this_cycle > 0:
            logger.info(f"Processed {new_circulars_processed_this_cycle} new circular(s) in this cycle. Saving all data.")
            save_output_data(all_extracted_data) # Save the updated list
        else:
            if skipped_due_to_id_count == 0: # Only log "no new" if no ID skips happened either
                logger.info("No new circulars to process in this cycle.")

        logger.info(f"Next check in {CHECK_INTERVAL_SECONDS // 60} minutes ({CHECK_INTERVAL_SECONDS} seconds).")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("GCN Monitor stopped by user.")
    except Exception as e:
        logger.critical(f"Critical error in main loop: {e}", exc_info=True)
        sys.exit(1)
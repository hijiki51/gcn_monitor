# debug_circular_parser.py
import argparse
import json
import logging
import sys
import os
from urllib.parse import urljoin

from config import LOG_LEVEL, BASE_GCN_URL, SLACK_WEBHOOK_URL # SLACK_WEBHOOK_URLもインポート
from gcn_utils import get_circular_text_robust
from llm_utils import extract_info_with_llm, JSON_SCHEMA_DICT, get_default_extracted_data
from slack_notifier import send_slack_notification # Slack通知関数をインポート

# --- ロギング設定 ---
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def debug_parse_url(circular_url_or_id, send_to_slack=False): # send_to_slack引数を追加
    """指定されたURLまたはIDのGCN Circularをパースし、LLMで情報を抽出する"""
    
    circular_id_str = ""
    circular_url_str = ""

    if str(circular_url_or_id).isdigit():
        circular_id_str = str(circular_url_or_id)
        circular_url_str = urljoin(BASE_GCN_URL, f"/circulars/{circular_id_str}")
    elif str(circular_url_or_id).startswith("http"):
        circular_url_str = str(circular_url_or_id)
        try:
            path_parts = circular_url_str.split('/')
            potential_id = path_parts[-1]
            if potential_id.isdigit():
                circular_id_str = potential_id
            elif path_parts[-2].isdigit() and (path_parts[-1] == '' or path_parts[-1].startswith('#')):
                circular_id_str = path_parts[-2]
            else:
                id_from_url = potential_id.split('.')[0] 
                if id_from_url.isdigit():
                    circular_id_str = id_from_url
                else:
                    circular_id_str = "unknown_from_url"
        except Exception as e:
            logger.warning(f"Could not reliably determine circular ID from URL '{circular_url_str}': {e}")
            circular_id_str = "unknown_from_url"
    else:
        logger.error(f"Invalid input: {circular_url_or_id}. Please provide a full URL or a GCN circular ID.")
        return

    if not circular_id_str or circular_id_str == "unknown_from_url":
        logger.error(f"Could not determine a valid Circular ID for input: {circular_url_or_id}")
        return
        
    logger.info(f"Processing Circular ID: {circular_id_str}, URL: {circular_url_str}")

    raw_text = get_circular_text_robust(circular_id_str, circular_url_str)

    extracted_info = None # 初期化
    if not raw_text:
        logger.error(f"Could not retrieve raw text for circular {circular_id_str} from {circular_url_str}")
        extracted_info = get_default_extracted_data(circular_id_str, circular_url_str, "N/A (text retrieval failed)", None)
        extracted_info["llm_error_message"] = "Failed to retrieve raw text."
    else:
        logger.info(f"Raw text retrieved (first 300 chars):\n{raw_text[:300]}...")
        subject_debug = f"Debug parsing for circular {circular_id_str}"
        extracted_info = extract_info_with_llm(raw_text, circular_id_str, circular_url_str, subject_debug)
    
    print("\n--- Extracted Information (JSON) ---")
    print(json.dumps(extracted_info, indent=4, ensure_ascii=False))
    
    if not extracted_info["extraction_successful"]:
        logger.warning(f"LLM extraction was not successful. Error: {extracted_info.get('llm_error_message')}")
    else:
        logger.info("LLM extraction successful.")

    if send_to_slack:
        if SLACK_WEBHOOK_URL:
            logger.info("Sending result to Slack...")
            send_slack_notification(extracted_info)
        else:
            logger.warning("Slack notification requested, but SLACK_WEBHOOK_URL is not configured.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Debug GCN Circular Parser and LLM Extractor.")
    parser.add_argument("url_or_id", help="Full URL of the GCN circular (e.g., 'https://gcn.nasa.gov/circulars/36789') or just the ID (e.g., '36789').")
    parser.add_argument("--slack", action="store_true", help="Send the parsed result to Slack (if configured).") # Slackフラグ追加
    
    args = parser.parse_args()
    
    debug_parse_url(args.url_or_id, args.slack) # slackフラグを渡す
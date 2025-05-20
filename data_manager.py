# data_manager.py
import os
import json
import logging
from config import PROCESSED_CIRCULARS_FILE, OUTPUT_JSON_FILE

logger = logging.getLogger(__name__)

def load_processed_ids():
    """処理済みCircular IDをファイルから読み込む"""
    if not os.path.exists(PROCESSED_CIRCULARS_FILE):
        return set()
    try:
        with open(PROCESSED_CIRCULARS_FILE, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        logger.error(f"Error loading processed IDs from {PROCESSED_CIRCULARS_FILE}: {e}")
        return set()

def save_processed_id(circular_id):
    """処理済みCircular IDをファイルに追記する"""
    try:
        # Ensure the directory for the processed IDs file exists
        processed_ids_dir = os.path.dirname(PROCESSED_CIRCULARS_FILE)
        if processed_ids_dir and not os.path.exists(processed_ids_dir):
            os.makedirs(processed_ids_dir, exist_ok=True)
            
        with open(PROCESSED_CIRCULARS_FILE, 'a', encoding='utf-8') as f:
            f.write(str(circular_id) + '\n') # Ensure ID is string
    except Exception as e:
        logger.error(f"Error saving processed ID {circular_id} to {PROCESSED_CIRCULARS_FILE}: {e}")

def load_output_data():
    """既存の出力JSONデータを読み込む"""
    if os.path.exists(OUTPUT_JSON_FILE):
        try:
            with open(OUTPUT_JSON_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip(): 
                    return []
                return json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"Could not decode JSON from {OUTPUT_JSON_FILE}. Attempting to backup and start fresh.")
            backup_file = OUTPUT_JSON_FILE + ".bak." + time.strftime("%Y%m%d%H%M%S")
            try:
                os.rename(OUTPUT_JSON_FILE, backup_file)
                logger.info(f"Backed up corrupted {OUTPUT_JSON_FILE} to {backup_file}")
            except OSError as e_rename:
                logger.error(f"Could not backup corrupted file {OUTPUT_JSON_FILE}: {e_rename}")
            return []
        except Exception as e:
            logger.error(f"Error loading output data from {OUTPUT_JSON_FILE}: {e}")
            return []
    return []

def save_output_data(data_list):
    """抽出データをJSONファイルに保存する"""
    try:
        # Ensure the directory for the output file exists
        output_dir = os.path.dirname(OUTPUT_JSON_FILE)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        temp_file = OUTPUT_JSON_FILE + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, OUTPUT_JSON_FILE) 
        logger.debug(f"Data saved to {OUTPUT_JSON_FILE}")
    except Exception as e:
        logger.error(f"Error saving output data to {OUTPUT_JSON_FILE}: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
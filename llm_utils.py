# llm_utils.py
import requests
import json
import logging
import time
from config import OLLAMA_API_URL, LLM_MODEL, MAX_RETRIES_LLM, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

# --- JSON Schema (Python辞書として) ---
JSON_SCHEMA_DICT = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "GCN Circular Information",
    "description": "Extracted information from a GCN circular",
    "type": "object",
    "properties": {
        "circular_id": {"type": ["string", "null"], "description": "GCN circular identifier (e.g., '36789')"},
        "circular_url": {"type": ["string", "null"], "format": "uri", "description": "URL of the specific circular"},
        "subject": {"type": ["string", "null"], "description": "Subject line of the circular"},
        "is_trigger_event": {"type": "boolean", "description": "True if this circular reports the initial trigger/discovery of the event, False if it's a follow-up. Default to False if unclear or follow-up."},
        "event_time_utc": {"type": ["string", "null"], "description": "Time of the observation or the trigger event in UTC (YYYY-MM-DDTHH:MM:SSZ or as found). For follow-ups, this is the observation time."},
        "time_since_trigger": {"type": ["string", "null"], "description": "Time elapsed from the initial event trigger to this observation (e.g., '1.2 hours', '3 days', '0 seconds' or 'N/A' if this is the trigger)."},
        "ra": {"type": ["string", "null"], "description": "Right Ascension of the observation (e.g., HH:MM:SS.ss or decimal degrees). Null if not provided or a follow-up without new coordinates."},
        "dec": {"type": ["string", "null"], "description": "Declination of the observation (e.g., +/-DD:MM:SS.s or decimal degrees). Null if not provided or a follow-up without new coordinates."},
        "magnitude": {"type": ["string", "number", "null"], "description": "Observed magnitude. If multiple bands, report the brightest (smallest numeric value). If upper limit, this is the limit value."},
        "magnitude_error": {"type": ["string", "number", "null"], "description": "Error for the reported magnitude."},
        "is_upper_limit": {"type": "boolean", "description": "True if the reported magnitude is an upper limit. Default to False."},
        "wavelength_band": {"type": ["string", "null"], "description": "Wavelength/band for the reported magnitude (e.g., Optical, X-ray, g-band). If multiple, state for the brightest mag."},
        "multiple_bands_reported": {"type": "boolean", "description": "True if observations in multiple bands were mentioned, even if only one is primary. Default to False."},
        "telescope": {"type": ["string", "null"], "description": "Name of the telescope or instrument (e.g., 'Swift/XRT', 'ZTF')."},
        "observatory": {"type": ["string", "null"], "description": "Name of the observatory or facility (e.g., 'Palomar Observatory', 'Swift Satellite')."},
        "raw_text": {"type": "string", "description": "The full raw text content of the circular"},
        "extraction_successful": {"type": "boolean", "description": "True if LLM extraction was attempted and deemed successful, False otherwise"},
        "llm_error_message": {"type": ["string", "null"], "description": "Error message from LLM processing if extraction_successful is false"}
    },
    "required": ["circular_id", "circular_url", "raw_text", "extraction_successful"]
}

def get_default_extracted_data(circular_id, circular_url, subject, raw_text):
    """スキーマに基づいてデフォルトの抽出データ構造を返す"""
    data = {
        "circular_id": circular_id,
        "circular_url": circular_url,
        "subject": subject,
        "raw_text": raw_text,
        "extraction_successful": False,
        "llm_error_message": None,
        "is_trigger_event": False, # Default
        "is_upper_limit": False,   # Default
        "multiple_bands_reported": False # Default
    }
    for prop, details in JSON_SCHEMA_DICT["properties"].items():
        if prop not in data:
            # Default boolean handling already done for specific fields
            if details.get("type") == "boolean" and prop in ["is_trigger_event", "is_upper_limit", "multiple_bands_reported"]:
                continue
            data[prop] = None
    return data

def extract_info_with_llm(circular_text, circular_id, circular_url, subject):
    """LLMを使用してCircularテキストから情報を抽出する"""
    
    ordered_keys_for_prompt = [
        "is_trigger_event", "event_time_utc", "time_since_trigger",
        "ra", "dec",
        "magnitude", "magnitude_error", "is_upper_limit", "wavelength_band", "multiple_bands_reported",
        "telescope", "observatory"
    ]
    
    properties_description_list = []
    for prop in ordered_keys_for_prompt:
        details = JSON_SCHEMA_DICT["properties"].get(prop)
        if not details: continue

        desc = details.get('description', 'N/A')
        prop_type = details.get('type', 'any')
        type_str_parts = []
        if isinstance(prop_type, list):
            type_str_parts = [t for t in prop_type if t != "null"]
        else:
            type_str_parts = [str(prop_type)]
        type_str = " or ".join(type_str_parts)
        
        additional_info = ""
        if prop_type == "boolean" or "boolean" in type_str_parts:
             additional_info = " (boolean: true or false)"
        
        properties_description_list.append(f"- {prop} ({type_str}{additional_info}): {desc}")
    properties_description = "\n    ".join(properties_description_list)

    json_keys_structure_lines = []
    for key in ordered_keys_for_prompt:
        default_val_str = '"extracted_value_or_null"'
        if key in ["is_trigger_event", "is_upper_limit", "multiple_bands_reported"]:
            default_val_str = 'false' 
        json_keys_structure_lines.append(f'        "{key}": {default_val_str},')
    
    if json_keys_structure_lines:
        json_keys_structure_lines[-1] = json_keys_structure_lines[-1].rstrip(',')
    json_keys_structure = "{\n" + "\n".join(json_keys_structure_lines) + "\n    }"

    prompt = f"""
    You are an expert astronomical data extractor from GCN Circulars.
    Given the GCN Circular text below, extract the specified information and format it as a JSON object.
    Adhere strictly to the following JSON structure. Use `null` for fields if the information is not found or not applicable.
    For boolean fields (`is_trigger_event`, `is_upper_limit`, `multiple_bands_reported`), default to `false` if not explicitly determinable.
    Return ONLY the JSON object, with no other text before or after it.

    Target JSON structure to populate:
    {json_keys_structure}
    
    Field descriptions and extraction guidelines:
    {properties_description}

    Specific Extraction Rules:
    1.  `is_trigger_event`: Set to `true` if the circular announces the initial discovery or trigger of an event (e.g., "Swift detection of GRB...", "Discovery of a new transient..."). If it's a follow-up observation, an update, or analysis of a previously announced event, set to `false`.
    2.  `event_time_utc`: For trigger events, this is the trigger time. For follow-ups, this is the time of the observation reported in this circular.
    3.  `time_since_trigger`: If `is_trigger_event` is `true`, this should be "0 seconds" or similar indicating it's the trigger time itself. For follow-ups, calculate or extract the time difference from the trigger (e.g., "T0+1.2 hours", "2 days after trigger"). If trigger time is unknown or not referenced, use `null`.
    4.  `ra`, `dec`: If coordinates are not explicitly given in this circular (common for some follow-ups that refer to a previous position), set to `null`.
    5.  Multiple Bands & Brightest Magnitude:
        - If observations are reported in multiple wavelength bands (e.g., g, r, i, or Optical and X-ray):
            - Set `multiple_bands_reported` to `true`.
            - For `magnitude`, `magnitude_error`, `is_upper_limit`, and `wavelength_band`, report the values corresponding to the *brightest detection* (i.e., the numerically smallest magnitude value that is NOT an upper limit). If all detections are upper limits, report the *faintest* (numerically largest) upper limit.
            - If no clear "brightest" can be determined or if context is ambiguous, pick a representative optical band if available.
        - If only one band is reported, set `multiple_bands_reported` to `false`.
    6.  `is_upper_limit`: Set to `true` if the reported `magnitude` is an upper limit (e.g., "mag > 19.0", "limit of 20.5", "not detected down to 21", "fainter than 22"). Otherwise, `false`.
    7.  `observatory`: Might be the ground facility or the space mission itself (e.g., Swift, Fermi, ZTF is telescope, Palomar is observatory).

    GCN Circular Text:
    ---
    {circular_text}
    ---

    Extracted JSON Output:
    """
    logger.debug(f"LLM Prompt for circular {circular_id} (first 500 chars): {prompt[:500]}...")

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json" 
    }

    extracted_data = get_default_extracted_data(circular_id, circular_url, subject, circular_text)

    for attempt in range(MAX_RETRIES_LLM):
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            api_response_json = response.json()
            parsed_llm_json = None

            if "response" in api_response_json and isinstance(api_response_json["response"], str):
                llm_output_str = api_response_json["response"]
                try:
                    parsed_llm_json = json.loads(llm_output_str.strip())
                except json.JSONDecodeError as e_parse:
                    logger.warning(f"Initial JSON parse failed for {circular_id}. Response string: '{llm_output_str}'. Error: {e_parse}")
                    clean_json_str = llm_output_str.strip()
                    if clean_json_str.startswith("```json"): clean_json_str = clean_json_str[7:]
                    if clean_json_str.endswith("```"): clean_json_str = clean_json_str[:-3]
                    parsed_llm_json = json.loads(clean_json_str.strip())
            elif isinstance(api_response_json, dict) and "model" in api_response_json:
                if 'response' in api_response_json and isinstance(api_response_json['response'], (dict, list)):
                    parsed_llm_json = api_response_json['response']
                elif 'response' in api_response_json and isinstance(api_response_json['response'], str):
                     parsed_llm_json = json.loads(api_response_json['response'])
                else:
                    temp_json = {k: v for k, v in api_response_json.items() if k not in ['model', 'created_at', 'done', 'total_duration', 'load_duration', 'prompt_eval_count', 'prompt_eval_duration', 'eval_count', 'eval_duration', 'context']}
                    if temp_json: parsed_llm_json = temp_json
                    else: raise ValueError(f"Unexpected LLM API response (format=json, no clear data) for {circular_id}: {api_response_json}")
            else:
                raise ValueError(f"Unexpected LLM API response structure for {circular_id}: {api_response_json}")

            for key in JSON_SCHEMA_DICT["properties"].keys():
                if key in parsed_llm_json and key not in ["circular_id", "circular_url", "subject", "raw_text", "extraction_successful", "llm_error_message"]:
                    extracted_data[key] = parsed_llm_json[key]
            
            # Ensure booleans are booleans
            for bool_key in ["is_trigger_event", "is_upper_limit", "multiple_bands_reported"]:
                if isinstance(extracted_data.get(bool_key), str):
                    extracted_data[bool_key] = extracted_data[bool_key].lower() == "true"
                elif not isinstance(extracted_data.get(bool_key), bool):
                    extracted_data[bool_key] = False # Default if type is wrong

            extracted_data["extraction_successful"] = True
            logger.info(f"Successfully extracted data for circular {circular_id} using LLM.")
            return extracted_data

        except requests.exceptions.Timeout:
            logger.error(f"LLM API request timed out for circular {circular_id} (attempt {attempt+1}/{MAX_RETRIES_LLM}).")
            extracted_data["llm_error_message"] = f"API Request Timeout (attempt {attempt+1})"
        except requests.exceptions.RequestException as e:
            logger.error(f"LLM API request failed for circular {circular_id} (attempt {attempt+1}/{MAX_RETRIES_LLM}): {e}")
            extracted_data["llm_error_message"] = f"API Request Error: {e}"
        except json.JSONDecodeError as e:
            resp_text = response.text if 'response' in locals() and response else 'No response text available'
            logger.error(f"Failed to parse LLM JSON response for circular {circular_id} (attempt {attempt+1}/{MAX_RETRIES_LLM}): {e}. Response: {resp_text[:500]}")
            extracted_data["llm_error_message"] = f"JSON Decode Error: {e}. Raw LLM output: {resp_text[:200]}"
        except Exception as e:
            logger.error(f"An unexpected error occurred during LLM extraction for {circular_id} (attempt {attempt+1}/{MAX_RETRIES_LLM}): {e}", exc_info=True)
            extracted_data["llm_error_message"] = f"Unexpected Error: {str(e)}"
        
        if attempt < MAX_RETRIES_LLM - 1:
            sleep_time = 5 * (attempt + 1)
            logger.info(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
            
    logger.error(f"All LLM extraction attempts failed for circular {circular_id}.")
    return extracted_data
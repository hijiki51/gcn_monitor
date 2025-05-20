# slack_notifier.py
import requests
import json
import logging
from config import SLACK_WEBHOOK_URL, SLACK_CHANNEL, SLACK_USERNAME, SLACK_ICON_EMOJI

logger = logging.getLogger(__name__)

def format_slack_message(data):
    """抽出されたデータをSlackメッセージ用に整形する"""
    if not data.get("extraction_successful"):
        # ... (エラーメッセージ部分は変更なし) ...
        message = f"⚠️ Failed to extract data for GCN Circular <{data['circular_url']}|*{data['circular_id']}*>"
        if data.get("subject"):
            message += f"\n*Subject*: {data['subject']}"
        if data.get("llm_error_message"):
            message += f"\n*Error*: `{data['llm_error_message']}`"
        return {
            "text": message,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
        }

    subject = data.get("subject", "N/A")
    
    # ヘッダー構築
    header_icon = "🚨" if data.get("is_trigger_event") else "🛰️"
    header_text_parts = [f"{header_icon} GCN Circular {data['circular_id']}"]
    if data.get("is_trigger_event"):
        header_text_parts.append("- *TRIGGER EVENT*")
    
    tel_obs_parts = []
    if data.get("telescope"):
        tel_obs_parts.append(data['telescope'])
    if data.get("observatory") and data.get("observatory") != data.get("telescope"):
        tel_obs_parts.append(f"at {data['observatory']}")
    if tel_obs_parts:
        header_text_parts.append(f"({', '.join(tel_obs_parts)})")
    header_text = " ".join(header_text_parts)

    title_block_text = f"*Subject*: {subject}\n*<{data['circular_url']}|View Circular on GCN>*"

    fields = []
    
    # Coordinates
    if data.get("ra") and data.get("dec"):
        fields.append({"title": "RA / Dec", "value": f"`{data['ra']}` / `{data['dec']}`", "short": True})
    elif not data.get("is_trigger_event"): # トリガーでなく座標もない場合
        fields.append({"title": "Coordinates", "value": "_Not provided in this circular_", "short": True})

    # Magnitude block
    mag_str_parts = []
    mag_title = "Magnitude"
    if data.get("magnitude") is not None:
        mag_val = str(data['magnitude'])
        if data.get("is_upper_limit"):
            mag_str_parts.append(f"> {mag_val} (UL)")
        else:
            mag_str_parts.append(mag_val)
        
        if data.get("magnitude_error") is not None:
            mag_str_parts.append(f"± {data['magnitude_error']}")
        
        if data.get("wavelength_band"):
            mag_str_parts.append(f"[{data['wavelength_band']}]")

        mag_display_str = " ".join(mag_str_parts)
        if data.get("multiple_bands_reported"):
            mag_title = "Brightest Mag." # 複数バンド報告時は明示
            mag_display_str += " (multi-band)"

        fields.append({"title": mag_title, "value": f"`{mag_display_str}`", "short": True})
    elif data.get("wavelength_band"): # 等級なしでもバンド情報があれば表示
         fields.append({"title": "Band Obs.", "value": data['wavelength_band'], "short": True})


    # Time information
    if data.get("is_trigger_event"):
        if data.get("event_time_utc"):
            fields.append({"title": "Trigger Time (UTC)", "value": f"`{data['event_time_utc']}`", "short": True})
    else: # Follow-up
        if data.get("event_time_utc"):
            fields.append({"title": "Obs. Time (UTC)", "value": f"`{data['event_time_utc']}`", "short": True})
        if data.get("time_since_trigger"):
            fields.append({"title": "Time Since Trig.", "value": data['time_since_trigger'], "short": True})
    
    # Other info (Telescope/Observatory already in header, Wavelength_band with magnitude)
    # if data.get("wavelength_band") and data.get("magnitude") is None: # Only if not with mag
    #    fields.append({"title": "Band", "value": data['wavelength_band'], "short": True})


    mrkdwn_fields_elements = []
    for field in fields:
        mrkdwn_fields_elements.append(f"*{field['title']}*:\n{field['value']}")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": title_block_text}}
    ]

    if mrkdwn_fields_elements:
        blocks.append({"type": "divider"})
        field_sections = []
        for i in range(0, len(mrkdwn_fields_elements), 2):
            current_pair = [{"type": "mrkdwn", "text": mrkdwn_fields_elements[i]}]
            if i + 1 < len(mrkdwn_fields_elements):
                current_pair.append({"type": "mrkdwn", "text": mrkdwn_fields_elements[i+1]})
            field_sections.append({"type": "section", "fields": current_pair})
        blocks.extend(field_sections)

    payload = {
        "username": SLACK_USERNAME,
        "icon_emoji": SLACK_ICON_EMOJI,
        "blocks": blocks,
        "text": f"{header_icon} GCN {data['circular_id']}: {subject}" # Fallback
    }
    if SLACK_CHANNEL:
        payload["channel"] = SLACK_CHANNEL
        
    return payload

# send_slack_notification 関数は変更なし
def send_slack_notification(data):
    if not SLACK_WEBHOOK_URL:
        logger.debug("SLACK_WEBHOOK_URL not set. Skipping notification.")
        return False
    payload = format_slack_message(data)
    logger.debug(f"Slack payload: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(payload), headers={'Content-Type': 'application/json'}, timeout=30)
        response.raise_for_status()
        if response.text != "ok":
             logger.warning(f"Slack notification sent for circular {data.get('circular_id')}, but response was not 'ok': {response.text}")
        else:
            logger.info(f"Slack notification sent successfully for circular {data.get('circular_id')}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending Slack notification for circular {data.get('circular_id')}: {e}")
        if 'response' in locals() and response is not None: logger.error(f"Response content: {response.content}")
        return False
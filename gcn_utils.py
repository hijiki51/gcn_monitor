# gcn_utils.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
from config import GCN_CIRCULARS_INDEX_URL, BASE_GCN_URL, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

def get_page_content(url):
    """指定されたURLからページのHTMLコンテンツを取得する"""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        if response.encoding.lower() == 'iso-8859-1' and 'utf-8' in response.text.lower():
             response.encoding = 'utf-8'
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching page {url}: {e}")
        return None

def parse_gcn_circular_list(html_content):
    """GCN Circulars一覧ページをパースして、各Circularの情報を取得する"""
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, 'html.parser')
    circulars = []
    
    table = soup.find('table')
    if not table:
        pre_tags = soup.find_all('pre')
        if pre_tags:
            for pre_tag in pre_tags:
                links = pre_tag.find_all('a', href=True)
                for link in links:
                    href = link['href']
                    text = link.text.strip()
                    if (href.endswith('.gcn3') and text.isdigit()) or \
                       (href.startswith('/circulars/') and href.split('/')[-1].isdigit()):
                        
                        circular_id = text if text.isdigit() else href.split('/')[-1].replace('.gcn3', '')
                        if not circular_id.isdigit(): continue

                        if href.endswith('.gcn3'):
                            detail_page_url = urljoin(BASE_GCN_URL, f"/circulars/{circular_id}")
                        else:
                            detail_page_url = urljoin(BASE_GCN_URL, href)

                        subject_text = link.next_sibling
                        subject = subject_text.strip() if subject_text and isinstance(subject_text, str) else f"Subject for {circular_id}"

                        circulars.append({
                            'id': circular_id,
                            'url': detail_page_url,
                            'subject': subject
                        })
            if circulars:
                logger.info(f"Parsed {len(circulars)} circulars from <pre> tags.")
                return circulars
        logger.warning("Could not find the main table or <pre> tags in GCN circulars list.")
        return []
        
    for row in table.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) > 1: 
            link_tag = cells[0].find('a')
            if link_tag and link_tag.has_attr('href'):
                circular_id_text = link_tag.text.strip()
                if not circular_id_text.isdigit():
                    continue

                relative_url = link_tag['href']
                circular_id = ""
                circular_url = ""

                if relative_url.endswith('.gcn3'):
                    potential_id = relative_url.split('/')[-1].replace('.gcn3', '')
                    if potential_id.isdigit():
                        circular_id = potential_id
                        circular_url = urljoin(BASE_GCN_URL, f"/circulars/{circular_id}")
                    else:
                        continue
                elif relative_url.startswith('/circulars/'):
                    circular_id = circular_id_text
                    circular_url = urljoin(BASE_GCN_URL, relative_url)
                else:
                    continue
                
                subject = cells[1].text.strip() if len(cells) > 1 else None
                
                circulars.append({
                    'id': circular_id,
                    'url': circular_url,
                    'subject': subject
                })
    logger.info(f"Parsed {len(circulars)} circulars from table.")
    return circulars

def get_circular_raw_text_from_page(circular_page_url):
    """個別のCircularページ (HTML) から本文テキストを取得する"""
    html_content = get_page_content(circular_page_url)
    if not html_content:
        return None
    
    soup = BeautifulSoup(html_content, 'html.parser')
    pre_tag = soup.find('pre')
    if pre_tag:
        return pre_tag.text.strip()
    else:
        logger.warning(f"No <pre> tag found in {circular_page_url}. Attempting to extract from body, may include noise.")
        body_tag = soup.find('body')
        if body_tag:
            for s in body_tag(['script', 'style']):
                s.decompose()
            return body_tag.get_text(separator='\n', strip=True)
        return None

def get_circular_raw_text_from_gcn3_file(gcn3_url):
    """ .gcn3 ファイルから直接テキストを取得する (フォールバック用) """
    try:
        response = requests.get(gcn3_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        if response.encoding.lower() == 'iso-8859-1' and 'utf-8' in response.text.lower():
             response.encoding = 'utf-8'
        return response.text.strip()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching .gcn3 file {gcn3_url}: {e}")
        return None

def get_circular_text_robust(circular_id, circular_page_url):
    """
    GCN Circularの本文テキストを取得する。
    まずHTMLページを試し、失敗したらgcn3ファイル直リンクも試す。
    """
    logger.info(f"Attempting to get text for circular {circular_id} from page: {circular_page_url}")
    raw_text = get_circular_raw_text_from_page(circular_page_url)
    
    if raw_text and len(raw_text) > 50:
        if raw_text.strip().startswith("The GCN Circular system is evolving.") or \
           raw_text.strip().startswith("This GCN Circular is currently unavailable."):
            logger.warning(f"Circular {circular_id} page content seems to be a placeholder. Trying .gcn3 file.")
            raw_text = None 

    if not raw_text or len(raw_text) < 50 : 
        gcn3_file_url = urljoin(BASE_GCN_URL, f"/gcn3/{circular_id}.gcn3")
        logger.info(f"Failed to get sufficient text from HTML page or text was short/placeholder. Trying .gcn3 file: {gcn3_file_url}")
        raw_text_gcn3 = get_circular_raw_text_from_gcn3_file(gcn3_file_url)
        if raw_text_gcn3 and len(raw_text_gcn3) > len(raw_text or ""): 
            logger.info(f"Successfully fetched text from {gcn3_file_url}")
            return raw_text_gcn3
        elif raw_text:
             logger.info(f"Using text from HTML page as .gcn3 was not better or also failed.")
             return raw_text
        else:
            logger.warning(f"Could not retrieve valid text from HTML page or .gcn3 file for circular {circular_id}.")
            return None
            
    return raw_text
"""
Smart HSN Code & GST Rate Detector for BillZen AI
Provides instant local lookup for common retail/wholesale goods,
with AI fallback via OpenAI / Gemini.
"""

import os
import json
import re

# Comprehensive Indian Goods & Commodities HSN Dictionary
COMMODITY_HSN_DICT = {
    # Footwear & Leather (5% / 18%)
    "shoes": ("6403", 5), "shoe": ("6403", 5), "footwear": ("6403", 5), "leather shoes": ("6403", 18),
    "chappal": ("6402", 5), "sandals": ("6402", 5), "slipper": ("6402", 5), "boots": ("6403", 18),
    "sneakers": ("6404", 5), "heels": ("6404", 5), "socks": ("6115", 5),
    
    # Clothing & Garments & Textiles (5% / 18%)
    "shirt": ("6205", 5), "shirts": ("6205", 5), "t-shirt": ("6109", 5), "tshirt": ("6109", 5),
    "pant": ("6203", 5), "trousers": ("6203", 5), "jeans": ("6203", 5), "saree": ("5407", 5),
    "kurti": ("6204", 5), "suit": ("6203", 5), "dress": ("6204", 5), "jacket": ("6201", 5),
    "cotton cloth": ("5208", 5), "fabric": ("5208", 5), "towel": ("6302", 5), "bedsheet": ("6304", 5),
    "blanket": ("6301", 5), "curtain": ("6303", 5), "garments": ("6203", 5),

    # Electronics & Appliances (18%)
    "air conditioner": ("8415", 18), "ac": ("8415", 18), "split ac": ("8415", 18),
    "mobile": ("8517", 18), "mobile phone": ("8517", 18), "smartphone": ("8517", 18), "phone": ("8517", 18), "phones": ("8517", 18),
    "charger": ("8504", 18), "mobile charger": ("8504", 18), "power bank": ("8504", 18),
    "earphones": ("8518", 18), "headphones": ("8518", 18), "speaker": ("8518", 18),
    "laptop": ("8471", 18), "computer": ("8471", 18), "desktop": ("8471", 18), "monitor": ("8528", 18),
    "keyboard": ("8471", 18), "mouse": ("8471", 18), "printer": ("8443", 18), "cpu": ("8471", 18),
    "tablet": ("8471", 18), "television": ("8528", 18), "tv": ("8528", 18), "led tv": ("8528", 18),
    "refrigerator": ("8418", 18), "fridge": ("8418", 18), "washing machine": ("8450", 18),
    "microwave": ("8516", 18), "oven": ("8516", 18), "fan": ("8414", 18), "ceiling fan": ("8414", 18),
    "cooler": ("8479", 18), "air cooler": ("8479", 18), "geyser": ("8516", 18), "heater": ("8516", 18),
    "inverter": ("8504", 18), "battery": ("8507", 18), "ups": ("8504", 18), "cctv": ("8525", 18),
    "camera": ("8525", 18), "bulb": ("8539", 18), "led bulb": ("8539", 18), "wire": ("8544", 18),
    "cable": ("8544", 18), "switch": ("8536", 18),

    # Food, Groceries & FMCG (0% / 5%)
    "rice": ("1006", 0), "basmati rice": ("1006", 5), "wheat": ("1001", 0), "atta": ("1101", 0),
    "flour": ("1101", 0), "maida": ("1101", 0), "suji": ("1103", 0), "besan": ("1106", 0),
    "dal": ("0713", 0), "pulses": ("0713", 0), "sugar": ("1701", 5), "tea": ("0902", 5),
    "coffee": ("0901", 5), "oil": ("1512", 5), "mustard oil": ("1514", 5), "refined oil": ("1512", 5),
    "ghee": ("0405", 5), "butter": ("0405", 5), "milk": ("0401", 0), "paneer": ("0406", 5),
    "curd": ("0403", 0), "salt": ("2501", 0), "spices": ("0910", 5), "masala": ("0910", 5),
    "turmeric": ("0910", 5), "haldi": ("0910", 5), "chilli": ("0904", 5), "mirch": ("0904", 5),
    "biscuit": ("1905", 18), "biscuits": ("1905", 18), "namkeen": ("2106", 12), "chips": ("2005", 12),
    "soap": ("3401", 18), "shampoo": ("3305", 18), "toothpaste": ("3306", 18), "detergent": ("3402", 18),

    # Medicines & Pharma (5% / 12% -> 18%)
    "medicine": ("3004", 5), "tablet": ("3004", 5), "syrup": ("3004", 5), "capsule": ("3004", 5),
    "injection": ("3004", 5), "ointment": ("3004", 5), "bandage": ("3005", 5), "dettol": ("3808", 18),

    # Building Material & Hardware (18%)
    "cement": ("2523", 18), "tiles": ("6907", 18), "ceramic tiles": ("6907", 18), "paint": ("3208", 18),
    "pipe": ("3917", 18), "pvc pipe": ("3917", 18), "iron rod": ("7214", 18), "steel": ("7214", 18),
    "plywood": ("4412", 18), "glass": ("7005", 18), "sanitary": ("6910", 18), "tap": ("8481", 18),
    "faucet": ("8481", 18), "hardware": ("7318", 18), "nuts": ("7318", 18), "bolts": ("7318", 18),
    "screws": ("7318", 18),

    # Stationery & Packaging (0% / 18%)
    "book": ("4901", 0), "books": ("4901", 0), "notebook": ("4820", 0), "pen": ("9608", 18),
    "pencil": ("9609", 18), "paper": ("4802", 18), "carton": ("4819", 18), "box": ("4819", 18),
}

def detect_hsn_and_gst(item_name, shop_api_key=None, ai_provider=None):
    """
    Given an item name, returns (hsn_code, gst_pct, source).
    1. Checks local dictionary.
    2. If not found and AI key available, queries AI.
    3. Default fallback: ('', 18, 'default').
    """
    if not item_name or not str(item_name).strip():
        return "", 18, "default"

    raw = str(item_name).lower().strip()
    clean = re.sub(r'[^a-z0-9\s]', ' ', raw)
    words = clean.split()

    # 1. Exact match
    if clean in COMMODITY_HSN_DICT:
        hsn, gst = COMMODITY_HSN_DICT[clean]
        return hsn, gst, "dict"

    # 2. Key phrase match
    for k, (hsn, gst) in COMMODITY_HSN_DICT.items():
        if k in clean or any(w == k for w in words):
            return hsn, gst, "dict"

    # 3. AI Lookup Fallback
    try:
        # Check environment or shop keys
        openai_key = os.environ.get('OPENAI_API_KEY') or (shop_api_key if ai_provider == 'openai' else None)
        gemini_key = os.environ.get('GEMINI_API_KEY') or (shop_api_key if ai_provider == 'gemini' else None)

        if openai_key and (ai_provider == 'openai' or not gemini_key):
            import urllib.request
            prompt = f"For Indian GST, what is the standard 4-digit or 6-digit HSN code and applicable GST rate (0, 5, or 18) for product: '{item_name}'? Return ONLY valid JSON: {{\"hsn\": \"...\", \"gst\": 18}}."
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                data=json.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                }).encode('utf-8')
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                content = data['choices'][0]['message']['content'].strip()
                # Parse JSON
                m = re.search(r'\{.*?\}', content, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    hsn = str(parsed.get('hsn', ''))
                    gst = int(parsed.get('gst', 18))
                    if gst not in (0, 5, 18):
                        gst = 18 if gst >= 12 else 5
                    return hsn, gst, "ai"

        if gemini_key:
            import urllib.request
            prompt = f"For Indian GST, what is the standard 4-digit or 6-digit HSN code and applicable GST rate (0, 5, or 18) for product: '{item_name}'? Return ONLY JSON: {{\"hsn\": \"...\", \"gst\": 18}}."
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            req = urllib.request.Request(
                url,
                headers={"Content-Type": "application/json"},
                data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode('utf-8')
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                content = data['candidates'][0]['content']['parts'][0]['text'].strip()
                m = re.search(r'\{.*?\}', content, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    hsn = str(parsed.get('hsn', ''))
                    gst = int(parsed.get('gst', 18))
                    if gst not in (0, 5, 18):
                        gst = 18 if gst >= 12 else 5
                    return hsn, gst, "ai"
    except Exception:
        pass

    return "", 18, "default"

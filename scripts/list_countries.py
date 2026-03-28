
import json
import os
import urllib.request

COUNTRY_JSON_PATH = os.path.join(os.path.dirname(__file__), '../ai_engine/scripts/country_by_name.json')
COUNTRY_JSON_URL = 'https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-name.json'

FALLBACK_COUNTRIES = [
    "United States", "China", "Russia", "India", "Brazil", "Germany", "United Kingdom", "France", "Japan", "Canada"
]

def download_country_json():
    try:
        print(f"Downloading country list from {COUNTRY_JSON_URL} ...")
        urllib.request.urlretrieve(COUNTRY_JSON_URL, COUNTRY_JSON_PATH)
        print("Download complete.")
    except Exception as e:
        print(f"[ERROR] Failed to download country list: {e}")

def load_country_names():
    if not os.path.exists(COUNTRY_JSON_PATH):
        download_country_json()
    try:
        with open(COUNTRY_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return sorted({entry["country"] for entry in data if "country" in entry})
    except Exception as e:
        print(f"[WARNING] Could not load country list: {e}")
        return FALLBACK_COUNTRIES

if __name__ == "__main__":
    countries = load_country_names()
    print(f"Loaded {len(countries)} countries:")
    for c in countries:
        print(c)

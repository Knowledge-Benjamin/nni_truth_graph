import os
USER_AGENT = 'Mozilla/5.0 (LivingTruthGraph/1.0)'
def urlopen_with_headers(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    return urllib.request.urlopen(req)
import os
import urllib.request
import csv
import json
from io import StringIO

# --- World Leaders CSV to JSON ---


# --- World Leaders (CSV) ---


# --- Nobel Prize Winners ---
NOBEL_PRIZE_URL = 'https://api.nobelprize.org/v1/prize.json'
def parse_nobel_laureates():
    try:
        with urlopen_with_headers(NOBEL_PRIZE_URL) as resp:
            data = json.load(resp)
        laureates = []
        for prize in data.get('prizes', []):
            for l in prize.get('laureates', []):
                name = f"{l.get('firstname', '')} {l.get('surname', '')}".strip()
                if name:
                    laureates.append(name)
        print(f"Nobel Laureates ({len(laureates)}):", laureates[:10], '...')
        return laureates
    except Exception as e:
        print(f"[ERROR] Could not parse Nobel Prize API: {e}")
        print("[INFO] If API access is restricted, download the dataset manually from https://nobelprize.org and place it as 'nobel_prize.json' in the current directory.")
        # Fallback: try loading from local file
        try:
            with open('nobel_prize.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
            laureates = []
            for prize in data.get('prizes', []):
                for l in prize.get('laureates', []):
                    name = f"{l.get('firstname', '')} {l.get('surname', '')}".strip()
                    if name:
                        laureates.append(name)
            print(f"Nobel Laureates ({len(laureates)}):", laureates[:10], '...')
            return laureates
        except Exception as e2:
            print(f"[ERROR] Could not load local Nobel Prize JSON: {e2}")
            return []

# --- Country Capitals ---
CAPITALS_URL = 'https://raw.githubusercontent.com/samayo/country-json/master/src/country-by-capital-city.json'
def parse_country_capitals():
    try:
        with urlopen_with_headers(CAPITALS_URL) as resp:
            capitals = json.load(resp)
        capital_names = [f"{c['country']} ({c['city']})" for c in capitals if 'country' in c and 'city' in c]
        print(f"Country Capitals ({len(capital_names)}):", capital_names[:10], '...')
        return capital_names
    except Exception as e:
        print(f"[ERROR] Could not parse Country Capitals: {e}")
        return []

# --- Kaggle Celebrities ---
def parse_kaggle_celebrities(csv_path=None):
    if csv_path is None:
        # Default path for kagglehub
        csv_path = os.path.expanduser(r"~/.cache/kagglehub/datasets/lakshayjain611/imdb-top100-celebrities-dataset/versions/10/celebrity_data.csv")
    celebrities = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('Name') or row.get('name')
                if name:
                    celebrities.append(name)
        print(f"Kaggle Celebrities ({len(celebrities)}):", celebrities[:10], '...')
    except Exception as e:
        print(f"[ERROR] Could not parse Kaggle celebrity CSV: {e}")
    return celebrities

if __name__ == '__main__':
    n = parse_nobel_laureates()
    c = parse_country_capitals()
    k = parse_kaggle_celebrities()
    combined = (n or []) + (c or []) + (k or [])
    print(f"\nCombined array ({len(combined)}): {combined[:10]} ...")

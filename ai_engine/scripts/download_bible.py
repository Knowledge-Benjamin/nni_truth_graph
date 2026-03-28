import os
import sys
import json
import urllib.request

url = "https://raw.githubusercontent.com/thiagobodruk/bible/master/json/en_kjv.json"
json_path = "en_kjv.json"

if not os.path.exists(json_path):
    print(f"Downloading KJV JSON to {json_path}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            with open(json_path, 'wb') as f:
                f.write(response.read())
        print("Downloaded.")
    except Exception as e:
        print(f"Failed to download JSON: {e}")
        sys.exit(1)

with open(json_path, 'r', encoding='utf-8-sig') as f:
    data = json.load(f)

print(f"Loaded {len(data)} items.")
if len(data) > 0:
    print("Sample:", data[0])

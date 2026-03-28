import urllib.request
import csv
import json
from io import StringIO

# ====================== CONFIG ======================
url = "https://raw.githubusercontent.com/OEFDataScience/REIGN.github.io/gh-pages/data_sets/leader_list_8_21.csv"
json_filename = "leader_list.json"
# ===================================================

print("Downloading CSV...")
with urllib.request.urlopen(url) as response:
    csv_text = response.read().decode("utf-8")

print("Parsing CSV...")
csv_data = StringIO(csv_text)
reader = csv.DictReader(csv_data)          # uses first row as column headers
leaders = list(reader)                     # this is your array/list of dicts

# Preview
print(f"✅ Successfully parsed {len(leaders)} leaders!")
if leaders:
    print("First leader example:")
    print(json.dumps(leaders[0], indent=2))

# Save as JSON
print(f"Saving to {json_filename} ...")
with open(json_filename, "w", encoding="utf-8") as f:
    json.dump(leaders, f, indent=4, ensure_ascii=False)

print("✅ Done! JSON file created.")
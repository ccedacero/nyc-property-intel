import os
import subprocess
import requests
import warnings
from urllib3.exceptions import NotOpenSSLWarning

warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

APP_TOKEN = "REDACTED"
DATA_DIR = "data"

# The "Big Three" ACRIS Core Tables
ACRIS_CORE = {
    "acris_real_property_parties.csv": "636b-3b5g",  # ~46M rows
    "acris_real_property_master.csv": "bnx9-e6tj",   # ~17M rows
    "acris_real_property_legals.csv": "8abb-977r"    # ~22M rows
}

def get_remote_count(dataset_id):
    url = f"https://data.cityofnewyork.us/api/views/{dataset_id}.json"
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
        count = data.get('rowsCount') or data.get('columns', [{}])[0].get('cachedContents', {}).get('count')
        return int(count) if count is not None else 0
    except: return 0

def get_local_rows(filepath):
    if not os.path.exists(filepath): return 0
    try:
        output = subprocess.check_output(['wc', '-l', filepath]).decode('utf-8')
        return max(0, int(output.strip().split()[0]) - 1)
    except: return 0

def main():
    if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)
    
    print("🎯 Starting ACRIS Core Download (Critical for Deeds & Liens)...\n")
    
    for filename, ds_id in ACRIS_CORE.items():
        filepath = os.path.join(DATA_DIR, filename)
        expected = get_remote_count(ds_id)
        current = get_local_rows(filepath)
        
        # Skip if we already have a healthy file
        if expected > 0 and current >= (expected - 100):
            print(f"✅ {filename} is already healthy ({current:,} rows).")
            continue
            
        print(f"🚀 Downloading {filename} (~{expected:,} rows expected)...")
        if os.path.exists(filepath): os.remove(filepath)
        
        url = f"https://data.cityofnewyork.us/api/views/{ds_id}/rows.csv?accessType=DOWNLOAD"
        
        # Using caffeinate + curl for maximum stability on these massive files
        subprocess.run(['caffeinate', '-i', 'curl', '-L', '-#', 
                        '-H', f'X-App-Token: {APP_TOKEN}', 
                        '-o', filepath, url])
        
        final_rows = get_local_rows(filepath)
        if final_rows >= (expected - 100):
            print(f"✨ SUCCESS: {filename} verified with {final_rows:,} rows.\n")
        else:
            print(f"⚠️ WARNING: {filename} looks short ({final_rows:,} vs {expected:,}).\n")

if __name__ == "__main__":
    main()
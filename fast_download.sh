import os
import subprocess
import requests
import time
import warnings
from urllib3.exceptions import NotOpenSSLWarning

warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

APP_TOKEN = "REDACTED"
DATA_DIR = "data"

# The refined list of ONLY the 15 files that need refreshing (Duplicates removed)
DATASETS = {
    "acris_real_property_parties.csv": "636b-3b5g",
    "dof_property_valuation_and_assessments.csv": "8y4t-faws",
    "acris_personal_property_legals.csv": "uqqa-hym2",
    "acris_personal_property_master.csv": "sv7x-dduq",
    "hpd_complaints_and_problems.csv": "a2nx-4u46",
    "hpd_contacts.csv": "feu5-w2e2",
    "hpd_registrations.csv": "tesw-yqqr",
    "hpd_litigations.csv": "59kj-x8nc",
    "dob_now_jobs.csv": "8613-p88w",
    "dob_violations.csv": "3h2n-5cm9",
    "ecb_violations.csv": "6bgk-3dad",
    "dof_exemptions.csv": "7id6-duv6",      # Removed the duplicate classification codes
    "dof_sales.csv": "yg7y-7jbu",
    "dof_tax_lien_sale_list.csv": "9rz4-mjek",
    "pluto_latest.csv": "469e-p79z"
}

def get_remote_count(dataset_id):
    """Fetches the expected row count from the metadata API."""
    url = f"https://data.cityofnewyork.us/api/views/{dataset_id}.json"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        count = data.get('rowsCount')
        if count is None:
            columns = data.get('columns', [])
            for col in columns:
                cached = col.get('cachedContents')
                if cached and 'count' in cached:
                    return int(cached['count'])
        return int(count) if count is not None else 0
    except Exception as e:
        print(f"      [!] Failed to get metadata for {dataset_id}: {e}")
        return 0

def get_local_count(filepath):
    """Counts local lines extremely fast using the system 'wc' command."""
    if not os.path.exists(filepath):
        return 0
    try:
        output = subprocess.check_output(['wc', '-l', filepath]).decode('utf-8')
        return max(0, int(output.strip().split()[0]) - 1) # Subtract header
    except Exception:
        return 0

def download_file(filename, dataset_id, expected_rows, max_retries=3):
    """Downloads the file and verifies integrity immediately."""
    filepath = os.path.join(DATA_DIR, filename)
    url = f"https://data.cityofnewyork.us/api/views/{dataset_id}/rows.csv?accessType=DOWNLOAD"
    
    for attempt in range(1, max_retries + 1):
        print(f"  --> Attempt {attempt}/{max_retries} for {filename}...")
        
        # Remove partial/corrupted file before starting
        if os.path.exists(filepath):
            os.remove(filepath)
            
        # Run curl via subprocess for maximum network speed and reliability
        curl_cmd = [
            'curl', '-L', '-#', 
            '-H', f'X-App-Token: {APP_TOKEN}', 
            '-o', filepath, 
            url
        ]
        
        try:
            subprocess.run(curl_cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"      [!] Curl download interrupted.")
        
        # Immediate Integrity Check
        local_rows = get_local_count(filepath)
        
        # We allow a small tolerance (100 rows) because Socrata live sets update constantly
        if expected_rows > 0 and (expected_rows - local_rows) <= 100:
            print(f"      ✅ VERIFIED: {local_rows:,} rows match expected {expected_rows:,}.")
            return True
        elif expected_rows == 0 and local_rows > 0:
            print(f"      ⚠️ WARNING: Could not verify remote count, but downloaded {local_rows:,} rows.")
            return True
        else:
            diff = expected_rows - local_rows
            print(f"      ❌ FAILED: File is short by {diff:,} rows. (Got {local_rows:,} / Expected {expected_rows:,})")
            
            if attempt < max_retries:
                print("      ⏳ Waiting 5 seconds before retrying...")
                time.sleep(5)
            else:
                print("      🚨 MAX RETRIES REACHED. Moving to next file.")
                return False

def main():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    print("🚀 Starting Smart Download & Verification Process...\n")
    
    for filename, dataset_id in DATASETS.items():
        print(f"[*] Processing: {filename}")
        
        expected_rows = get_remote_count(dataset_id)
        if expected_rows == 0:
            print("    ⚠️ Could not reach API for target row count. Will download blindly.")
        else:
            print(f"    🎯 Target integrity: {expected_rows:,} rows.")
            
        download_file(filename, dataset_id, expected_rows)
        print("-" * 60)

    print("✨ All downloads processed.")

if __name__ == "__main__":
    main()
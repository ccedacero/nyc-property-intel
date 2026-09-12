from playwright.sync_api import sync_playwright

URL = "https://nycpropertyintel.com/r/1flN9UFc74E"
SCREENSHOT_TOPFOLD = "/Users/devtzi/dev/nyc-property-intel/screenshots/report_ui_topfold.png"

def capture_topfold():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        page.goto(URL, wait_until="networkidle", timeout=60000)
        # Capture only above-the-fold (viewport, not full page)
        page.screenshot(path=SCREENSHOT_TOPFOLD, full_page=False)
        browser.close()
        print("Topfold screenshot saved.")

if __name__ == "__main__":
    capture_topfold()

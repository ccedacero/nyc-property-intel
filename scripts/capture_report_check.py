from playwright.sync_api import sync_playwright
import json

URL = "https://nycpropertyintel.com/r/1flN9UFc74E"
SCREENSHOT_PATH = "/Users/devtzi/dev/nyc-property-intel/screenshots/report_ui_check.png"

def capture():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})

        console_errors = []
        network_issues = []

        page.on("console", lambda msg: console_errors.append({
            "type": msg.type,
            "text": msg.text
        }) if msg.type in ("error", "warning") else None)

        page.on("response", lambda resp: network_issues.append({
            "url": resp.url,
            "status": resp.status
        }) if resp.status >= 400 else None)

        page.goto(URL, wait_until="networkidle", timeout=60000)

        # Check for #report-print-btn inside #report-actions
        print_btn = page.query_selector("#report-print-btn")
        report_actions = page.query_selector("#report-actions")

        btn_visible = False
        btn_in_actions = False
        btn_bounding_box = None

        if print_btn:
            btn_visible = print_btn.is_visible()
            btn_bounding_box = print_btn.bounding_box()
            # Check if btn is inside report-actions
            if report_actions:
                btn_in_actions = report_actions.query_selector("#report-print-btn") is not None

        # Check report body content length
        report_body = page.query_selector(".report-body, #report-body, main, article, .report-content")
        body_text = ""
        if report_body:
            body_text = report_body.inner_text()

        # Also get full page text as fallback
        full_text = page.inner_text("body")

        # Check dark theme - look for dark background styles
        bg_color = page.evaluate("""() => {
            const body = document.body;
            const style = window.getComputedStyle(body);
            return {
                bodyBg: style.backgroundColor,
                bodyColor: style.color
            };
        }""")

        # Check if print stylesheet leaks into screen (look for display:none or other print-only elements)
        print_leak = page.evaluate("""() => {
            // Check if any elements that should only appear in print are visible on screen
            const allElements = document.querySelectorAll('*');
            const printOnlyVisible = [];
            for (const el of allElements) {
                const style = window.getComputedStyle(el);
                if (el.className && el.className.includes && el.className.includes('print-only') && style.display !== 'none') {
                    printOnlyVisible.push(el.tagName + '.' + el.className);
                }
            }
            return printOnlyVisible;
        }""")

        # Get the title/heading visible on page
        h1 = page.query_selector("h1")
        h1_text = h1.inner_text() if h1 else "NO H1 FOUND"
        h1_box = h1.bounding_box() if h1 else None

        # Check button position relative to title (should be near top/under title)
        above_fold = False
        if btn_bounding_box:
            above_fold = btn_bounding_box['y'] < 1080  # within viewport height

        # Capture full-page screenshot
        page.screenshot(path=SCREENSHOT_PATH, full_page=True)

        results = {
            "print_btn_found": print_btn is not None,
            "print_btn_visible": btn_visible,
            "print_btn_in_report_actions": btn_in_actions,
            "print_btn_bounding_box": btn_bounding_box,
            "above_fold": above_fold,
            "report_actions_found": report_actions is not None,
            "report_body_char_count": len(body_text),
            "full_page_char_count": len(full_text),
            "h1_text": h1_text,
            "h1_bounding_box": h1_box,
            "bg_colors": bg_color,
            "print_leak_elements": print_leak,
            "console_errors": console_errors,
            "network_4xx": network_issues,
        }

        browser.close()
        return results

if __name__ == "__main__":
    results = capture()
    print(json.dumps(results, indent=2))

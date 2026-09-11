from playwright.sync_api import sync_playwright
import json

URL = "https://nycpropertyintel.com/r/1flN9UFc74E"
SCREENSHOT_PATH = "/Users/devtzi/dev/nyc-property-intel/screenshots/shared_report_check.png"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        network_log = []

        def on_response(response):
            network_log.append({
                "url": response.url,
                "status": response.status,
            })

        page.on("response", on_response)

        # Navigate and wait for network idle (API fetch happens after load)
        page.goto(URL, wait_until="networkidle", timeout=30000)

        # Extra wait in case JS fetch is slow
        try:
            page.wait_for_function(
                "() => document.getElementById('report-title') && "
                "document.getElementById('report-title').innerText !== 'Loading report…'",
                timeout=15000
            )
        except Exception:
            pass  # We'll report what we see

        # --- 1. Network errors ---
        errors_4xx = [r for r in network_log if r["status"] >= 400]
        all_responses = network_log

        # --- 2. CSS applied ---
        body_bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        report_title_ff = page.evaluate(
            "() => { "
            "  const el = document.getElementById('report-title'); "
            "  return el ? getComputedStyle(el).fontFamily : 'ELEMENT_NOT_FOUND'; "
            "}"
        )

        # --- 3. Report populated ---
        title_text = page.evaluate(
            "() => { "
            "  const el = document.getElementById('report-title'); "
            "  return el ? el.innerText : 'ELEMENT_NOT_FOUND'; "
            "}"
        )
        body_content = page.evaluate(
            "() => { "
            "  const el = document.getElementById('report-body'); "
            "  return el ? el.innerText : 'ELEMENT_NOT_FOUND'; "
            "}"
        )
        body_length = len(body_content) if body_content != "ELEMENT_NOT_FOUND" else 0

        # --- 4. Watch box ---
        watch_visible = page.evaluate(
            "() => { "
            "  const el = document.getElementById('report-watch'); "
            "  if (!el) return 'ELEMENT_NOT_FOUND'; "
            "  const rect = el.getBoundingClientRect(); "
            "  const style = getComputedStyle(el); "
            "  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0; "
            "}"
        )

        # --- 5. Screenshot ---
        page.screenshot(path=SCREENSHOT_PATH, full_page=True)

        browser.close()

        # --- Report ---
        print("=" * 60)
        print("NETWORK LOG (all responses):")
        for r in all_responses:
            flag = "  [4xx/5xx ERROR]" if r["status"] >= 400 else ""
            print(f"  {r['status']}  {r['url']}{flag}")

        print()
        print("=" * 60)
        print("CHECK 1 — Network errors (4xx / 5xx):")
        if errors_4xx:
            print(f"  FAIL — {len(errors_4xx)} error(s):")
            for r in errors_4xx:
                print(f"    {r['status']}  {r['url']}")
        else:
            print("  PASS — Zero 4xx/5xx responses")

        print()
        print("CHECK 2 — CSS applied:")
        print(f"  body background-color : {body_bg}")
        print(f"  #report-title font-family: {report_title_ff}")
        # Dark theme would be rgb(something dark), not rgb(0,0,0) default white
        default_white = body_bg in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)", "")
        serif_default = "Times" in report_title_ff or "serif" == report_title_ff.strip().lower()
        if not default_white and not serif_default:
            print("  PASS — Non-default styling detected (dark theme / sans-serif)")
        elif default_white:
            print("  FAIL — body background is default white; CSS likely not applied")
        else:
            print("  WARN — background non-default but font-family looks like browser default serif")

        print()
        print("CHECK 3 — Report populated:")
        print(f"  #report-title text : {repr(title_text[:120])}")
        print(f"  #report-body length: {body_length} chars")
        loading_stuck = "loading report" in title_text.lower() or "loading this property" in body_content.lower()
        if not loading_stuck and body_length > 200:
            print("  PASS — Title populated and body has content")
        elif loading_stuck:
            print("  FAIL — Page is still stuck on loading state")
        else:
            print(f"  FAIL — Body content too short ({body_length} chars) or title not populated")

        print()
        print("CHECK 4 — Watch box (#report-watch):")
        if watch_visible == "ELEMENT_NOT_FOUND":
            print("  FAIL — Element #report-watch not found in DOM")
        elif watch_visible:
            print("  PASS — #report-watch is visible")
        else:
            print("  FAIL — #report-watch exists but is hidden or zero-size")

        print()
        print("CHECK 5 — Screenshot:")
        print(f"  Saved to: {SCREENSHOT_PATH}")

        print()
        print("=" * 60)

if __name__ == "__main__":
    run()

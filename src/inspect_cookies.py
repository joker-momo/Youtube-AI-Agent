import asyncio
import sys
from pathlib import Path

# Since this runs inside the container, PYTHONPATH already has /app/src
from video_agent.orchestrator.browser_client import BrowserClient

async def main():
    # Let's query cookies via playwright CDP using the container's remote URL
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        # Native Chromium with CDP on localhost
        client = BrowserClient(base_url="http://localhost:8001") # not needed

        from video_agent.browser_worker.app import _resolve_browser_ws
        try:
            ws_url = await _resolve_browser_ws("http://127.0.0.1:9222")
            print("WS URL:", ws_url)
        except Exception as e:
            print("Failed to get WS URL:", e)
            return
            
        browser = await pw.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0]
        cookies = await context.cookies()
        print(f"Total cookies: {len(cookies)}")
        
        # Print domains and sizes
        domain_counts = {}
        for c in cookies:
            dom = c["domain"]
            domain_counts[dom] = domain_counts.get(dom, 0) + 1
            
        print("\nCookie counts by domain:")
        for dom, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {dom}: {count}")
            
        # Print large cookies (size > 100 bytes)
        print("\nLarge cookies (> 500 bytes):")
        for c in cookies:
            val_len = len(c["value"])
            if val_len > 500:
                print(f"  Domain: {c['domain']}, Name: {c['name']}, Length: {val_len}")

if __name__ == "__main__":
    asyncio.run(main())

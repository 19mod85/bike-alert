import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from playwright.async_api import async_playwright

CANYON_FILTERS = (
    "?prefn1=pc_familie"
    "&prefv1=Endurace"
    "&prefn2=pc_rahmengroesse"
    "&prefv2=L"
    "&searchType=bikes"
    "&srule=sort_price_ascending"
)

COUNTRIES = {
    "Polska": f"https://www.canyon.com/pl-pl/rowery-wyprzedaz/{CANYON_FILTERS}",
    "Niemcy": f"https://www.canyon.com/de-de/fahrrad-outlet/{CANYON_FILTERS}",
    "Wielka Brytania": f"https://www.canyon.com/en-gb/outlet-bikes/road-bikes/{CANYON_FILTERS}",
    "Francja": f"https://www.canyon.com/fr-fr/promo-velos/{CANYON_FILTERS}",
    "Włochy": f"https://www.canyon.com/it-it/bici-outlet/bici-da-corsa/{CANYON_FILTERS}",
    "Hiszpania": f"https://www.canyon.com/es-es/bicicletas-outlet/carretera/{CANYON_FILTERS}",
}
STATE_FILE = Path("canyon_state.json")


def clean_url(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def send_ntfy_notification(title, message, click_url):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("[NTFY] NTFY_TOPIC missing; notification would be:")
        print(f"[NTFY] {title} | {click_url}")
        return True

    endpoint = f"https://ntfy.sh/{topic}"
    print(f"[NTFY] Sending: {title}")
    print(f"[NTFY] Click: {click_url}")

    try:
        response = requests.post(
            endpoint,
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Click": click_url,
                "Priority": "high",
                "Tags": "bicycling,shopping",
            },
            timeout=10,
        )
        print(f"[NTFY] HTTP {response.status_code}")
        print(f"[NTFY] Response: {response.text[:500]}")
        if 200 <= response.status_code < 300:
            print("[NTFY] SUCCESS")
            return True
        print("[NTFY] FAILURE - not marking bike as seen")
        return False
    except Exception as e:
        print(f"[NTFY] ERROR {type(e).__name__}: {e}")
        print("[NTFY] FAILURE - not marking bike as seen")
        return False


def load_state():
    if not STATE_FILE.exists():
        print("[STATE] File does not exist; starting empty.")
        return {"seen_bikes": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            data = {"seen_bikes": data}
        seen = data.get("seen_bikes", [])
        if not isinstance(seen, list):
            raise ValueError("seen_bikes is not a list")
        print(f"[STATE] Loaded {len(seen)} IDs")
        return {"seen_bikes": seen}
    except Exception as e:
        print(f"[STATE] ERROR loading state: {type(e).__name__}: {e}")
        return {"seen_bikes": []}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[STATE] Saved {len(state['seen_bikes'])} IDs")


async def accept_cookies(page):
    selectors = [
        "button[data-cookieconsent='accept']",
        ".cookie-banner__button--accept",
        "button:has-text('Accept')",
        "button:has-text('Akceptuj')",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Accetta')",
        "button:has-text('Aceptar')",
        "#js-cookie-banner-accept",
        "button.wt-cookie-consent-accept",
    ]
    for selector in selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(1000)
                print(f"[COOKIES] Accepted using {selector}")
                break
        except Exception:
            pass


async def collect_bikes(page, country, url):
    found = []
    print("\n" + "=" * 80)
    print(f"[{country}] START")
    print(f"[{country}] URL: {url}")
    print("=" * 80)

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if response:
            print(f"[{country}] HTTP status: {response.status}")
        await accept_cookies(page)

        for i in range(4):
            await page.evaluate(
                "window.scrollBy(0, document.body.scrollHeight / 3)"
            )
            await page.wait_for_timeout(1000)
            print(f"[{country}] Scroll {i + 1}/4")

        products = await page.evaluate("""() => {
            const items = [];
            const selectors = [
                '.productGrid__item', '.product-tile', 'div.grid-product',
                'li.product-grid__item', 'div[class*="productGrid"]',
                'div[class*="product-tile"]', 'article.product'
            ];
            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length) break;
            }
            if (!cards.length) {
                cards = document.querySelectorAll(
                    "a[href*='/p/'], a[href*='/rowery/'], " +
                    "a[href*='/fahrrad/'], a[href*='/bici/'], " +
                    "a[href*='/bicicletas/']"
                );
            }
            cards.forEach(card => {
                const link = card.tagName.toLowerCase() === 'a'
                    ? card : card.querySelector('a');
                const href = link ? link.getAttribute('href') : null;
                const text = card.innerText || '';
                const priceEl = card.querySelector(
                    '.product-tile__price, .price, .price__regular, ' +
                    '.product-price, span[class*="price"]'
                );
                const priceText = priceEl ? priceEl.innerText : '';
                if (href) items.push({href, text, priceText});
            });
            return items;
        }""")

        print(f"[{country}] Cards found: {len(products)}")

        for n, item in enumerate(products, 1):
            try:
                href = item.get("href")
                text = item.get("text", "")
                price_text = item.get("priceText", "")
                if not href:
                    print(f"[{country}] Card {n}: no href")
                    continue
                if href.startswith("/"):
                    href = "https://www.canyon.com" + href
                elif not href.startswith("http"):
                    print(f"[{country}] Card {n}: unsupported href")
                    continue

                href = clean_url(href)
                lines = [x.strip() for x in text.split("\n") if x.strip()]
                title = lines[0] if lines else f"Canyon {country}"

                price = price_text.strip().replace("\n", " ") if price_text.strip() else "Brak ceny"
                if price == "Brak ceny":
                    m = re.search(
                        r"([\d\s,\.]+\s*(?:zł|PLN|€|EUR|£|GBP))",
                        text, re.I
                    )
                    if m:
                        price = m.group(1).strip()

                if any(x["url"] == href for x in found):
                    print(f"[{country}] Card {n}: duplicate")
                    continue

                found.append({
                    "country": country,
                    "title": title,
                    "price": price,
                    "url": href,
                })
                print(f"[{country}] Candidate: {title} | {price} | {href}")

            except Exception as e:
                print(f"[{country}] Card {n}: ERROR {type(e).__name__}: {e}")

    except Exception as e:
        print(f"[{country}] COLLECTION ERROR {type(e).__name__}: {e}")

    print(f"[{country}] Unique candidates: {len(found)}")
    return found


async def main():
    state = load_state()
    seen = set(state.get("seen_bikes", []))

    print("=" * 80)
    print("CANYON BIKE ALERT")
    print("Canyon URL filter: Endurace only")
    print("Python model filter: DISABLED")
    print("Python DI2 filter: DISABLED")
    print(f"Existing state IDs: {len(seen)}")
    print("=" * 80)

    total = new = already_seen = sent = failed = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="pl-PL",
        )
        page = await context.new_page()

        try:
            for country, url in COUNTRIES.items():
                bikes = await collect_bikes(page, country, url)
                total += len(bikes)

                print(f"[{country}] Processing {len(bikes)} candidates")

                for i, bike in enumerate(bikes, 1):
                    bike_id = hashlib.md5(
                        bike["url"].encode("utf-8")
                    ).hexdigest()

                    print(f"[{country}] Candidate {i}/{len(bikes)}")
                    print(f"[{country}]   title={bike['title']}")
                    print(f"[{country}]   price={bike['price']}")
                    print(f"[{country}]   url={bike['url']}")
                    print(f"[{country}]   id={bike_id}")

                    if bike_id in seen:
                        already_seen += 1
                        print(f"[{country}]   STATUS=SEEN")
                        continue

                    new += 1
                    print(f"[{country}]   STATUS=NEW")

                    title = (
                        f"Canyon Endurace - {bike['country']} - {bike['title']}"
                    )
                    message = (
                        f"Model: {bike['title']}\n"
                        f"Cena: {bike['price']}\n"
                        f"Kraj: {bike['country']}\n"
                        f"URL: {bike['url']}"
                    )

                    if send_ntfy_notification(title, message, bike["url"]):
                        seen.add(bike_id)
                        sent += 1
                        print(f"[{country}]   STATE=MARKED_SEEN")
                    else:
                        failed += 1
                        print(f"[{country}]   STATE=NOT_MARKED")

                await asyncio.sleep(3)
        finally:
            await browser.close()

    state["seen_bikes"] = sorted(seen)
    save_state(state)

    print("=" * 80)
    print("FINAL SUMMARY")
    print(f"Candidates found: {total}")
    print(f"Already seen: {already_seen}")
    print(f"New candidates: {new}")
    print(f"Notifications successful: {sent}")
    print(f"Notifications failed: {failed}")
    print(f"State IDs saved: {len(seen)}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())

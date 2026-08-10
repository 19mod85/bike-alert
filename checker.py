import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
import requests
from playwright.async_api import async_playwright

# Konfiguracja
COUNTRIES = {
    "Polska": "https://www.canyon.com/pl-pl/rowery-wyprzedaz/?prefn1=pc_rahmengroesse&prefn2=pc_welt&prefv1=L&prefv2=Szosa&searchType=bikes&srule=sort_price_ascending",
    "Niemcy": "https://www.canyon.com/de-de/fahrrad-outlet/?prefn1=pc_familie&prefn2=pc_rahmengroesse&prefv1=Endurace%7CEndurace%3AON%7CAeroad%7CUltimate%7CSpeedmax%7CInflite&prefv2=L&searchType=bikes&srule=sort_price_ascending",
    "Wielka Brytania": "https://www.canyon.com/en-gb/outlet-bikes/road-bikes/?prefn1=pc_rahmengroesse&prefv1=L&srule=sort_price_ascending",
    "Francja": "https://www.canyon.com/fr-fr/promo-velos/?prefn1=pc_familie&prefn2=pc_rahmengroesse&prefv1=Endurace%7CEndurace%3AON%7CAeroad%7CUltimate%7CSpeedmax%7CInflite&prefv2=L&searchType=bikes&srule=sort_price_ascending",
}

TARGET_MODELS = ["cf 7", "cf 8"]
REQUIRED_KEYWORDS = ["di2"]
STATE_FILE = Path("canyon_state.json")


def send_ntfy_notification(title, message, click_url):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[ALERT] {title}\n{message}\nLink: {click_url}\n")
        return

    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Click": click_url,
                "Priority": "high",
                "Tags": "bicycling,shopping",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"Błąd wysyłania NTFY: {e}")


def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            if isinstance(data, list):
                return {"seen_bikes": data}
            return data
        except Exception:
            return {"seen_bikes": []}
    return {"seen_bikes": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


async def accept_cookies(page):
    """Próbuje zamknąć baner cookies w różnych wersjach językowych."""
    cookie_selectors = [
        "button[data-cookieconsent='accept']",
        ".cookie-banner__button--accept",
        "button:has-text('Accept')",
        "button:has-text('Akceptuj')",
        "button:has-text('Alle akzeptieren')",
        "#js-cookie-banner-accept",
    ]
    for selector in cookie_selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(1000)
                break
        except Exception:
            continue


async def collect_bikes(page, country, url):
    found = []
    try:
        print(f"Sprawdzam kraj: {country}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Obsługa ciasteczek
        await accept_cookies(page)
        
        # Przewijanie strony, aby wymusić lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

        product_selectors = [
            ".productGrid__item",
            ".product-tile",
            "div.grid-product",
            "li.product-grid__item"
        ]
        
        cards = []
        for sel in product_selectors:
            cards = await page.locator(sel).all()
            if cards:
                break
                
        if not cards:
            cards = await page.locator("a.product-tile__link, a.js-product-tile-link").all()

        print(f"Znaleziono elementów w kategorii {country}: {len(cards)}")

        for card in cards:
            try:
                text = await card.inner_text()
                
                # Wyciąganie linku do produktu
                link_elem = card.locator("a").first
                href = await link_elem.get_attribute("href") if await link_elem.count() > 0 else None
                
                if not href and await card.evaluate("el => el.tagName.toLowerCase()") == "a":
                    href = await card.get_attribute("href")

                if not href:
                    continue
                    
                if href.startswith("/"):
                    href = f"https://www.canyon.com{href}"

                text_lower = text.lower()

                # Sprawdzenie dopasowania modeli oraz słów kluczowych
                model_matched = any(model in text_lower for model in TARGET_MODELS)
                keyword_matched = any(kw in text_lower for kw in REQUIRED_KEYWORDS)

                if model_matched and keyword_matched:
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    title = lines[0] if lines else f"Canyon {country}"
                    
                    # Próba wyciągnięcia ceny
                    price = "Brak ceny"
                    price_selectors = [
                        ".product-tile__price", 
                        ".price", 
                        ".price__regular", 
                        ".product-price",
                        "span[class*='price']"
                    ]
                    for p_sel in price_selectors:
                        p_elem = card.locator(p_sel).first
                        if await p_elem.count() > 0:
                            p_text = await p_elem.inner_text()
                            if p_text.strip():
                                price = p_text.strip().replace("\n", " ")
                                break
                    
                    # Awaryjne wyciąganie ceny przez Regex, jeśli selektory zawiodły
                    if price == "Brak ceny":
                        price_match = re.search(r'([\d\s,\.]+\s*(?:zł|PLN|€|EUR|£|GBP))', text, re.IGNORECASE)
                        if price_match:
                            price = price_match.group(1).strip()

                    found.append({
                        "country": country,
                        "title": title,
                        "price": price,
                        "url": href,
                    })
            except Exception:
                continue

    except Exception as e:
        print(f"Błąd podczas pobierania {country}: {e}")
        
    return found


async def main():
    state = load_state()
    seen_bikes = set(state.get("seen_bikes", []))
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        all_new_bikes = []

        for country, url in COUNTRIES.items():
            bikes = await collect_bikes(page, country, url)
            for bike in bikes:
                bike_id = hashlib.md5(bike["url"].encode()).hexdigest()
                
                if bike_id not in seen_bikes:
                    seen_bikes.add(bike_id)
                    all_new_bikes.append(bike)
                    
                    title = f"Rowerowa okazja! ({bike['country']})"
                    message = f"Model: {bike['title']}\nCena: {bike['price']}\nKraj: {bike['country']}"
                    send_ntfy_notification(title, message, bike["url"])
            
            await asyncio.sleep(2)

        await browser.close()
        
        state["seen_bikes"] = list(seen_bikes)
        save_state(state)
        
        print(f"Zakończono. Znaleziono nowych okazji: {len(all_new_bikes)}")

if __name__ == "__main__":
    asyncio.run(main())

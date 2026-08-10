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
    "Polska": "https://www.canyon.com/pl-pl/outlet-rowery/szosa/",
    "Niemcy": "https://www.canyon.com/de-de/outlet-fahrrader/rennrad/",
    "Wielka Brytania": "https://www.canyon.com/en-gb/outlet-bikes/road-bikes/",
    "Francja": "https://www.canyon.com/fr-fr/outlet-velos/route/",
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
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


async def collect_bikes(page, country, url):
    found = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        # Odrzucenie cookies
        for btn in ['button:has-text("Accept")', 'button:has-text("Akceptuję")']:
            try:
                await page.locator(btn).first.click(timeout=1000)
                break
            except Exception:
                pass

        # Przewijanie dla lazy loading
        for _ in range(4):
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(500)

        # Pobranie linków do produktów
        cards = page.locator('a[href*="/outlet-"]')
        count = await cards.count()

        for i in range(count):
            card = cards.nth(i)
            href = await card.get_attribute("href")
            text = (await card.inner_text()).lower()

            if not href or not text:
                continue

            matches_model = any(m in text for m in TARGET_MODELS)
            has_di2 = any(k in text for k in REQUIRED_KEYWORDS)

            if matches_model and has_di2:
                full_url = href if href.startswith("http") else f"https://www.canyon.com{href}"
                
                # Wyciągnięcie ceny z tekstu kafelka
                price_match = re.search(r'([\d\s.,]+)\s*(zł|€|£|PLN|EUR|GBP)', text, re.I)
                price_str = price_match.group(0) if price_match else "Brak ceny"

                found.append({
                    "country": country,
                    "url": full_url,
                    "title": text.split("\n")[0].upper(),
                    "price": price_str,
                    "hash": hashlib.sha1(full_url.encode()).hexdigest()
                })
    except Exception as e:
        print(f"Błąd skanowania {country}: {e}")

    return found


async def main():
    state = load_state()
    new_state = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        for country, url in COUNTRIES.items():
            print(f"Sprawdzam: {country}...")
            bikes = await collect_bikes(page, country, url)

            for bike in bikes:
                h = bike["hash"]
                new_state[h] = bike

                # Wyślij powiadomienie tylko jeśli oferta jest nowa
                if h not in state:
                    title = f"🆕 OKAZJA Canyon ({bike['country']})!"
                    msg = f"Model: {bike['title']}\nCena: {bike['price']}"
                    send_ntfy_notification(title, msg, bike["url"])

        await browser.close()

    save_state(new_state)


if __name__ == "__main__":
    asyncio.run(main())
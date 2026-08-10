import os
import requests
from bs4 import BeautifulSoup

# Lista krajów i adresów URL do outletu/wyprzedaży szosowej w Canyon
COUNTRIES = {
    "Polska": "https://www.canyon.com/pl-pl/outlet-rowery/szosa/",
    "Niemcy": "https://www.canyon.com/de-de/outlet-fahrrader/rennrad/",
    "Wielka Brytania": "https://www.canyon.com/en-gb/outlet-bikes/road-bikes/",
    "Francja": "https://www.canyon.com/fr-fr/outlet-velos/route/",
}

# Kryteria wyszukiwania
TARGET_MODELS = ["cf 7", "cf 8"]
REQUIRED_KEYWORDS = ["di2"]  # Wymusza elektroniczną zmianę biegów


def send_push_notification(title, message, click_url):
  topic = os.environ.get("NTFY_TOPIC")
  if not topic:
    print("Brak skonfigurowanego tematu NTFY_TOPIC!")
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
    )
  except Exception as e:
    print(f"Błąd wysyłania powiadomienia: {e}")


def check_canyon_outlet():
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/122.0.0.0 Safari/537.36"
      )
  }

  found_bikes = []

  for country, url in COUNTRIES.items():
    print(f"Sprawdzam rynek: {country} ({url})")
    try:
      response = requests.get(url, headers=headers, timeout=20)
      if response.status_code != 200:
        print(f"  -> Błąd pobierania strony (Status: {response.status_code})")
        continue

      soup = BeautifulSoup(response.text, "html.parser")

      # Wyszukiwanie kafelków produktów na stronie Canyon
      products = soup.find_all(
          ["div", "li"], class_=lambda x: x and "product" in x.lower()
      )

      for product in products:
        text = product.get_text().lower()

        # Warunki logiczne dopasowania
        matches_model = any(model in text for model in TARGET_MODELS)
        has_di2 = any(kw in text for kw in REQUIRED_KEYWORDS)
        has_carbon = (
            "carbon" in text or "cf" in text
        )  # Upewnienie się co do ramy/karbonu

        if matches_model and has_di2 and has_carbon:
          # Wyciąganie linku do konkretnego roweru
          link_tag = product.find("a", href=True)
          bike_url = link_tag["href"] if link_tag else url
          if not bike_url.startswith("http"):
            bike_url = "https://www.canyon.com" + bike_url

          # Wyciąganie nazwy
          title_tag = product.find(
              class_=lambda x: x
              and any(c in x.lower() for c in ["title", "name", "headline"])
          )
          bike_name = (
              title_tag.get_text().strip()
              if title_tag
              else f"Canyon {country} Szosa"
          )

          bike_info = {
              "country": country,
              "name": bike_name,
              "url": bike_url,
          }

          if bike_info not in found_bikes:
            found_bikes.append(bike_info)

    except Exception as e:
      print(f"  -> Wyjątek podczas sprawdzania {country}: {e}")

  # Wysyłanie powiadomień na telefon
  for bike in found_bikes:
    title = f"OKAZJA Canyon ({bike['country']})!"
    message = (
        f"Znaleziono: {bike['name']}\nKliknij, aby otworzyć ofertę w sklepie!"
    )
    print(f"Wysyłam alert dla: {bike['name']} ({bike['country']})")
    send_push_notification(title, message, bike["url"])


if __name__ == "__main__":
  check_canyon_outlet()
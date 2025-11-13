#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import time
import requests
import logging
import random
import re
from pathlib import Path
from dotenv import load_dotenv

# === Настройки ===
CHECK_INTERVAL_MINUTES = 60      # интервал между циклами мониторинга
ROUTE_DELAY_SECONDS = 5          # пауза между разными маршрутами
SAME_ROUTE_DELAY_MINUTES = 1    # пауза между одинаковыми маршрутами (в минутах)
BASE_DIR = Path(__file__).resolve().parent
ROUTES_FILE = BASE_DIR / "routes.json"
PREV_PRICES_FILE = BASE_DIR / "prev_prices.json"
AIRPORTS_FILE = BASE_DIR / "airports.json"  # Файл с кодами аэропортов
BUILD_NUMBER_URL = "https://www.wizzair.com/buildnumber"

# === Telegram ===
load_dotenv(BASE_DIR / ".env")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
if not TELEGRAM_TOKEN or not CHAT_ID:
    raise RuntimeError("TELEGRAM_TOKEN или CHAT_ID не заданы в .env")

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# === HTTP Headers ===
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# === Глобальная переменная для версии API ===
API_VERSION = "27.36.0"  # Версия по умолчанию

def load_airports():
    """Загружает коды аэропортов из файла."""
    if AIRPORTS_FILE.exists():
        try:
            with AIRPORTS_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки {AIRPORTS_FILE}: {e}")
            return {}
    return {}

AIRPORT_CODES = load_airports()  # Загружаем коды аэропортов при старте

def get_city_name_with_code(airport_code: str) -> str:
    """Возвращает строку 'Название города (код аэропорта)'."""
    city_name = AIRPORT_CODES.get(airport_code, airport_code)
    if city_name == airport_code:  # Если название не найдено, возвращаем только код
        return city_name
    return f"{city_name} ({airport_code})"

def get_current_api_version():
    """Получает актуальную версию API с сайта WizzAir и обновляет глобальную переменную."""
    global API_VERSION
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
        }
        resp = requests.get(BUILD_NUMBER_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        # Извлекаем версию API из ответа (например, "SSR https://be.wizzair.com/27.36.0")
        match = re.search(r"https://be\.wizzair\.com/(\d+\.\d+\.\d+)", resp.text)
        if match:
            new_version = match.group(1)
            if new_version != API_VERSION:
                logging.info(f"🔄 Обновлена версия API: {API_VERSION} → {new_version}")
                API_VERSION = new_version
            else:
                logging.info(f"🔄 Версия API актуальна: {API_VERSION}")
        else:
            logging.error("Не удалось извлечь версию API из ответа")
    except Exception as e:
        logging.error(f"Ошибка при получении версии API: {e}")

def send_telegram(msg: str):
    """Отправляет сообщение в Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        logging.info("✅ Сообщение отправлено в Telegram")
    except Exception as e:
        logging.error(f"Ошибка Telegram: {e}")

def format_price(price: float, currency: str) -> str:
    """Форматирует цену в зависимости от валюты."""
    if currency == "RON":
        price_eur = round(price / 4.9, 2)  # 1 EUR = 4.9 RON
        return f"{price:.2f} RON (≈ {price_eur:.2f} EUR)"
    elif currency == "EUR":
        return f"{price:.2f} EUR"
    else:
        return f"{price:.2f} {currency}"

def check_route_price(origin: str, destination: str, depart_date: str, adults: int = 1):
    """Проверяет цену для одного маршрута."""
    payload = {
        "isRescueFare": False,
        "adultCount": adults,
        "childCount": 0,
        "dayInterval": 7,
        "wdc": False,
        "isFlightChange": False,
        "flightList": [{
            "departureStation": origin,
            "arrivalStation": destination,
            "date": f"{depart_date}T00:00:00"
        }],
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": random.choice(USER_AGENTS),
        "Origin": "https://wizzair.com",
        "Referer": "https://wizzair.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        with requests.Session() as session:
            resp = session.post(
                f"https://be.wizzair.com/{API_VERSION}/Api/asset/farechart",
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            logging.debug(f"Ответ API для {origin} → {destination}: {json.dumps(data, indent=2)}")

            # Ищем цену для нужной даты
            target_date = f"{depart_date}T00:00:00"
            outbound_flights = data.get("outboundFlights", [])
            for flight in outbound_flights:
                if flight.get("date") == target_date:
                    if flight.get("priceType") == "price":
                        price = flight.get("price", {}).get("amount")
                        currency = flight.get("price", {}).get("currencyCode")
                        if price and currency:
                            logging.debug(f"Найдена цена для {target_date}: {price} {currency}")
                            return price, currency
                    else:
                        logging.error(f"Для {target_date} нет доступной цены (priceType: {flight.get('priceType')})")
                        return None

            logging.error(f"Не найдена цена для {target_date}")
            return None
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP ошибка для {origin} → {destination}: {e.response.status_code} {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"Ошибка при запросе цены для {origin} → {destination}: {e}")
        return None

def load_prev_prices():
    """Загружает предыдущие цены из файла."""
    if PREV_PRICES_FILE.exists():
        try:
            with PREV_PRICES_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_prev_prices(data):
    """Сохраняет текущие цены в файл."""
    try:
        with PREV_PRICES_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка записи {PREV_PRICES_FILE}: {e}")

def get_route_id(route: dict):
    """Возвращает уникальный ID маршрута."""
    return f"{route['origin']}-{route['destination']}-{route['depart_date']}"

def main_loop():
    """Основной цикл мониторинга."""
    logging.info("🚀 Старт мониторинга маршрутов Wizzair")
    while True:
        # Обновляем версию API перед каждым циклом
        get_current_api_version()
        logging.info(f"🔄 Используется версия API: {API_VERSION}")

        if not ROUTES_FILE.exists():
            logging.error(f"Файл {ROUTES_FILE} не найден")
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
            continue

        with ROUTES_FILE.open("r", encoding="utf-8") as f:
            routes = json.load(f)

        prev_prices = load_prev_prices()
        cur_prices = prev_prices.copy()
        any_changes = False

        for idx, route in enumerate(routes, start=1):
            route_id = get_route_id(route)
            origin_city = get_city_name_with_code(route["origin"])
            destination_city = get_city_name_with_code(route["destination"])
            logging.info(f"🕑 Проверка маршрута {idx}/{len(routes)}: {origin_city} → {destination_city} ({route_id})")

            result = check_route_price(
                origin=route["origin"],
                destination=route["destination"],
                depart_date=route["depart_date"],
                adults=route.get("adults", 1)
            )

            if result is None:
                send_telegram(
                    f"⚠️ Не удалось получить цену для <b>{origin_city} → {destination_city}</b> "
                    f"на {route['depart_date']}"
                )
            else:
                price, currency = result
                old_price_data = prev_prices.get(route_id)
                cur_prices[route_id] = {"price": price, "currency": currency}

                if old_price_data is None or abs(price - old_price_data["price"]) > 0.01:
                    old_price = old_price_data["price"] if old_price_data else None
                    old_currency = old_price_data["currency"] if old_price_data else None

                    arrow = "⬆️" if old_price and price > old_price else "⬇️"
                    msg = (
                        f"{arrow} <b>{origin_city} → {destination_city}</b>\n"
                        f"Дата вылета: <b>{route['depart_date']}</b>\n"
                        f"Цена: <b>{format_price(price, currency)}</b>\n"
                        f"Старое: <b>{format_price(old_price, old_currency) if old_price else '–'}</b>"
                    )
                    send_telegram(msg)
                    any_changes = True
                else:
                    logging.info(f"🔹 Цена не изменилась для {origin_city} → {destination_city}")

            time.sleep(ROUTE_DELAY_SECONDS)

        save_prev_prices(cur_prices)

        if not any_changes:
            logging.info("✅ Изменений цен нет")

        logging.info(f"⏳ Следующая проверка через {CHECK_INTERVAL_MINUTES} минут\n")
        time.sleep(CHECK_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    main_loop()

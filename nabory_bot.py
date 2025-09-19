import requests
from bs4 import BeautifulSoup
import json
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
from urllib.parse import urljoin
from threading import Thread
from flask import Flask
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Mikroserwer Flask dla Render Health Check ---
app = Flask(__name__)

@app.route('/health')
def health_check():
    return "OK", 200

# --- Konfiguracja Selenium (headless) ---
def get_selenium_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (compatible; NaboryBot/1.0)")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

class NaboryBot:
    def __init__(self, config_path='config.json'):
        self.config = self.load_config(config_path)
        self.seen_items_file = 'seen_items.json'
        self.seen_items = self.load_seen_items()

    def load_config(self, path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_seen_items(self):
        if os.path.exists(self.seen_items_file):
            with open(self.seen_items_file, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        return set()

    def save_seen_items(self):
        with open(self.seen_items_file, 'w', encoding='utf-8') as f:
            json.dump(list(self.seen_items), f, ensure_ascii=False, indent=2)

    def fetch_page_with_selenium(self, url):
        try:
            driver = get_selenium_driver()
            driver.get(url)
            time.sleep(5)
            html = driver.page_source
            driver.quit()
            return html
        except Exception as e:
            logging.error(f"Błąd Selenium pobierania {url}: {e}")
            return None

    def fetch_page_with_requests(self, url):
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; NaboryBot/1.0)'}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logging.error(f"Błąd pobierania {url}: {e}")
            return None

    def fetch_page(self, url, use_selenium=False):
        if use_selenium:
            logging.info(f".Selenium: {url}")
            return self.fetch_page_with_selenium(url)
        else:
            logging.info(f"..Requests: {url}")
            return self.fetch_page_with_requests(url)

    def extract_items(self, html, selector, base_url):
        if not html:
            return []
        soup = BeautifulSoup(html, 'html.parser')
        elements = soup.select(selector)
        items = []
        for el in elements:
            title = el.get_text(strip=True)
            href = el.get('href')
            if href:
                full_url = urljoin(base_url, href)
                item_id = full_url
                items.append({
                    'title': title,
                    'url': full_url,
                    'id': item_id
                })
        return items

    def send_email(self, subject, body):
        if not self.config['email']['enabled']:
            return
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config['email']['sender_email']
            msg['To'] = self.config['email']['recipient_email']
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))

            server = smtplib.SMTP(self.config['email']['smtp_server'], self.config['email']['smtp_port'])
            server.starttls()
            server.login(self.config['email']['sender_email'], self.config['email']['sender_password'])
            server.send_message(msg)
            server.quit()
            logging.info("✅ E-mail wysłany pomyślnie.")
        except Exception as e:
            logging.error(f"Błąd wysyłania e-maila: {e}")

    def check_target(self, target):
        name = target['name']
        url = target['url']
        selector = target['selector']
        base_url = target['base_url']
        use_selenium = target.get('use_selenium', False)

        logging.info(f"🔍 Sprawdzam: {name} ({url})")
        html = self.fetch_page(url, use_selenium)
        if not html:
            return

        items = self.extract_items(html, selector, base_url)
        new_items = []

        for item in items:
            if item['id'] not in self.seen_items:
                self.seen_items.add(item['id'])
                new_items.append(item)

        if new_items:
            subject = f"🚨 NOWY NABÓR: {name} ({len(new_items)} nowych)"
            body = f"<h3>{subject}</h3><ul>"
            for item in new_items:
                body += f"<li><a href='{item['url']}'>{item['title']}</a></li>"
            body += "</ul>"
            self.send_email(subject, body)
            logging.info(f"🎉 Znaleziono {len(new_items)} nowych naborów w {name}")

    def run(self):
        logging.info("🚀 Bot startuje...")
        self.save_seen_items()

        while True:
            for target in self.config['targets']:
                try:
                    self.check_target(target)
                except Exception as e:
                    logging.error(f"Błąd podczas sprawdzania {target['name']}: {e}")
            self.save_seen_items()
            minutes = self.config['check_interval_minutes']
            logging.info(f"😴 Czekam {minutes} minut do kolejnego sprawdzenia...")
            time.sleep(minutes * 60)

# --- URUCHOMIENIE: Flask w głównym wątku, Bot w tle ---
def start_bot():
    bot = NaboryBot()
    bot.run()

if __name__ == "__main__":
    # Uruchom bota w tle
    bot_thread = Thread(target=start_bot)
    bot_thread.daemon = True
    bot_thread.start()

    # Uruchom Flask w głównym wątku — Render czeka na to!
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

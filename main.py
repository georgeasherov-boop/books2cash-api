from flask import Flask, request, jsonify
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import re

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# -----------------------------
# Hilfsfunktionen
# -----------------------------

def clean_isbn(isbn):
    return re.sub(r"[^0-9X]", "", isbn)

# -----------------------------
# Google Books
# -----------------------------

def fetch_google_books(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()

        if "items" not in data:
            return None

        book = data["items"][0]["volumeInfo"]

        return {
            "title": book.get("title", "Unbekannt"),
            "author": ", ".join(book.get("authors", [])),
            "source": "google"
        }

    except:
        return None

# -----------------------------
# OpenLibrary
# -----------------------------

def fetch_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/isbn/{isbn}.json"

        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()

        return {
            "title": data.get("title", "Unbekannt"),
            "author": "",
            "source": "openlibrary"
        }

    except:
        return None

# -----------------------------
# Deutsche Nationalbibliothek
# -----------------------------

def fetch_dnb(isbn):
    try:
        url = (
            "https://services.dnb.de/sru/dnb?"
            "version=1.1&operation=searchRetrieve"
            f"&query=isbn={isbn}"
            "&recordSchema=MARC21-xml"
        )

        r = requests.get(url, headers=HEADERS, timeout=15)

        if r.status_code != 200:
            return None

        root = ET.fromstring(r.text)

        ns = {
            "srw": "http://www.loc.gov/zing/srw/",
            "marc": "http://www.loc.gov/MARC21/slim"
        }

        title = None
        author = None

        for field in root.findall(".//marc:datafield", ns):

            tag = field.attrib.get("tag")

            # Titel
            if tag == "245":
                for sub in field.findall("marc:subfield", ns):
                    if sub.attrib.get("code") == "a":
                        title = sub.text

            # Autor
            if tag == "100":
                for sub in field.findall("marc:subfield", ns):
                    if sub.attrib.get("code") == "a":
                        author = sub.text

        if title:
            return {
                "title": title.strip(" /"),
                "author": author if author else "",
                "source": "dnb"
            }

        return None

    except Exception as e:
        print("DNB Fehler:", e)
        return None

# -----------------------------
# reBuy Preis
# -----------------------------

def fetch_rebuy_price(isbn):
    try:
        url = f"https://www.rebuy.de/verkaufen/suchen?query={isbn}"

        r = requests.get(url, headers=HEADERS, timeout=15)

        html = r.text

        soup = BeautifulSoup(html, "html.parser")

        text = soup.get_text(" ", strip=True)

        match = re.search(r"(\d+,\d+)\s?€", text)

        if match:
            return match.group(1) + " €"

        return None

    except Exception as e:
        print("reBuy Fehler:", e)
        return None

# -----------------------------
# API Route
# -----------------------------

@app.route("/")
def home():
    return "Books2Cash API läuft"

@app.route("/isbn/<isbn>")
def get_book(isbn):

    isbn = clean_isbn(isbn)

    result = None

    # 1. Google
    result = fetch_google_books(isbn)

    # 2. OpenLibrary
    if not result:
        result = fetch_openlibrary(isbn)

    # 3. DNB
    if not result:
        result = fetch_dnb(isbn)

    # Nichts gefunden
    if not result:
        result = {
            "title": "Nicht gefunden",
            "author": "-",
            "source": "none"
        }

    # reBuy Preis
    rebuy_price = fetch_rebuy_price(isbn)

    if rebuy_price:
        result["rebuy_price"] = rebuy_price
    else:
        result["rebuy_price"] = None

    result["isbn"] = isbn

    return jsonify(result)

# -----------------------------
# Start
# -----------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

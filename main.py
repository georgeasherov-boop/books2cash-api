from flask import Flask, jsonify
import requests
import re
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from statistics import median

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def clean_isbn(isbn):
    return re.sub(r"[^0-9Xx]", "", str(isbn)).upper()


def price_to_float(text):
    if not text:
        return None

    m = re.search(r"(\d{1,4}(?:[.,]\d{2}))", text)
    if not m:
        return None

    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def fmt(value):
    if value is None:
        return None
    return f"{value:.2f}".replace(".", ",")


def extract_prices(text):
    prices = []
    for m in re.finditer(r"(\d{1,4}[,.]\d{2})\s?€", text):
        v = price_to_float(m.group(1))
        if v is not None and 0.30 <= v <= 500:
            prices.append(v)
    return prices


def fetch_url(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def fetch_google_books(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()

        items = data.get("items", [])
        if not items:
            return None

        info = items[0].get("volumeInfo", {})
        title = info.get("title", "").strip()
        authors = info.get("authors", [])
        author = ", ".join(authors).strip() if authors else "-"

        if not title:
            return None

        return {
            "title": title,
            "author": author,
            "source": "google_books"
        }
    except Exception:
        return None


def fetch_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/isbn/{isbn}.json"
        r = requests.get(url, headers=HEADERS, timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()
        title = data.get("title", "").strip()

        if not title:
            return None

        author = "-"
        author_names = []

        for a in data.get("authors", []):
            key = a.get("key")
            if key:
                try:
                    ar = requests.get(f"https://openlibrary.org{key}.json", headers=HEADERS, timeout=6)
                    if ar.status_code == 200:
                        name = ar.json().get("name", "").strip()
                        if name:
                            author_names.append(name)
                except Exception:
                    pass

        if author_names:
            author = ", ".join(author_names)

        return {
            "title": title,
            "author": author,
            "source": "openlibrary"
        }
    except Exception:
        return None


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
            "marc": "http://www.loc.gov/MARC21/slim"
        }

        title = None
        author = None

        for field in root.findall(".//marc:datafield", ns):
            tag = field.attrib.get("tag")

            if tag == "245":
                parts = []
                for sub in field.findall("marc:subfield", ns):
                    if sub.attrib.get("code") in ["a", "b"]:
                        if sub.text:
                            parts.append(sub.text.strip())
                if parts:
                    title = " ".join(parts).strip(" /:")

            if tag == "100":
                for sub in field.findall("marc:subfield", ns):
                    if sub.attrib.get("code") == "a" and sub.text:
                        author = sub.text.strip(" ,")

        if not title:
            return None

        return {
            "title": title,
            "author": author or "-",
            "source": "dnb"
        }
    except Exception:
        return None


def get_book_info(isbn):
    for fn in [fetch_google_books, fetch_openlibrary, fetch_dnb]:
        result = fn(isbn)
        if result:
            return result

    return {
        "title": "Nicht gefunden",
        "author": "-",
        "source": "none"
    }


def fetch_rebuy_buy(isbn):
    html = fetch_url(f"https://www.rebuy.de/verkaufen/suchen?query={isbn}")
    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    if not prices:
        return None

    return min(prices)


def fetch_momox_buy(isbn):
    html = fetch_url(f"https://www.momox.de/offer/{isbn}")
    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    if not prices:
        return None

    return min(prices)


def fetch_zoxs_buy(isbn):
    html = fetch_url(f"https://www.zoxs.de/ankauf/search?search={isbn}")
    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    if not prices:
        return None

    return min(prices)


def fetch_medimops_sell(isbn, title):
    html = fetch_url(f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={isbn}")
    if not html and title:
        html = fetch_url(f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={requests.utils.quote(title)}")

    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    if not prices:
        return None

    return min(prices)


def fetch_ebay_sell(isbn, title):
    query = isbn if isbn else title
    if not query:
        return None

    url = f"https://www.ebay.de/sch/i.html?_nkw={requests.utils.quote(query)}&_sacat=267&LH_BIN=1"
    html = fetch_url(url)

    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    prices = extract_prices(text)

    cleaned = []
    for p in prices:
        if 1 <= p <= 300:
            cleaned.append(p)

    if not cleaned:
        return None

    return median(cleaned[:10])


def fetch_amazon_sell(isbn, title):
    query = isbn if isbn else title
    if not query:
        return None

    url = f"https://www.amazon.de/s?k={requests.utils.quote(query)}&i=stripbooks"
    html = fetch_url(url)

    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    prices = extract_prices(text)

    cleaned = []
    for p in prices:
        if 1 <= p <= 500:
            cleaned.append(p)

    if not cleaned:
        return None

    return min(cleaned[:10])


def fetch_booklooker_sell(isbn, title):
    query = isbn if isbn else title
    if not query:
        return None

    url = f"https://www.booklooker.de/B%C3%BCcher/Angebote/isbn={requests.utils.quote(isbn)}"
    html = fetch_url(url)

    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    cleaned = []
    for p in prices:
        if 1 <= p <= 300:
            cleaned.append(p)

    if not cleaned:
        return None

    return min(cleaned)


def fetch_willhaben_sell(isbn, title):
    query = isbn if isbn else title
    if not query:
        return None

    url = f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={requests.utils.quote(query)}"
    html = fetch_url(url)

    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    cleaned = []
    for p in prices:
        if 1 <= p <= 500:
            cleaned.append(p)

    if not cleaned:
        return None

    return median(cleaned[:10])


def fetch_vinted_sell(isbn, title):
    query = isbn if isbn else title
    if not query:
        return None

    url = f"https://www.vinted.at/catalog?search_text={requests.utils.quote(query)}"
    html = fetch_url(url)

    if not html:
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    prices = extract_prices(text)

    cleaned = []
    for p in prices:
        if 1 <= p <= 300:
            cleaned.append(p)

    if not cleaned:
        return None

    return median(cleaned[:10])


@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "version": "prices_v1",
        "sources": [
            "google_books",
            "openlibrary",
            "dnb",
            "rebuy",
            "momox",
            "zoxs",
            "medimops",
            "ebay",
            "amazon",
            "booklooker",
            "willhaben",
            "vinted"
        ]
    })


@app.route("/isbn/<isbn>")
def lookup(isbn):
    isbn = clean_isbn(isbn)

    info = get_book_info(isbn)
    title = info.get("title", "")
    author = info.get("author", "")

    momox_buy = fetch_momox_buy(isbn)
    rebuy_buy = fetch_rebuy_buy(isbn)
    zoxs_buy = fetch_zoxs_buy(isbn)

    medimops_sell = fetch_medimops_sell(isbn, title)
    ebay_sell = fetch_ebay_sell(isbn, title)
    amazon_sell = fetch_amazon_sell(isbn, title)
    booklooker_sell = fetch_booklooker_sell(isbn, title)
    willhaben_sell = fetch_willhaben_sell(isbn, title)
    vinted_sell = fetch_vinted_sell(isbn, title)

    ankauf_values = [v for v in [momox_buy, rebuy_buy, zoxs_buy] if v is not None]
    verkauf_values = [v for v in [medimops_sell, ebay_sell, amazon_sell, booklooker_sell, willhaben_sell, vinted_sell] if v is not None]

    best_buy = max(ankauf_values) if ankauf_values else None
    best_sell = max(verkauf_values) if verkauf_values else None
    avg_sell = sum(verkauf_values) / len(verkauf_values) if verkauf_values else None

    return jsonify({
        "ok": True,
        "isbn": isbn,
        "title": title,
        "author": author,
        "source": info.get("source", "none"),

        "ankauf": {
            "momox": fmt(momox_buy),
            "rebuy": fmt(rebuy_buy),
            "zoxs": fmt(zoxs_buy),
            "1000books": None,
            "buchmaxe": None
        },

        "verkauf": {
            "momox": None,
            "rebuy": None,
            "medimops": fmt(medimops_sell),
            "amazon": fmt(amazon_sell),
            "ebay": fmt(ebay_sell),
            "booklooker": fmt(booklooker_sell),
            "willhaben": fmt(willhaben_sell),
            "vinted": fmt(vinted_sell)
        },

        "analyse": {
            "best_buy": fmt(best_buy),
            "best_sell": fmt(best_sell),
            "avg_sell": fmt(avg_sell)
        },

        "rebuy_price": fmt(rebuy_buy),
        "blocked": False,
        "error": None
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

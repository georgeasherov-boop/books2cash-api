from flask import Flask, jsonify
import requests
import re
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from statistics import median
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

TIMEOUT = 8


def clean_isbn(value):
    return re.sub(r"[^0-9Xx]", "", str(value)).upper()


def normalize_price_text(value):
    if value is None:
        return None

    value = str(value)
    value = value.replace("\xa0", " ")
    value = value.replace("EUR", "€")
    value = value.replace("Euro", "€")
    value = value.strip()

    if not value or value.lower() in ["none", "null", "-", "nan"]:
        return None

    match = re.search(r"(\d{1,4}(?:[.,]\d{1,2}))", value)
    if not match:
        return None

    raw = match.group(1).replace(",", ".")

    try:
        number = float(raw)
    except Exception:
        return None

    if number <= 0 or number > 2000:
        return None

    return number


def fmt(value):
    number = normalize_price_text(value)
    if number is None:
        return None

    return f"{number:.2f}".replace(".", ",")


def fetch(url, timeout=TIMEOUT):
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )

        if r.status_code != 200:
            return {
                "ok": False,
                "status": r.status_code,
                "url": url,
                "text": "",
            }

        return {
            "ok": True,
            "status": r.status_code,
            "url": url,
            "text": r.text,
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "exception",
            "url": url,
            "text": "",
            "error": str(e),
        }


def html_text(html):
    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        return soup.get_text(" ", strip=True)

    except Exception:
        return ""


def extract_all_prices(text):
    if not text:
        return []

    text = text.replace("\xa0", " ")

    patterns = [
        r"(\d{1,4}[,.]\d{2})\s*€",
        r"€\s*(\d{1,4}[,.]\d{2})",
        r"(\d{1,4}[,.]\d{2})\s*EUR",
    ]

    prices = []

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            number = normalize_price_text(match.group(1))
            if number is not None and 0.10 <= number <= 1000:
                prices.append(number)

    return prices


def extract_price_near_keywords(text, keywords):
    if not text:
        return None

    clean = text.replace("\xa0", " ")

    candidates = []

    for keyword in keywords:
        pos = clean.lower().find(keyword.lower())

        if pos >= 0:
            start = max(0, pos - 300)
            end = min(len(clean), pos + 700)
            window = clean[start:end]

            prices = extract_all_prices(window)
            candidates.extend(prices)

    if not candidates:
        return None

    return min(candidates)


def safe_min(prices):
    prices = [p for p in prices if p is not None and p > 0]

    if not prices:
        return None

    return min(prices)


def safe_median(prices):
    prices = [p for p in prices if p is not None and p > 0]

    if not prices:
        return None

    return median(prices)


def safe_max(prices):
    prices = [p for p in prices if p is not None and p > 0]

    if not prices:
        return None

    return max(prices)


# -------------------------------------------------
# Buchdaten
# -------------------------------------------------

def fetch_google_books(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
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
            "source": "google_books",
        }

    except Exception:
        return None


def fetch_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/isbn/{isbn}.json"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)

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
            if not key:
                continue

            try:
                ar = requests.get(
                    f"https://openlibrary.org{key}.json",
                    headers=HEADERS,
                    timeout=5,
                )

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
            "source": "openlibrary",
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

        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)

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
                    code = sub.attrib.get("code")
                    if code in ["a", "b"] and sub.text:
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
            "source": "dnb",
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
        "source": "none",
    }


# -------------------------------------------------
# Ankaufspreise
# -------------------------------------------------

def buy_momox(isbn, title):
    urls = [
        f"https://www.momox.de/offer/{isbn}",
        f"https://www.momox.de/verkaufen/?search={quote(isbn)}",
        f"https://www.momox.de/verkaufen/buecher/?search={quote(isbn)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        price = extract_price_near_keywords(
            text,
            [
                "ankaufspreis",
                "angebot",
                "verkaufspreis",
                "wir zahlen",
                "direkt verkaufen",
                "preis",
            ],
        )

        if price is not None:
            all_prices.append(price)

        all_prices.extend(extract_all_prices(text)[:5])

    return safe_min(all_prices)


def buy_rebuy(isbn, title):
    urls = [
        f"https://www.rebuy.de/verkaufen/suchen?query={quote(isbn)}",
        f"https://www.rebuy.de/verkaufen/suchen?q={quote(isbn)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        price = extract_price_near_keywords(
            text,
            [
                "unser angebot",
                "angebot",
                "ankaufspreis",
                "verkaufswert",
                "sofort angebotspreis",
                "preisinfo",
            ],
        )

        if price is not None:
            all_prices.append(price)

        prices = extract_all_prices(text)

        filtered = [
            p for p in prices
            if 0.10 <= p <= 100
        ]

        all_prices.extend(filtered[:5])

    return safe_min(all_prices)


def buy_zoxs(isbn, title):
    urls = [
        f"https://www.zoxs.de/ankauf/search?search={quote(isbn)}",
        f"https://www.zoxs.de/sell/search?search={quote(isbn)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        price = extract_price_near_keywords(
            text,
            [
                "ankauf",
                "ankaufspreis",
                "wir zahlen",
                "verkaufen",
                "angebot",
            ],
        )

        if price is not None:
            all_prices.append(price)

        all_prices.extend(extract_all_prices(text)[:5])

    return safe_min(all_prices)


def buy_buchmaxe(isbn, title):
    urls = [
        f"https://www.buchmaxe.de/ankauf/search?search={quote(isbn)}",
        f"https://www.buchmaxe.de/suche?q={quote(isbn)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        price = extract_price_near_keywords(
            text,
            [
                "ankaufspreis",
                "ankauf",
                "angebot",
                "verkaufen",
            ],
        )

        if price is not None:
            all_prices.append(price)

        all_prices.extend(extract_all_prices(text)[:5])

    return safe_min(all_prices)


def buy_1000books(isbn, title):
    urls = [
        f"https://www.1000books.de/?s={quote(isbn)}",
        f"https://www.1000books.de/search?q={quote(isbn)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        price = extract_price_near_keywords(
            text,
            [
                "ankauf",
                "ankaufspreis",
                "angebot",
                "verkaufen",
            ],
        )

        if price is not None:
            all_prices.append(price)

        all_prices.extend(extract_all_prices(text)[:5])

    return safe_min(all_prices)


# -------------------------------------------------
# Verkaufspreise
# -------------------------------------------------

def sell_medimops(isbn, title):
    urls = [
        f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={quote(isbn)}",
    ]

    if title and title != "Nicht gefunden":
        urls.append(
            f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={quote(title)}"
        )

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 500]
        all_prices.extend(filtered[:10])

    return safe_min(all_prices)


def sell_rebuy(isbn, title):
    query = isbn or title

    urls = [
        f"https://www.rebuy.de/kaufen/suchen?q={quote(query)}",
        f"https://www.rebuy.de/kaufen/suchen?query={quote(query)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 500]
        all_prices.extend(filtered[:10])

    return safe_min(all_prices)


def sell_momox(isbn, title):
    # momox verkauft meist über medimops; deshalb wird hier nichts erfunden.
    return None


def sell_zoxs(isbn, title):
    query = isbn or title

    urls = [
        f"https://www.zoxs.de/kaufen/search?search={quote(query)}",
        f"https://www.zoxs.de/search?search={quote(query)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 500]
        all_prices.extend(filtered[:10])

    return safe_min(all_prices)


def sell_buchmaxe(isbn, title):
    return None


def sell_1000books(isbn, title):
    return None


def sell_amazon(isbn, title):
    query = isbn or title

    urls = [
        f"https://www.amazon.de/s?k={quote(query)}&i=stripbooks",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 1000]
        all_prices.extend(filtered[:10])

    return safe_min(all_prices)


def sell_ebay(isbn, title):
    query = isbn or title

    urls = [
        f"https://www.ebay.de/sch/i.html?_nkw={quote(query)}&_sacat=267&LH_BIN=1",
        f"https://www.ebay.de/sch/i.html?_nkw={quote(query)}&_sacat=267&LH_Sold=1&LH_Complete=1",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 1000]
        all_prices.extend(filtered[:15])

    return safe_median(all_prices[:20])


def sell_booklooker(isbn, title):
    urls = [
        f"https://www.booklooker.de/B%C3%BCcher/Angebote/isbn={quote(isbn)}",
    ]

    if title and title != "Nicht gefunden":
        urls.append(
            f"https://www.booklooker.de/B%C3%BCcher/Angebote/titel={quote(title)}"
        )

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 500]
        all_prices.extend(filtered[:15])

    return safe_min(all_prices)


def sell_willhaben(isbn, title):
    query = isbn or title

    urls = [
        f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={quote(query)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 1000]
        all_prices.extend(filtered[:15])

    return safe_median(all_prices[:15])


def sell_vinted(isbn, title):
    query = isbn or title

    urls = [
        f"https://www.vinted.at/catalog?search_text={quote(query)}",
        f"https://www.vinted.de/catalog?search_text={quote(query)}",
    ]

    all_prices = []

    for url in urls:
        result = fetch(url)
        text = html_text(result["text"])

        prices = extract_all_prices(text)
        filtered = [p for p in prices if 0.50 <= p <= 500]
        all_prices.extend(filtered[:15])

    return safe_median(all_prices[:15])


# -------------------------------------------------
# API
# -------------------------------------------------

@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "version": "prices_v2_full",
        "hint": "Nutze /isbn/<isbn>",
    })


@app.route("/isbn/<isbn>")
def lookup(isbn):
    isbn = clean_isbn(isbn)
    info = get_book_info(isbn)

    title = info.get("title", "")
    author = info.get("author", "")

    jobs = {
        "buy_momox": lambda: buy_momox(isbn, title),
        "buy_rebuy": lambda: buy_rebuy(isbn, title),
        "buy_zoxs": lambda: buy_zoxs(isbn, title),
        "buy_1000books": lambda: buy_1000books(isbn, title),
        "buy_buchmaxe": lambda: buy_buchmaxe(isbn, title),

        "sell_momox": lambda: sell_momox(isbn, title),
        "sell_rebuy": lambda: sell_rebuy(isbn, title),
        "sell_zoxs": lambda: sell_zoxs(isbn, title),
        "sell_1000books": lambda: sell_1000books(isbn, title),
        "sell_buchmaxe": lambda: sell_buchmaxe(isbn, title),
        "sell_medimops": lambda: sell_medimops(isbn, title),
        "sell_amazon": lambda: sell_amazon(isbn, title),
        "sell_ebay": lambda: sell_ebay(isbn, title),
        "sell_booklooker": lambda: sell_booklooker(isbn, title),
        "sell_willhaben": lambda: sell_willhaben(isbn, title),
        "sell_vinted": lambda: sell_vinted(isbn, title),
    }

    results = {}
    debug = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {
            executor.submit(fn): name
            for name, fn in jobs.items()
        }

        for future in as_completed(future_map):
            name = future_map[future]

            try:
                value = future.result(timeout=12)
                results[name] = value
                debug[name] = "ok" if value is not None else "no_price"
            except Exception as e:
                results[name] = None
                debug[name] = f"error: {str(e)}"

    buy_values = [
        results.get("buy_momox"),
        results.get("buy_rebuy"),
        results.get("buy_zoxs"),
        results.get("buy_1000books"),
        results.get("buy_buchmaxe"),
    ]

    sell_values = [
        results.get("sell_momox"),
        results.get("sell_rebuy"),
        results.get("sell_zoxs"),
        results.get("sell_1000books"),
        results.get("sell_buchmaxe"),
        results.get("sell_medimops"),
        results.get("sell_amazon"),
        results.get("sell_ebay"),
        results.get("sell_booklooker"),
        results.get("sell_willhaben"),
        results.get("sell_vinted"),
    ]

    buy_values_clean = [
        v for v in buy_values
        if v is not None
    ]

    sell_values_clean = [
        v for v in sell_values
        if v is not None
    ]

    best_buy = safe_max(buy_values_clean)
    best_sell = safe_max(sell_values_clean)
    avg_sell = (
        sum(sell_values_clean) / len(sell_values_clean)
        if sell_values_clean
        else None
    )

    return jsonify({
        "ok": True,
        "isbn": isbn,
        "title": title,
        "author": author,
        "source": info.get("source", "none"),

        "ankauf": {
            "momox": fmt(results.get("buy_momox")),
            "rebuy": fmt(results.get("buy_rebuy")),
            "zoxs": fmt(results.get("buy_zoxs")),
            "1000books": fmt(results.get("buy_1000books")),
            "buchmaxe": fmt(results.get("buy_buchmaxe")),
        },

        "verkauf": {
            "momox": fmt(results.get("sell_momox")),
            "rebuy": fmt(results.get("sell_rebuy")),
            "zoxs": fmt(results.get("sell_zoxs")),
            "1000books": fmt(results.get("sell_1000books")),
            "buchmaxe": fmt(results.get("sell_buchmaxe")),
            "medimops": fmt(results.get("sell_medimops")),
            "amazon": fmt(results.get("sell_amazon")),
            "ebay": fmt(results.get("sell_ebay")),
            "booklooker": fmt(results.get("sell_booklooker")),
            "willhaben": fmt(results.get("sell_willhaben")),
            "vinted": fmt(results.get("sell_vinted")),
        },

        "analyse": {
            "best_buy": fmt(best_buy),
            "best_sell": fmt(best_sell),
            "avg_sell": fmt(avg_sell),
        },

        "debug": debug,
        "error": None,
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080
    )

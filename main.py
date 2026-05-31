from flask import Flask, jsonify
import requests
import re
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from statistics import median
from urllib.parse import quote

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

TIMEOUT = 6


def clean_isbn(value):
    return re.sub(r"[^0-9Xx]", "", str(value)).upper()


def normalize_price(value):
    if value is None:
        return None

    text = str(value)
    text = text.replace("\xa0", " ")
    text = text.replace("EUR", "€")
    text = text.replace("Euro", "€")
    text = text.strip()

    if not text or text.lower() in ["none", "null", "-", "nan"]:
        return None

    match = re.search(r"(\d{1,5}(?:[.,]\d{1,2}))", text)

    if not match:
        return None

    raw = match.group(1).replace(",", ".")

    try:
        number = float(raw)
    except Exception:
        return None

    if number <= 0 or number > 3000:
        return None

    return number


def fmt(value):
    number = normalize_price(value)

    if number is None:
        return None

    return f"{number:.2f}".replace(".", ",")


def fetch(url):
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        return {
            "ok": r.status_code == 200,
            "status": r.status_code,
            "url": url,
            "text": r.text if r.text else "",
        }

    except Exception as e:
        return {
            "ok": False,
            "status": "exception",
            "url": url,
            "text": "",
            "error": str(e),
        }


def html_to_text(html):
    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        return soup.get_text(" ", strip=True)

    except Exception:
        return ""


def page_contains_isbn(text, isbn):
    if not text:
        return False

    compact_text = re.sub(r"[^0-9Xx]", "", text).upper()
    compact_isbn = clean_isbn(isbn)

    return compact_isbn in compact_text


def extract_prices(text):
    if not text:
        return []

    text = text.replace("\xa0", " ")

    patterns = [
        r"(\d{1,5}[,.]\d{2})\s*€",
        r"€\s*(\d{1,5}[,.]\d{2})",
        r"(\d{1,5}[,.]\d{2})\s*EUR",
    ]

    prices = []

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            number = normalize_price(match.group(1))

            if number is not None:
                prices.append(number)

    return prices


def filter_prices(prices, min_price=0.50, max_price=1000):
    clean = []

    for p in prices:
        n = normalize_price(p)

        if n is not None and min_price <= n <= max_price:
            clean.append(n)

    return clean


def safe_min(values):
    values = filter_prices(values)

    if not values:
        return None

    return min(values)


def safe_median(values):
    values = filter_prices(values)

    if not values:
        return None

    return median(values)


def get_strict_prices_from_url(url, isbn, min_price=0.50, max_price=1000):
    result = fetch(url)

    if not result["ok"]:
        return [], {
            "status": result["status"],
            "reason": "http_error",
        }

    html = result["text"]
    text = html_to_text(html)
    merged = html + " " + text

    if not page_contains_isbn(merged, isbn):
        return [], {
            "status": result["status"],
            "reason": "isbn_not_found_on_page",
        }

    prices = extract_prices(merged)
    prices = filter_prices(prices, min_price, max_price)

    if not prices:
        return [], {
            "status": result["status"],
            "reason": "no_price_found",
        }

    return prices, {
        "status": result["status"],
        "reason": "ok",
    }


# -------------------------------------------------
# Buchdaten
# -------------------------------------------------

def fetch_google_books(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, headers=HEADERS, timeout=5)
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
        r = requests.get(url, headers=HEADERS, timeout=5)

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
                    timeout=3,
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

        r = requests.get(url, headers=HEADERS, timeout=5)

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

def buy_momox(isbn):
    urls = [
        f"https://www.momox.de/offer/{isbn}",
        f"https://www.momox.de/verkaufen/?search={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.01,
            max_price=300,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def buy_rebuy(isbn):
    urls = [
        f"https://www.rebuy.de/verkaufen/suchen?query={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.01,
            max_price=300,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def buy_zoxs(isbn):
    urls = [
        f"https://www.zoxs.de/ankauf/search?search={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.01,
            max_price=300,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


# -------------------------------------------------
# Verkaufspreise strict
# -------------------------------------------------

def sell_booklooker(isbn):
    urls = [
        f"https://www.booklooker.de/B%C3%BCcher/Angebote/isbn={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=1000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_medimops(isbn):
    urls = [
        f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=1000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_rebuy(isbn):
    urls = [
        f"https://www.rebuy.de/kaufen/suchen?q={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=1000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_zoxs(isbn):
    urls = [
        f"https://www.zoxs.de/kaufen/search?search={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=1000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_amazon(isbn):
    urls = [
        f"https://www.amazon.de/s?k={quote(isbn)}&i=stripbooks",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=2000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_ebay_active(isbn):
    urls = [
        f"https://www.ebay.de/sch/i.html?_nkw={quote(isbn)}&_sacat=267&LH_BIN=1",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=2000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


def sell_ebay_sold(isbn):
    urls = [
        f"https://www.ebay.de/sch/i.html?_nkw={quote(isbn)}&_sacat=267&LH_Sold=1&LH_Complete=1",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=2000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


def sell_willhaben(isbn):
    urls = [
        f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=2000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


def sell_vinted(isbn):
    urls = [
        f"https://www.vinted.at/catalog?search_text={quote(isbn)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(
            url,
            isbn,
            min_price=0.50,
            max_price=1000,
        )
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


# -------------------------------------------------
# API
# -------------------------------------------------

@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "version": "prices_v5_strict_isbn_only",
        "hint": "Nutze /isbn/<isbn>",
        "note": "Preise werden nur übernommen, wenn die exakte ISBN auf der Quellseite vorkommt.",
    })


@app.route("/isbn/<isbn>")
def lookup(isbn):
    isbn = clean_isbn(isbn)
    info = get_book_info(isbn)

    jobs = {
        "buy_momox": lambda: buy_momox(isbn),
        "buy_rebuy": lambda: buy_rebuy(isbn),
        "buy_zoxs": lambda: buy_zoxs(isbn),

        "sell_medimops": lambda: sell_medimops(isbn),
        "sell_rebuy": lambda: sell_rebuy(isbn),
        "sell_zoxs": lambda: sell_zoxs(isbn),
        "sell_amazon": lambda: sell_amazon(isbn),
        "sell_ebay": lambda: sell_ebay_active(isbn),
        "sell_ebay_sold": lambda: sell_ebay_sold(isbn),
        "sell_booklooker": lambda: sell_booklooker(isbn),
        "sell_willhaben": lambda: sell_willhaben(isbn),
        "sell_vinted": lambda: sell_vinted(isbn),
    }

    results = {}
    debug = {}

    for name, fn in jobs.items():
        try:
            value, trace = fn()
            results[name] = value
            debug[name] = {
                "status": "ok" if value is not None else "no_reliable_price",
                "trace": trace,
            }
        except Exception as e:
            results[name] = None
            debug[name] = {
                "status": "error",
                "error": str(e),
            }

    buy_values = [
        results.get("buy_momox"),
        results.get("buy_rebuy"),
        results.get("buy_zoxs"),
    ]

    sell_values = [
        results.get("sell_medimops"),
        results.get("sell_rebuy"),
        results.get("sell_zoxs"),
        results.get("sell_amazon"),
        results.get("sell_ebay"),
        results.get("sell_ebay_sold"),
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

    best_buy = max(buy_values_clean) if buy_values_clean else None
    best_sell = max(sell_values_clean) if sell_values_clean else None

    avg_sell = (
        sum(sell_values_clean) / len(sell_values_clean)
        if sell_values_clean
        else None
    )

    return jsonify({
        "ok": True,
        "isbn": isbn,
        "title": info.get("title", ""),
        "author": info.get("author", ""),
        "source": info.get("source", "none"),

        "ankauf": {
            "momox": fmt(results.get("buy_momox")),
            "rebuy": fmt(results.get("buy_rebuy")),
            "zoxs": fmt(results.get("buy_zoxs")),
            "1000books": None,
            "buchmaxe": None,
        },

        "verkauf": {
            "momox": None,
            "rebuy": fmt(results.get("sell_rebuy")),
            "zoxs": fmt(results.get("sell_zoxs")),
            "1000books": None,
            "buchmaxe": None,
            "medimops": fmt(results.get("sell_medimops")),
            "amazon": fmt(results.get("sell_amazon")),
            "amazon_new": None,
            "ebay": fmt(results.get("sell_ebay")),
            "ebay_sold": fmt(results.get("sell_ebay_sold")),
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

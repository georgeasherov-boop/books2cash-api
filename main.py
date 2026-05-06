from flask import Flask, jsonify
import requests
from statistics import median

app = Flask(__name__)

BOOK_CATEGORY_UUID = "64f6cfe0-3f10-4abd-8ba2-8c020da0e7d1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 Books2Cash/1.0",
    "Accept": "application/json,text/html,*/*",
}


def clean_isbn(isbn):
    return "".join(ch for ch in str(isbn) if ch.isdigit() or ch.upper() == "X").upper()


def safe_get_json(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def normalize_partner(name):
    n = name.lower()
    if "momox" in n:
        return "momox"
    if "rebuy" in n:
        return "rebuy"
    if "zoxs" in n:
        return "zoxs"
    if "buchmaxe" in n:
        return "buchmaxe"
    if "1000books" in n:
        return "1000books"
    if "medimops" in n:
        return "medimops"
    return name


def get_google_books_info(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        data = safe_get_json(url, timeout=10)

        if "items" not in data or not data["items"]:
            return {"title": None, "author": None}

        info = data["items"][0].get("volumeInfo", {})
        title = info.get("title")
        authors = info.get("authors", [])
        author = ", ".join(authors) if authors else None

        return {"title": title, "author": author}
    except Exception:
        return {"title": None, "author": None}


def get_bonavendi_data(isbn):
    search_url = (
        "https://api.bonavendi.at/rest/v2/products/sell"
        f"?q={isbn}&productCategoryFilterUuids={BOOK_CATEGORY_UUID}"
    )

    search = safe_get_json(search_url)

    if not search.get("payload"):
        return {
            "title": None,
            "author": None,
            "ankauf": {},
            "bonavendi": {},
            "raw": [],
        }

    external_id = search["payload"][0].get("externalId", isbn)

    product_url = f"https://api.bonavendi.at/rest/v2/products/{external_id}"
    product = safe_get_json(product_url)

    payload = product.get("payload", {})
    product_uuid = payload.get("uuid")
    title = payload.get("name")
    description = payload.get("description") or ""

    author = None
    if "Buch von " in description:
        try:
            author = description.split("Buch von ", 1)[1].split("\n", 1)[0].strip()
        except Exception:
            author = None

    if not product_uuid:
        return {
            "title": title,
            "author": author,
            "ankauf": {},
            "bonavendi": {},
            "raw": [],
        }

    offers_url = (
        f"https://api.bonavendi.at/rest/v2/products/{product_uuid}/buyOffers"
        "?maxAgeOfOfferInMinutes=0"
    )

    offers = safe_get_json(offers_url)
    offer_list = offers.get("payload", [])

    ankauf = {}
    bonavendi = {}
    raw = []

    for offer in offer_list:
        if not offer.get("querySuccess"):
            continue

        price = offer.get("price")
        if not price or price <= 0:
            continue

        partner = offer.get("partner", {})
        partner_name = partner.get("name", "Unbekannt")
        key = normalize_partner(partner_name)

        price = round(float(price), 2)

        raw.append({
            "partner": partner_name,
            "key": key,
            "price": price
        })

        if key not in ankauf or price > ankauf[key]:
            ankauf[key] = price

        if key not in bonavendi or price > bonavendi[key]:
            bonavendi[key] = price

    return {
        "title": title,
        "author": author,
        "ankauf": ankauf,
        "bonavendi": bonavendi,
        "raw": raw,
    }


def calc_stats(values):
    values = [float(v) for v in values if v is not None and float(v) > 0]
    if not values:
        return {
            "best": None,
            "average": None,
            "median": None
        }

    return {
        "best": round(max(values), 2),
        "average": round(sum(values) / len(values), 2),
        "median": round(median(values), 2)
    }


@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "mode": "real_bonavendi"
    })


@app.route("/isbn/<isbn>")
def lookup(isbn):
    isbn = clean_isbn(isbn)

    google = get_google_books_info(isbn)
    bonavendi = get_bonavendi_data(isbn)

    title = bonavendi.get("title") or google.get("title") or "Nicht gefunden"
    author = bonavendi.get("author") or google.get("author") or "-"

    ankauf = bonavendi.get("ankauf", {})
    bonavendi_compare = bonavendi.get("bonavendi", {})

    ankauf_stats = calc_stats(ankauf.values())

    # Verkaufspreise sind vorerst bewusst leer, damit keine falschen Demo-Werte angezeigt werden.
    verkauf = {
        "medimops": None,
        "zvab": None,
        "booklooker": None,
        "amazon": None,
        "ebay": None
    }

    return jsonify({
        "isbn": isbn,
        "title": title,
        "author": author,
        "ankauf": ankauf,
        "bonavendi": bonavendi_compare,
        "bonavendi_raw": bonavendi.get("raw", []),
        "ankauf_stats": ankauf_stats,
        "verkauf": verkauf,
        "hinweis": "Ankaufspreise kommen live über Bonavendi. Verkaufspreise sind aktuell deaktiviert, damit keine Demo-Werte angezeigt werden."
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

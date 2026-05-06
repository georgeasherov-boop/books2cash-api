from flask import Flask, jsonify
import requests

app = Flask(__name__)

BOOK_CATEGORY_UUID = "64f6cfe0-3f10-4abd-8ba2-8c020da0e7d1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def clean_isbn(isbn):
    return "".join(
        ch for ch in str(isbn)
        if ch.isdigit() or ch.upper() == "X"
    ).upper()


def get_google_books_info(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, timeout=10)
        data = r.json()

        items = data.get("items", [])
        if not items:
            return {"title": "Nicht gefunden", "author": "-"}

        info = items[0].get("volumeInfo", {})
        title = info.get("title", "Nicht gefunden")
        authors = info.get("authors", [])
        author = ", ".join(authors) if authors else "-"

        return {"title": title, "author": author}

    except Exception as e:
        return {"title": "Nicht gefunden", "author": "-", "error": str(e)}


def try_bonavendi(isbn):
    url = (
        "https://api.bonavendi.at/rest/v2/products/sell"
        f"?q={isbn}&productCategoryFilterUuids={BOOK_CATEGORY_UUID}"
    )

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)

        if r.status_code == 403:
            return {
                "ok": False,
                "blocked": True,
                "error": "Bonavendi blockiert Railway mit HTTP 403.",
                "ankauf": {},
                "bonavendi": {},
                "raw": []
            }

        if r.status_code < 200 or r.status_code >= 300:
            return {
                "ok": False,
                "blocked": False,
                "error": f"Bonavendi HTTP {r.status_code}: {r.text[:300]}",
                "ankauf": {},
                "bonavendi": {},
                "raw": []
            }

        search = r.json()
        payload = search.get("payload", [])

        if not payload:
            return {
                "ok": True,
                "blocked": False,
                "error": None,
                "ankauf": {},
                "bonavendi": {},
                "raw": []
            }

        external_id = payload[0].get("externalId", isbn)

        product_url = f"https://api.bonavendi.at/rest/v2/products/{external_id}"
        product_r = requests.get(product_url, headers=HEADERS, timeout=15)

        if product_r.status_code != 200:
            return {
                "ok": False,
                "blocked": False,
                "error": f"Bonavendi Produkt HTTP {product_r.status_code}",
                "ankauf": {},
                "bonavendi": {},
                "raw": []
            }

        product = product_r.json()
        product_payload = product.get("payload", {})
        product_uuid = product_payload.get("uuid")

        if not product_uuid:
            return {
                "ok": False,
                "blocked": False,
                "error": "Bonavendi Produkt-UUID fehlt.",
                "ankauf": {},
                "bonavendi": {},
                "raw": []
            }

        offers_url = (
            f"https://api.bonavendi.at/rest/v2/products/{product_uuid}/buyOffers"
            "?maxAgeOfOfferInMinutes=0"
        )

        offers_r = requests.get(offers_url, headers=HEADERS, timeout=30)

        if offers_r.status_code != 200:
            return {
                "ok": False,
                "blocked": False,
                "error": f"Bonavendi Offers HTTP {offers_r.status_code}",
                "ankauf": {},
                "bonavendi": {},
                "raw": []
            }

        offers = offers_r.json().get("payload", [])

        ankauf = {}
        raw = []

        for offer in offers:
            if not offer.get("querySuccess"):
                continue

            price = offer.get("price")
            if not price or price <= 0:
                continue

            partner = offer.get("partner", {})
            name = partner.get("name", "unknown")
            key = name.lower()

            if "momox" in key:
                key = "momox"
            elif "rebuy" in key:
                key = "rebuy"
            elif "zoxs" in key:
                key = "zoxs"
            elif "buchmaxe" in key:
                key = "buchmaxe"
            elif "1000books" in key:
                key = "1000books"
            elif "medimops" in key:
                key = "medimops"
            else:
                key = name

            price = round(float(price), 2)

            raw.append({
                "partner": name,
                "key": key,
                "price": price
            })

            if key not in ankauf or price > ankauf[key]:
                ankauf[key] = price

        return {
            "ok": True,
            "blocked": False,
            "error": None,
            "ankauf": ankauf,
            "bonavendi": ankauf,
            "raw": raw
        }

    except Exception as e:
        return {
            "ok": False,
            "blocked": False,
            "error": str(e),
            "ankauf": {},
            "bonavendi": {},
            "raw": []
        }


@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "mode": "safe_no_crash",
        "note": "Wenn Bonavendi Railway blockiert, liefert die API trotzdem JSON statt HTTP 500."
    })


@app.route("/isbn/<isbn>")
def lookup(isbn):
    isbn = clean_isbn(isbn)

    google = get_google_books_info(isbn)
    bonavendi = try_bonavendi(isbn)

    return jsonify({
        "ok": True,
        "isbn": isbn,
        "title": google.get("title", "Nicht gefunden"),
        "author": google.get("author", "-"),
        "ankauf": bonavendi.get("ankauf", {}),
        "bonavendi": bonavendi.get("bonavendi", {}),
        "bonavendi_raw": bonavendi.get("raw", []),
        "verkauf": {
            "medimops": None,
            "zvab": None,
            "booklooker": None,
            "amazon": None,
            "ebay": None
        },
        "blocked": bonavendi.get("blocked", False),
        "error": bonavendi.get("error"),
        "hinweis": "Bonavendi blockiert Railway aktuell mit HTTP 403." if bonavendi.get("blocked") else ""
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

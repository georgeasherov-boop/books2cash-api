from flask import Flask, jsonify
import requests

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Books2Cash/1.0",
    "Accept": "application/json,text/plain,*/*",
}


def clean_isbn(isbn):
    return "".join(
        ch for ch in str(isbn)
        if ch.isdigit() or ch.upper() == "X"
    ).upper()


def google_books(isbn):
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


def openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/isbn/{isbn}.json"
        r = requests.get(url, headers=HEADERS, timeout=10)

        if r.status_code != 200:
            return None

        data = r.json()

        title = data.get("title", "").strip()
        author = "-"

        authors = data.get("authors", [])
        if authors:
            author_names = []

            for a in authors:
                key = a.get("key")
                if key:
                    try:
                        ar = requests.get(
                            f"https://openlibrary.org{key}.json",
                            headers=HEADERS,
                            timeout=6
                        )
                        if ar.status_code == 200:
                            ad = ar.json()
                            name = ad.get("name", "").strip()
                            if name:
                                author_names.append(name)
                    except Exception:
                        pass

            if author_names:
                author = ", ".join(author_names)

        if not title:
            return None

        return {
            "title": title,
            "author": author,
            "source": "openlibrary"
        }

    except Exception:
        return None


def get_book_info(isbn):
    result = google_books(isbn)

    if result:
        return result

    result = openlibrary(isbn)

    if result:
        return result

    return {
        "title": "Nicht gefunden",
        "author": "-",
        "source": "none"
    }


@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "version": "book_lookup_v2",
        "sources": ["google_books", "openlibrary"]
    })


@app.route("/isbn/<isbn>")
def lookup(isbn):
    isbn = clean_isbn(isbn)

    info = get_book_info(isbn)

    return jsonify({
        "ok": True,
        "isbn": isbn,
        "title": info.get("title", "Nicht gefunden"),
        "author": info.get("author", "-"),
        "source": info.get("source", "none"),
        "ankauf": {},
        "bonavendi": {},
        "bonavendi_raw": [],
        "verkauf": {
            "medimops": None,
            "zvab": None,
            "booklooker": None,
            "amazon": None,
            "ebay": None
        },
        "blocked": False,
        "error": None,
        "hinweis": ""
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

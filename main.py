from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft"
    })

@app.route("/isbn/<isbn>")
def lookup(isbn):

    isbn = isbn.replace("-", "").strip()

    prices = {}

    # Google Books
    try:
        r = requests.get(
            f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}",
            timeout=10
        )

        data = r.json()

        if "items" in data:
            volume = data["items"][0]["volumeInfo"]

            prices["title"] = volume.get("title", "Unbekannt")
            prices["author"] = ", ".join(volume.get("authors", []))
        else:
            prices["title"] = "Nicht gefunden"
            prices["author"] = "-"
    except:
        prices["title"] = "Fehler"
        prices["author"] = "-"

    # Demo Ankaufspreise
    prices["ankauf"] = {
        "momox": 10.49,
        "rebuy": 11.35,
        "zoxs": 11.16,
        "buchmaxe": 5.19,
        "1000books": 2.66
    }

    # Demo Verkaufspreise
    prices["verkauf"] = {
        "medimops": 22.90,
        "zvab": 24.50,
        "booklooker": 19.99,
        "amazon": 27.80,
        "ebay": 21.00
    }

    return jsonify(prices)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

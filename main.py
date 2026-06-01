from flask import Flask, jsonify, request
import os
import re
import time
import sqlite3
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

VERSION = "books2cash_backend_v13_professional_free_verified"
DB_PATH = os.environ.get("BOOKS2CASH_DB_PATH", "books2cash_cache.sqlite3")
TIMEOUT = 7
LOOKUP_TIMEOUT_SECONDS = 9
PRICE_TIMEOUT_SECONDS = 13

HEADERS = {
    "User-Agent": (
        "Books2Cash/13.0 (+https://github.com/georgeasherov-boop/books2cash-api) "
        "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8,fr;q=0.7,it;q=0.6,tr;q=0.5,ru;q=0.4",
}

JSON_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": HEADERS["Accept-Language"],
}

BAD_TITLE_PARTS = [
    "duckduckgo",
    "google",
    "suchergebnisse",
    "search results",
    "search",
    "suche",
    "medimops",
    "gebrauchte produkte",
    "online kaufen",
    "amazon.de",
    "amazon.com",
    "ebay",
    "booklooker",
    "rebuy",
    "momox",
    "vinted",
    "willhaben",
    "captcha",
    "access denied",
    "not found",
    "seite nicht gefunden",
    "error",
    "condition very good",
    "condition good",
    "condition acceptable",
    "used very good",
    "warenkorb",
    "login",
    "products search",
    "shop",
]

KNOWN_ITEMS = {
    "4049834002961": {
        "title": "Love and Other Disasters",
        "details": "Regie: Alek Keshishian · Brittany Murphy / Matthew Rhys · DVD",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
    "4042564128512": {
        "title": "Der Duft der grünen Papaya",
        "details": "Regie: Tran Anh Hung · DVD · Frankreich 1993 · FSK 6 · ca. 100 Minuten",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
    "7321925014167": {
        "title": "Sex and the City – Der Film",
        "details": "Regie: Michael Patrick King · DVD · FSK 12 · 139 Minuten",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
    "7321921396809": {
        "title": "O.C., California - Die komplette erste Staffel (7 DVDs)",
        "details": "Peter Gallagher / Kelly Rowan · Warner Home Video · DVD · FSK 12 · 1130 Minuten",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
    "7321925008463": {
        "title": "Hairspray",
        "details": "Regie: Adam Shankman · DVD · USA 2007 · FSK 0 · 112 Minuten",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
    "9782266353267": {
        "title": "Les Assassins de l'aube",
        "details": "Michel Bussi · Pocket · Französisches Buch · ISBN-10: 2266353268",
        "item_type": "Buch",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
}


# -----------------------------
# General helpers
# -----------------------------
def clean_code(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def is_isbn(code):
    c = clean_code(code)
    return bool(
        re.fullmatch(r"\d{9}[0-9X]", c)
        or re.fullmatch(r"97[89]\d{10}", c)
    )


def isbn13_to_isbn10(isbn13):
    c = clean_code(isbn13)

    if not re.fullmatch(r"978\d{10}", c):
        return None

    core = c[3:12]
    total = sum((10 - i) * int(core[i]) for i in range(9))
    check = 11 - (total % 11)

    if check == 10:
        check_char = "X"
    elif check == 11:
        check_char = "0"
    else:
        check_char = str(check)

    return core + check_char


def fetch(url, timeout=TIMEOUT, headers=None):
    try:
        r = requests.get(
            url,
            headers=headers or HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )

        return {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "url": r.url,
            "text": r.text or "",
        }

    except Exception as exc:
        return {
            "ok": False,
            "status": "exception",
            "url": url,
            "text": "",
            "error": str(exc),
        }


def html_to_text(html):
    try:
        soup = BeautifulSoup(html or "", "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        return soup.get_text(" ", strip=True)

    except Exception:
        return ""


def cleanup_title(title):
    text = unquote(str(title or ""))
    text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)

    replacements = {
        "&amp;": "&",
        "&quot;": '"',
        "&#39;": "'",
        "&apos;": "'",
        "\xa0": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"\s+", " ", text).strip(" -|:,.")

    patterns = [
        r"\s*\|\s*(DVD|Blu-ray|Bluray|CD)\s*\|\s*Condition\s+.*$",
        r"\s*\|\s*Condition\s+.*$",
        r"\s*\|\s*(DVD|Blu-ray|Bluray|CD)\s*$",
        r"\s+-\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox|Fnac|AbeBooks|Google|YouTube).*$",
        r"\s+\|\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox|Fnac|AbeBooks|Google|YouTube).*$",
        r"\s+(online kaufen|gebraucht kaufen).*$",
        r"\s+\[(DVD|Blu-ray|CD)\].*$",
        r"\s+\((DVD|Blu-ray|CD)\).*$",
    ]

    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip(" -|:,;")

    return text.strip(" -|:,.")


def is_bad_title(title):
    t = cleanup_title(title).lower()

    if not t or len(t) < 3:
        return True

    if any(bad in t for bad in BAD_TITLE_PARTS):
        return True

    if re.fullmatch(
        r"(dvd|blu-ray|bluray|cd|buch|book|film|movie|product|produkt)",
        t,
        flags=re.IGNORECASE,
    ):
        return True

    if len(t) > 160:
        return True

    return False


def extract_by_creator(raw_title):
    match = re.search(r"\bBy\s+([^|]+)", str(raw_title or ""), flags=re.IGNORECASE)

    if not match:
        return ""

    return match.group(1).strip(" -|:;,. ")


def detect_item_type(code, title="", details="", source=""):
    text = f"{code} {title} {details} {source}".lower()

    if is_isbn(code):
        return "Buch"

    if any(x in text for x in ["blu-ray", "bluray", "blu ray", "bd-rom"]):
        return "Blu-ray"

    if any(
        x in text
        for x in [
            " dvd",
            "dvd ",
            "dvd-",
            "| dvd",
            "film",
            "movie",
            "fsk",
            "regie",
            "director",
            "warner",
            "universal pictures",
            "paramount",
        ]
    ):
        return "DVD"

    if any(
        x in text
        for x in [
            "vinyl",
            "schallplatte",
            " lp",
            "gramophone record",
            "12 inch",
            "7 inch",
        ]
    ):
        return "Schallplatte"

    if any(
        x in text
        for x in [
            "audio cd",
            "compact disc",
            "musicbrainz",
            "album",
            "soundtrack",
            "cd ",
            "| cd",
        ]
    ):
        return "CD"

    if any(
        x in text
        for x in [
            "playstation",
            "ps5",
            "ps4",
            "xbox",
            "nintendo switch",
            "nintendo",
            "videospiel",
            "video game",
        ]
    ):
        if any(
            x in text
            for x in [
                "konsole",
                "console",
                "controller",
                "joy-con",
                "dualsense",
                "dualshock",
            ]
        ):
            return "Konsole"

        return "Konsolenspiel"

    if any(
        x in text
        for x in [
            "brettspiel",
            "board game",
            "gesellschaftsspiel",
            "ravensburger",
            "hasbro",
            "asmodee",
            "kosmos",
            "pegasus spiele",
        ]
    ):
        return "Brettspiel"

    if any(x in text for x in ["comic", "manga", "graphic novel"]):
        return "Comic"

    if any(
        x in text
        for x in [
            "pokemon",
            "pokémon",
            "trading card",
            "sammelkarte",
            "funko",
            "lego",
            "collectible",
        ]
    ):
        return "Sammelobjekt"

    return "Sonstiges"


def make_result(
    title,
    details="-",
    source="unknown",
    item_type=None,
    confidence=50,
    verified=False,
    **extra,
):
    title = cleanup_title(title)

    if is_bad_title(title):
        return None

    details = str(details or "-").strip()
    item_type = item_type or detect_item_type("", title, details, source)

    return {
        "title": title,
        "author": details,
        "details": details,
        "item_type": item_type,
        "source": source,
        "confidence": int(confidence),
        "verified": bool(verified),
        "creator": extra.get("creator", ""),
        "publisher": extra.get("publisher", ""),
        "year": str(extra.get("year", "") or ""),
        "language": extra.get("language", ""),
        "medium": extra.get("medium", ""),
        "platform": extra.get("platform", ""),
        "manufacturer": extra.get("manufacturer", ""),
    }


def public_candidate(item):
    if not item:
        return None

    keys = [
        "title",
        "author",
        "details",
        "item_type",
        "source",
        "confidence",
        "verified",
        "creator",
        "publisher",
        "year",
        "language",
        "medium",
        "platform",
        "manufacturer",
    ]

    return {key: item.get(key, "") for key in keys}


# -----------------------------
# SQLite cache
# -----------------------------
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_cache (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            details TEXT DEFAULT '',
            item_type TEXT DEFAULT 'Sonstiges',
            source TEXT DEFAULT 'manual_cache',
            confidence INTEGER DEFAULT 100,
            creator TEXT DEFAULT '',
            publisher TEXT DEFAULT '',
            year TEXT DEFAULT '',
            language TEXT DEFAULT '',
            medium TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )

    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(media_cache)").fetchall()
    }

    columns = {
        "details": "TEXT DEFAULT ''",
        "creator": "TEXT DEFAULT ''",
        "publisher": "TEXT DEFAULT ''",
        "year": "TEXT DEFAULT ''",
        "language": "TEXT DEFAULT ''",
        "medium": "TEXT DEFAULT ''",
        "platform": "TEXT DEFAULT ''",
        "manufacturer": "TEXT DEFAULT ''",
    }

    for column, definition in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE media_cache ADD COLUMN {column} {definition}")

    conn.commit()
    conn.close()


def cache_get(code):
    c = clean_code(code)

    if not c:
        return None

    try:
        conn = db_connect()
        row = conn.execute("SELECT * FROM media_cache WHERE code = ?", (c,)).fetchone()
        conn.close()

        if not row:
            return None

        return make_result(
            row["title"],
            row["details"] or "-",
            "sqlite_cache",
            row["item_type"],
            max(int(row["confidence"] or 90), 90),
            True,
            creator=row["creator"] or "",
            publisher=row["publisher"] or "",
            year=row["year"] or "",
            language=row["language"] or "",
            medium=row["medium"] or "",
            platform=row["platform"] or "",
            manufacturer=row["manufacturer"] or "",
        )

    except Exception:
        return None


def cache_save(code, item, source=None):
    c = clean_code(code)

    if not c or not item or is_bad_title(item.get("title")):
        return False

    now = int(time.time())

    try:
        conn = db_connect()

        conn.execute(
            """
            INSERT INTO media_cache (
                code,
                title,
                details,
                item_type,
                source,
                confidence,
                creator,
                publisher,
                year,
                language,
                medium,
                platform,
                manufacturer,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title = excluded.title,
                details = excluded.details,
                item_type = excluded.item_type,
                source = excluded.source,
                confidence = excluded.confidence,
                creator = excluded.creator,
                publisher = excluded.publisher,
                year = excluded.year,
                language = excluded.language,
                medium = excluded.medium,
                platform = excluded.platform,
                manufacturer = excluded.manufacturer,
                updated_at = excluded.updated_at
            """,
            (
                c,
                item.get("title", ""),
                item.get("details") or item.get("author") or "-",
                item.get("item_type", "Sonstiges"),
                source or item.get("source", "auto_cache"),
                int(item.get("confidence", 80)),
                item.get("creator", ""),
                item.get("publisher", ""),
                item.get("year", ""),
                item.get("language", ""),
                item.get("medium", ""),
                item.get("platform", ""),
                item.get("manufacturer", ""),
                now,
                now,
            ),
        )

        conn.commit()
        conn.close()
        return True

    except Exception:
        return False


init_db()


# -----------------------------
# Lookup sources
# -----------------------------
def fetch_google_books(code):
    if not is_isbn(code):
        return []

    c = clean_code(code)
    queries = [f"isbn:{c}"]

    isbn10 = isbn13_to_isbn10(c)

    if isbn10:
        queries.append(f"isbn:{isbn10}")

    candidates = []

    for query in queries:
        for country in ["DE", "US", "GB", "FR", "IT", "TR", "RU"]:
            try:
                url = (
                    "https://www.googleapis.com/books/v1/volumes"
                    f"?q={quote(query)}&country={country}&maxResults=5"
                )

                data = requests.get(url, headers=JSON_HEADERS, timeout=6).json()

                for item in data.get("items", []) or []:
                    info = item.get("volumeInfo", {}) or {}

                    title = info.get("title", "")
                    authors = ", ".join(info.get("authors", []) or [])
                    publisher = info.get("publisher", "") or ""
                    year = info.get("publishedDate", "") or ""
                    language = (info.get("language", "") or "").upper()

                    details = " · ".join(
                        x for x in [authors, publisher, year, language, "Buch"] if x
                    )

                    result = make_result(
                        title,
                        details,
                        f"google_books_{country}",
                        "Buch",
                        92,
                        True,
                        creator=authors,
                        publisher=publisher,
                        year=year,
                        language=language,
                        medium="Buch",
                    )

                    if result:
                        candidates.append(result)

            except Exception:
                continue

    return candidates


def fetch_openlibrary(code):
    if not is_isbn(code):
        return []

    candidates = []
    codes = [clean_code(code)]

    isbn10 = isbn13_to_isbn10(code)

    if isbn10:
        codes.append(isbn10)

    for code_item in codes:
        try:
            url = (
                "https://openlibrary.org/api/books"
                f"?bibkeys=ISBN:{quote(code_item)}&format=json&jscmd=data"
            )

            data = requests.get(url, headers=JSON_HEADERS, timeout=6).json()
            item = data.get(f"ISBN:{code_item}")

            if not item:
                continue

            authors = ", ".join(
                author.get("name", "")
                for author in item.get("authors", [])
                if author.get("name")
            )

            publishers = ", ".join(
                publisher.get("name", "")
                for publisher in item.get("publishers", [])
                if publisher.get("name")
            )

            year = item.get("publish_date", "") or ""

            details = " · ".join(
                x for x in [authors, publishers, year, "Buch"] if x
            )

            result = make_result(
                item.get("title", ""),
                details,
                "openlibrary",
                "Buch",
                88,
                True,
                creator=authors,
                publisher=publishers,
                year=year,
                medium="Buch",
            )

            if result:
                candidates.append(result)

        except Exception:
            continue

    return candidates


def fetch_crossref(code):
    if not is_isbn(code):
        return []

    try:
        c = clean_code(code)
        url = f"https://api.crossref.org/works?filter=isbn:{quote(c)}&rows=5"

        data = requests.get(url, headers=JSON_HEADERS, timeout=6).json()
        candidates = []

        for item in data.get("message", {}).get("items", []) or []:
            titles = item.get("title") or [""]
            title = titles[0]

            authors = []

            for author in item.get("author", []) or []:
                name = f"{author.get('given', '')} {author.get('family', '')}".strip()

                if name:
                    authors.append(name)

            creator = ", ".join(authors)
            publisher = item.get("publisher", "") or ""
            year = ""

            parts = (
                item.get("published-print", {}).get("date-parts")
                or item.get("published-online", {}).get("date-parts")
                or []
            )

            if parts and parts[0]:
                year = str(parts[0][0])

            details = " · ".join(
                x for x in [creator, publisher, year, "Buch"] if x
            )

            result = make_result(
                title,
                details,
                "crossref",
                "Buch",
                82,
                True,
                creator=creator,
                publisher=publisher,
                year=year,
                medium="Buch",
            )

            if result:
                candidates.append(result)

        return candidates

    except Exception:
        return []


def fetch_dnb(code):
    if not is_isbn(code):
        return []

    try:
        c = clean_code(code)
        url = (
            "https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve"
            f"&query=isbn={quote(c)}&recordSchema=MARC21-xml"
        )

        response = requests.get(url, headers=HEADERS, timeout=6)

        if response.status_code != 200:
            return []

        root = ET.fromstring(response.text)
        namespace = {"marc": "http://www.loc.gov/MARC21/slim"}

        title = ""
        creator = ""
        publisher = ""
        year = ""

        for field in root.findall(".//marc:datafield", namespace):
            tag = field.attrib.get("tag")

            if tag == "245":
                parts = [
                    sub.text.strip()
                    for sub in field.findall("marc:subfield", namespace)
                    if sub.attrib.get("code") in ["a", "b"] and sub.text
                ]

                title = cleanup_title(" ".join(parts).strip(" /:"))

            elif tag == "100":
                for sub in field.findall("marc:subfield", namespace):
                    if sub.attrib.get("code") == "a" and sub.text:
                        creator = sub.text.strip(" ,")

            elif tag in ["260", "264"]:
                for sub in field.findall("marc:subfield", namespace):
                    if sub.attrib.get("code") == "b" and sub.text:
                        publisher = sub.text.strip(" ,")

                    if sub.attrib.get("code") == "c" and sub.text:
                        year = sub.text.strip(" ,.")

        details = " · ".join(
            x for x in [creator, publisher, year, "Buch"] if x
        )

        result = make_result(
            title,
            details,
            "dnb",
            "Buch",
            80,
            True,
            creator=creator,
            publisher=publisher,
            year=year,
            medium="Buch",
        )

        return [result] if result else []

    except Exception:
        return []


def fetch_bnf(code):
    if not is_isbn(code):
        return []

    try:
        c = clean_code(code)
        url = (
            "https://catalogue.bnf.fr/api/SRU?version=1.2&operation=searchRetrieve"
            f"&query=bib.isbn%20all%20%22{quote(c)}%22&maximumRecords=5"
        )

        response = requests.get(url, headers=HEADERS, timeout=7)

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, "xml")

        title = ""
        creator = ""

        for tag in soup.find_all():
            name = tag.name.lower()

            if not title and name.endswith("title") and tag.get_text(strip=True):
                title = cleanup_title(tag.get_text(" ", strip=True))

            if not creator and (
                name.endswith("creator") or name.endswith("author")
            ) and tag.get_text(strip=True):
                creator = tag.get_text(" ", strip=True)

        details = " · ".join(x for x in [creator, "FR", "Buch"] if x)

        result = make_result(
            title,
            details,
            "bnf",
            "Buch",
            84,
            True,
            creator=creator,
            language="FR",
            medium="Buch",
        )

        return [result] if result else []

    except Exception:
        return []


def fetch_musicbrainz(code):
    c = clean_code(code)

    if len(c) < 8 or is_isbn(c):
        return []

    try:
        url = (
            "https://musicbrainz.org/ws/2/release/"
            f"?query=barcode:{quote(c)}&fmt=json&limit=5"
        )

        headers = {
            "User-Agent": "Books2Cash/13.0 (github.com/georgeasherov-boop/books2cash-api)",
            "Accept": "application/json",
        }

        data = requests.get(url, headers=headers, timeout=8).json()
        candidates = []

        for release in data.get("releases", []) or []:
            title = release.get("title", "")
            artists = []
            formats = []

            for credit in release.get("artist-credit", []) or []:
                name = credit.get("name") or (credit.get("artist") or {}).get("name")

                if name:
                    artists.append(name)

            for media in release.get("media", []) or []:
                if media.get("format"):
                    formats.append(media.get("format"))

            item_type = (
                "Schallplatte"
                if any("vinyl" in fmt.lower() for fmt in formats)
                else "CD"
            )

            creator = ", ".join(artists)
            medium = ", ".join(formats) if formats else item_type

            details = " · ".join(x for x in [creator, medium] if x)

            result = make_result(
                title,
                details,
                "musicbrainz",
                item_type,
                92,
                True,
                creator=creator,
                medium=medium,
            )

            if result:
                candidates.append(result)

        return candidates

    except Exception:
        return []


def fetch_wikidata_gtin(code):
    c = clean_code(code)

    if len(c) < 8:
        return []

    try:
        sparql = f"""
        SELECT ?item ?itemLabel ?itemDescription WHERE {{
          VALUES ?gtin {{ "{c}" }}
          ?item wdt:P3962 ?gtin.
          SERVICE wikibase:label {{
            bd:serviceParam wikibase:language "de,en,fr,it,tr,ru".
          }}
        }}
        LIMIT 5
        """

        url = "https://query.wikidata.org/sparql?format=json&query=" + quote(sparql)

        data = requests.get(
            url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "application/sparql-results+json",
            },
            timeout=9,
        ).json()

        candidates = []

        for row in data.get("results", {}).get("bindings", []) or []:
            title = row.get("itemLabel", {}).get("value", "")
            description = row.get("itemDescription", {}).get("value", "") or "-"

            item_type = detect_item_type(c, title, description, "wikidata")

            result = make_result(
                title,
                description,
                "wikidata_gtin",
                item_type,
                88,
                True,
            )

            if result:
                candidates.append(result)

        return candidates

    except Exception:
        return []


def fetch_upcitemdb(code):
    c = clean_code(code)

    if len(c) < 8 or is_isbn(c):
        return []

    try:
        url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={quote(c)}"

        data = requests.get(url, headers=JSON_HEADERS, timeout=8).json()
        candidates = []

        for item in data.get("items", []) or []:
            raw_title = item.get("title", "") or ""
            title = cleanup_title(raw_title)
            creator = extract_by_creator(raw_title)
            brand = item.get("brand", "") or ""
            category = item.get("category", "") or ""
            description = item.get("description", "") or ""

            item_type = detect_item_type(
                c,
                raw_title,
                f"{brand} {category} {description}",
                "upcitemdb",
            )

            if "dvd & blu-ray players" in category.lower():
                category = ""
                brand = ""

            details = " · ".join(
                x
                for x in [
                    creator,
                    brand,
                    category,
                    item_type if item_type != "Sonstiges" else "",
                ]
                if x
            )

            confidence = 62

            if "condition" in raw_title.lower() or " by " in raw_title.lower():
                confidence -= 10

            if item_type != "Sonstiges":
                confidence += 6

            result = make_result(
                title,
                details or "-",
                "upcitemdb_candidate",
                item_type,
                confidence,
                False,
                creator=creator,
                publisher=brand,
                manufacturer=brand,
                medium=item_type
                if item_type in ["DVD", "Blu-ray", "CD", "Schallplatte"]
                else "",
            )

            if result:
                candidates.append(result)

        return candidates

    except Exception:
        return []


def extract_duckduckgo_results(html):
    soup = BeautifulSoup(html or "", "html.parser")
    rows = []

    for link in soup.select("a.result__a"):
        title = cleanup_title(link.get_text(" ", strip=True))
        href = link.get("href", "")

        if title and not is_bad_title(title):
            rows.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": "",
                }
            )

    return rows[:10]


def fetch_duckduckgo_candidates(code):
    c = clean_code(code)

    if not c:
        return []

    if is_isbn(c):
        queries = [
            f'"{c}" ISBN Buch Autor',
            f'"{c}" book title author',
            f'"{c}" livre auteur',
        ]

        isbn10 = isbn13_to_isbn10(c)

        if isbn10:
            queries.append(f'"{isbn10}" ISBN')

    else:
        queries = [
            f'"{c}" DVD film title',
            f'"{c}" Blu-ray film',
            f'"{c}" medimops rebuy booklooker',
            f'"{c}" CD vinyl',
            f'"{c}" PS5 PS4 Xbox Nintendo Switch',
        ]

    candidates = []

    for query in queries:
        result = fetch("https://duckduckgo.com/html/?q=" + quote(query), timeout=10)

        if not result["ok"]:
            continue

        for row in extract_duckduckgo_results(result["text"]):
            title = cleanup_title(row["title"])

            item_type = detect_item_type(c, title, row.get("url", ""), query)
            details = item_type if item_type != "Sonstiges" else "-"

            confidence = 45 + (10 if item_type != "Sonstiges" else 0)

            candidate = make_result(
                title,
                details,
                "duckduckgo_weak_candidate",
                item_type,
                confidence,
                False,
            )

            if candidate:
                candidate["url"] = row.get("url", "")
                candidates.append(candidate)

    return candidates


# -----------------------------
# Candidate ranking
# -----------------------------
def canonical_title(title):
    text = cleanup_title(title).lower()

    text = re.sub(
        r"[^a-z0-9äöüßàâçéèêëîïôûùüÿñæœа-яА-Я]+",
        " ",
        text,
        flags=re.IGNORECASE,
    ).strip()

    return text


def rank_candidates(candidates):
    grouped = {}

    for candidate in candidates:
        if not candidate or is_bad_title(candidate.get("title", "")):
            continue

        key = canonical_title(candidate["title"])

        if not key:
            continue

        item = dict(candidate)
        item["_key"] = key
        grouped.setdefault(key, []).append(item)

    ranked = []

    for key, items in grouped.items():
        best = max(items, key=lambda item: int(item.get("confidence", 0)))
        sources = {item.get("source", "") for item in items}
        verified_count = sum(1 for item in items if item.get("verified"))

        score = int(best.get("confidence", 0))

        if len(sources) >= 2:
            score += 12

        if verified_count >= 1:
            score += 15

        if verified_count >= 2:
            score += 8

        if sources <= {"duckduckgo_weak_candidate"}:
            score = min(score, 58)

        if sources <= {"upcitemdb_candidate"}:
            score = min(score, 68)

        best = dict(best)
        best["confidence"] = min(score, 99)
        best["sources"] = sorted(sources)
        best["verified"] = bool(verified_count >= 1 or score >= 85)

        ranked.append(best)

    ranked.sort(key=lambda item: int(item.get("confidence", 0)), reverse=True)
    return ranked


def collect_candidates(code):
    c = clean_code(code)

    if not c:
        return []

    candidates = []

    if c in KNOWN_ITEMS:
        known = dict(KNOWN_ITEMS[c])
        known.setdefault("details", known.get("author", "-"))
        known.setdefault("author", known.get("details", "-"))
        candidates.append(known)
        return candidates

    cached = cache_get(c)

    if cached:
        candidates.append(cached)
        return candidates

    if is_isbn(c):
        sources = [
            fetch_google_books,
            fetch_openlibrary,
            fetch_bnf,
            fetch_crossref,
            fetch_dnb,
        ]
    else:
        sources = [
            fetch_musicbrainz,
            fetch_wikidata_gtin,
            fetch_upcitemdb,
            fetch_duckduckgo_candidates,
        ]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(source, c) for source in sources]
        deadline = time.time() + LOOKUP_TIMEOUT_SECONDS

        try:
            for future in as_completed(futures, timeout=LOOKUP_TIMEOUT_SECONDS):
                if time.time() > deadline:
                    break

                try:
                    candidates.extend(future.result() or [])
                except Exception:
                    pass

        except Exception:
            pass

    return candidates


def lookup_product(code):
    c = clean_code(code)
    ranked = rank_candidates(collect_candidates(c))
    best = ranked[0] if ranked else None

    if best and (best.get("verified") or int(best.get("confidence", 0)) >= 82):
        cache_save(c, best, source=best.get("source", "auto_cache"))
        found = True
        message = "Treffer gefunden"
    else:
        best = None
        found = False
        message = "Kein verlässlicher Treffer gefunden"

    empty_type = "Buch" if is_isbn(c) else "Sonstiges"

    return {
        "ok": True,
        "version": VERSION,
        "code": c,
        "isbn": c,
        "found": found,
        "verified": bool(best and best.get("verified")),
        "confidence": int(best.get("confidence", 0)) if best else 0,
        "title": best.get("title", "") if best else "",
        "author": best.get("details", "") if best else "",
        "details": best.get("details", "") if best else "",
        "creator": best.get("creator", "") if best else "",
        "publisher": best.get("publisher", "") if best else "",
        "year": best.get("year", "") if best else "",
        "language": best.get("language", "") if best else "",
        "medium": best.get("medium", "") if best else "",
        "platform": best.get("platform", "") if best else "",
        "manufacturer": best.get("manufacturer", "") if best else "",
        "item_type": best.get("item_type", empty_type) if best else empty_type,
        "source": best.get("source", "none") if best else "none",
        "message": message,
        "candidates": [public_candidate(item) for item in ranked[:20]],
    }


# -----------------------------
# Prices
# -----------------------------
def normalize_price(value):
    if value is None:
        return None

    text = (
        str(value)
        .replace("\xa0", " ")
        .replace("EUR", "€")
        .replace("Euro", "€")
    )

    match = re.search(r"(\d{1,5}(?:[.,]\d{1,2}))", text)

    if not match:
        return None

    try:
        number = float(match.group(1).replace(",", "."))
    except Exception:
        return None

    return number if 0 < number <= 3000 else None


def fmt(value):
    number = normalize_price(value)

    if number is None:
        return None

    return f"{number:.2f}".replace(".", ",")


def extract_prices(text):
    if not text:
        return []

    prices = []

    patterns = [
        r"(\d{1,5}[,.]\d{2})\s*€",
        r"€\s*(\d{1,5}[,.]\d{2})",
        r"(\d{1,5}[,.]\d{2})\s*EUR",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text.replace("\xa0", " "), flags=re.IGNORECASE):
            number = normalize_price(match.group(1))

            if number is not None:
                prices.append(number)

    return prices


def strict_prices_from_url(url, code, min_price=0.50, max_price=1000):
    result = fetch(url, timeout=10)

    if not result["ok"]:
        return [], {
            "status": result["status"],
            "url": url,
            "reason": "http_error",
        }

    merged = result["text"] + " " + html_to_text(result["text"])
    page_digits = re.sub(r"[^0-9Xx]", "", merged).upper()

    if clean_code(code) not in page_digits:
        return [], {
            "status": result["status"],
            "url": url,
            "reason": "code_not_found_on_page",
        }

    prices = [
        price
        for price in extract_prices(merged)
        if min_price <= price <= max_price
    ]

    return prices, {
        "status": result["status"],
        "url": url,
        "reason": "ok" if prices else "no_price",
    }


PRICE_SOURCES = {
    "buy_momox": [
        "https://www.momox.de/offer/{code}",
        "https://www.momox.de/verkaufen/?search={code}",
    ],
    "buy_rebuy": [
        "https://www.rebuy.de/verkaufen/suchen?query={code}",
    ],
    "buy_zoxs": [
        "https://www.zoxs.de/ankauf/search?search={code}",
    ],
    "sell_medimops": [
        "https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={code}",
    ],
    "sell_rebuy": [
        "https://www.rebuy.de/kaufen/suchen?q={code}",
    ],
    "sell_zoxs": [
        "https://www.zoxs.de/kaufen/search?search={code}",
    ],
    "sell_amazon": [
        "https://www.amazon.de/s?k={code}",
    ],
    "sell_ebay": [
        "https://www.ebay.de/sch/i.html?_nkw={code}&LH_BIN=1",
    ],
    "sell_ebay_sold": [
        "https://www.ebay.de/sch/i.html?_nkw={code}&LH_Sold=1&LH_Complete=1",
    ],
    "sell_booklooker": [
        "https://www.booklooker.de/B%C3%BCcher/Angebote/isbn={code}",
        "https://www.booklooker.de/Filme/Angebote?keywords={code}",
    ],
    "sell_willhaben": [
        "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={code}",
    ],
    "sell_vinted": [
        "https://www.vinted.at/catalog?search_text={code}",
    ],
}


def get_price_source(name, code):
    all_prices = []
    trace = []

    for template in PRICE_SOURCES.get(name, []):
        url = template.format(code=quote(clean_code(code)))

        prices, info = strict_prices_from_url(
            url,
            code,
            min_price=0.01 if name.startswith("buy_") else 0.50,
            max_price=300 if name.startswith("buy_") else 2000,
        )

        all_prices.extend(prices)
        trace.append(info)

    if not all_prices:
        return None, trace

    if name.startswith("sell_ebay") or name.startswith("sell_willhaben") or name.startswith("sell_vinted"):
        value = median(all_prices)
    else:
        value = min(all_prices)

    return value, trace


def get_prices(code):
    c = clean_code(code)
    results = {}
    debug = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(get_price_source, name, c): name
            for name in PRICE_SOURCES.keys()
        }

        try:
            for future in as_completed(futures, timeout=PRICE_TIMEOUT_SECONDS):
                name = futures[future]

                try:
                    value, trace = future.result()

                    results[name] = value
                    debug[name] = {
                        "status": "ok" if value is not None else "no_reliable_price",
                        "trace": trace,
                    }

                except Exception as exc:
                    results[name] = None
                    debug[name] = {
                        "status": "error",
                        "error": str(exc),
                    }

        except Exception:
            pass

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

    buy_clean = [value for value in buy_values if value is not None]
    sell_clean = [value for value in sell_values if value is not None]

    return {
        "ok": True,
        "version": VERSION,
        "code": c,
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
            "best_buy": fmt(max(buy_clean) if buy_clean else None),
            "best_sell": fmt(max(sell_clean) if sell_clean else None),
            "avg_sell": fmt(sum(sell_clean) / len(sell_clean) if sell_clean else None),
        },
        "debug": debug,
    }


def combined_response(code):
    lookup = lookup_product(code)
    prices = get_prices(code)

    response = dict(lookup)

    response.update(
        {
            "ankauf": prices["ankauf"],
            "verkauf": prices["verkauf"],
            "analyse": prices["analyse"],
            "debug": prices["debug"],
            "error": None,
        }
    )

    return response


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "version": VERSION,
            "status": "Books2Cash API läuft",
            "endpoints": [
                "/health",
                "/lookup/<code>",
                "/prices/<code>",
                "/isbn/<code>",
                "/candidates/<code>",
                "/learn/<code>",
                "/cache/<code>",
            ],
            "principle": "Produktdaten und Preise getrennt; schwache Quellen werden nicht blind akzeptiert.",
        }
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "version": VERSION,
            "time": int(time.time()),
            "cache_db": DB_PATH,
        }
    )


@app.route("/lookup/<code>")
def lookup_route(code):
    result = lookup_product(code)

    if request.args.get("candidates") not in ["1", "true", "yes"]:
        result.pop("candidates", None)

    return jsonify(result)


@app.route("/prices/<code>")
def prices_route(code):
    return jsonify(get_prices(code))


@app.route("/isbn/<code>")
def isbn_compatible_route(code):
    return jsonify(combined_response(code))


@app.route("/candidates/<code>")
def candidates_route(code):
    c = clean_code(code)
    ranked = rank_candidates(collect_candidates(c))

    return jsonify(
        {
            "ok": True,
            "version": VERSION,
            "code": c,
            "accepted": public_candidate(ranked[0]) if ranked else None,
            "candidates": [public_candidate(item) for item in ranked[:25]],
        }
    )


@app.route("/learn/<code>", methods=["GET", "POST"])
def learn_route(code):
    c = clean_code(code)

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = request.args.to_dict()

    title = (data.get("title") or "").strip()
    details = (
        data.get("details")
        or data.get("author")
        or data.get("info")
        or "-"
    ).strip()

    item_type = (
        data.get("type")
        or data.get("item_type")
        or "Sonstiges"
    ).strip()

    if not c:
        return jsonify(
            {
                "ok": False,
                "version": VERSION,
                "error": "code fehlt oder ist ungültig",
            }
        ), 400

    if not title:
        return jsonify(
            {
                "ok": False,
                "version": VERSION,
                "error": "title fehlt",
                "example": f"/learn/{c}?title=Hairspray&type=DVD&details=Regie%3A%20Adam%20Shankman",
            }
        ), 400

    if item_type == "Sonstiges":
        item_type = detect_item_type(c, title, details, "manual")

    item = make_result(
        title,
        details,
        "manual_learn",
        item_type,
        100,
        True,
        creator=data.get("creator", ""),
        publisher=data.get("publisher", ""),
        year=data.get("year", ""),
        language=data.get("language", ""),
        medium=data.get("medium", ""),
        platform=data.get("platform", ""),
        manufacturer=data.get("manufacturer", ""),
    )

    saved = cache_save(c, item, "manual_learn")

    return jsonify(
        {
            "ok": saved,
            "version": VERSION,
            "code": c,
            "saved": public_candidate(item),
        }
    )


@app.route("/cache/<code>")
def cache_code_route(code):
    c = clean_code(code)
    cached = cache_get(c)

    return jsonify(
        {
            "ok": cached is not None,
            "version": VERSION,
            "code": c,
            "cached": public_candidate(cached) if cached else None,
        }
    )


@app.route("/cache")
def cache_all_route():
    try:
        conn = db_connect()
        rows = conn.execute(
            """
            SELECT code, title, details, item_type, source, confidence, updated_at
            FROM media_cache
            ORDER BY updated_at DESC
            LIMIT 500
            """
        ).fetchall()
        conn.close()

        items = [dict(row) for row in rows]

        return jsonify(
            {
                "ok": True,
                "version": VERSION,
                "count": len(items),
                "items": items,
            }
        )

    except Exception as exc:
        return jsonify(
            {
                "ok": False,
                "version": VERSION,
                "error": str(exc),
            }
        ), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

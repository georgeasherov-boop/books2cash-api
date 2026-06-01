from flask import Flask, jsonify, request
import os
import re
import time
import sqlite3
import xml.etree.ElementTree as ET
from statistics import median
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

VERSION = "books2cash_backend_v12_free_cache_clean_media"

DB_PATH = os.environ.get("BOOKS2CASH_DB_PATH", "books2cash_cache.sqlite3")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8,fr;q=0.7,it;q=0.6,tr;q=0.5,ru;q=0.4",
}

JSON_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": HEADERS["Accept-Language"],
}

TIMEOUT = 8

GENERIC_TITLES = [
    "medimops",
    "gebrauchte produkte",
    "online kaufen",
    "amazon.de",
    "amazon.com",
    "ebay",
    "willhaben",
    "booklooker",
    "rebuy",
    "momox",
    "vinted",
    "google",
    "captcha",
    "access denied",
    "seite nicht gefunden",
    "not found",
    "error",
    "search",
    "suche",
    "suchergebnisse",
    "products search",
    "shop",
    "warenkorb",
    "login",
    "condition very good",
    "condition good",
    "condition acceptable",
    "used very good",
]

KNOWN_ITEMS = {
    "4049834002961": {
        "title": "Love and Other Disasters",
        "author": "Regie: Alek Keshishian · Brittany Murphy / Matthew Rhys · DVD",
        "details": "DVD · Film · Regie: Alek Keshishian · Darsteller: Brittany Murphy, Matthew Rhys",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "4042564128512": {
        "title": "Der Duft der grünen Papaya",
        "author": "Regie: Tran Anh Hung · DVD · Frankreich 1993 · FSK 6 · ca. 100 Minuten",
        "details": "DVD · Film · Frankreich 1993 · Regie: Tran Anh Hung · FSK 6 · ca. 100 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "7321925014167": {
        "title": "Sex and the City – Der Film",
        "author": "Regie: Michael Patrick King · DVD · FSK 12 · 139 Minuten",
        "details": "DVD · Film · Regie: Michael Patrick King · FSK 12 · 139 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "7321921396809": {
        "title": "O.C., California - Die komplette erste Staffel (7 DVDs)",
        "author": "Peter Gallagher / Kelly Rowan · Warner Home Video · DVD · FSK 12 · 1130 Minuten",
        "details": "DVD-Box · TV-Serie · Warner Home Video · FSK 12 · 7 DVDs · ca. 1130 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "7321925008463": {
        "title": "Hairspray",
        "author": "Regie: Adam Shankman · DVD · USA 2007 · FSK 0 · 112 Minuten",
        "details": "DVD · Film · USA 2007 · Regie: Adam Shankman · FSK 0 · 112 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "9782266353267": {
        "title": "Les Assassins de l'aube",
        "author": "Michel Bussi · Pocket · Französisches Buch · ISBN-10: 2266353268",
        "details": "Buch · Französisch · Autor: Michel Bussi · Verlag: Pocket · ISBN-13: 9782266353267",
        "item_type": "Buch",
        "source": "known_book_cache",
        "confidence": 99,
    },
}


# -------------------------------------------------
# Basis / Cache
# -------------------------------------------------
def clean_code(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def is_isbn(code):
    code = clean_code(code)
    return bool(
        re.fullmatch(r"\d{9}[0-9X]", code)
        or re.fullmatch(r"97[89]\d{10}", code)
    )


def isbn13_to_isbn10(isbn13):
    code = clean_code(isbn13)

    if not re.fullmatch(r"978\d{10}", code):
        return None

    core = code[3:12]
    total = sum((10 - i) * int(core[i]) for i in range(9))
    check = 11 - (total % 11)

    if check == 10:
        check_char = "X"
    elif check == 11:
        check_char = "0"
    else:
        check_char = str(check)

    return core + check_char


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    existing = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing_names = {row["name"] for row in existing}

    if column not in existing_names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_cache (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT DEFAULT '',
            item_type TEXT DEFAULT 'Sonstiges',
            source TEXT DEFAULT 'manual_cache',
            confidence INTEGER DEFAULT 100,
            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )

    ensure_column(conn, "media_cache", "details", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "creator", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "publisher", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "year", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "language", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "medium", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "platform", "TEXT DEFAULT ''")
    ensure_column(conn, "media_cache", "manufacturer", "TEXT DEFAULT ''")

    conn.commit()
    conn.close()


def cache_get(code):
    code = clean_code(code)

    if not code:
        return None

    try:
        conn = db_connect()
        row = conn.execute(
            "SELECT * FROM media_cache WHERE code = ?",
            (code,),
        ).fetchone()
        conn.close()

        if not row:
            return None

        return {
            "title": row["title"],
            "author": row["author"] or row["details"] or "-",
            "details": row["details"] or row["author"] or "-",
            "creator": row["creator"] or "",
            "publisher": row["publisher"] or "",
            "year": row["year"] or "",
            "language": row["language"] or "",
            "medium": row["medium"] or "",
            "platform": row["platform"] or "",
            "manufacturer": row["manufacturer"] or "",
            "item_type": row["item_type"] or "Sonstiges",
            "source": row["source"] or "manual_cache",
            "confidence": int(row["confidence"] or 100),
        }

    except Exception:
        return None


def cache_save(
    code,
    title,
    author="-",
    item_type="Sonstiges",
    source="auto_cache",
    confidence=80,
    details="",
    creator="",
    publisher="",
    year="",
    language="",
    medium="",
    platform="",
    manufacturer="",
):
    code = clean_code(code)
    title = cleanup_title(title)

    if not code or not title or is_generic_title(title):
        return False

    author = str(author or "-").strip()
    details = str(details or author or "-").strip()
    item_type = str(item_type or "Sonstiges").strip()
    source = str(source or "auto_cache").strip()
    now = int(time.time())

    try:
        conn = db_connect()
        conn.execute(
            """
            INSERT INTO media_cache (
                code, title, author, details, creator, publisher, year, language,
                medium, platform, manufacturer, item_type, source, confidence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title = excluded.title,
                author = excluded.author,
                details = excluded.details,
                creator = excluded.creator,
                publisher = excluded.publisher,
                year = excluded.year,
                language = excluded.language,
                medium = excluded.medium,
                platform = excluded.platform,
                manufacturer = excluded.manufacturer,
                item_type = excluded.item_type,
                source = excluded.source,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (
                code,
                title,
                author,
                details,
                creator,
                publisher,
                year,
                language,
                medium,
                platform,
                manufacturer,
                item_type,
                source,
                int(confidence),
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


# -------------------------------------------------
# Text / HTTP / Preise
# -------------------------------------------------
def normalize_price(value):
    if value is None:
        return None

    text = (
        str(value)
        .replace("\xa0", " ")
        .replace("EUR", "€")
        .replace("Euro", "€")
        .strip()
    )

    if not text or text.lower() in {"none", "null", "-", "nan"}:
        return None

    match = re.search(r"(\d{1,5}(?:[.,]\d{1,2}))", text)

    if not match:
        return None

    try:
        number = float(match.group(1).replace(",", "."))
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


def fetch(url, timeout=TIMEOUT, headers=None):
    try:
        response = requests.get(
            url,
            headers=headers or HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )

        return {
            "ok": 200 <= response.status_code < 300,
            "status": response.status_code,
            "url": response.url,
            "text": response.text or "",
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


def page_contains_code(text, code):
    if not text:
        return False

    clean_page = re.sub(r"[^0-9Xx]", "", text).upper()
    return clean_code(code) in clean_page


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

    for price in prices:
        number = normalize_price(price)

        if number is not None and min_price <= number <= max_price:
            clean.append(number)

    return clean


def safe_min(values):
    values = filter_prices(values)
    return min(values) if values else None


def safe_median(values):
    values = filter_prices(values)
    return median(values) if values else None


def get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000):
    result = fetch(url)

    if not result["ok"]:
        return [], {
            "status": result["status"],
            "reason": "http_error",
            "url": url,
        }

    html = result["text"]
    text = html_to_text(html)
    merged = html + " " + text

    if not page_contains_code(merged, code):
        return [], {
            "status": result["status"],
            "reason": "code_not_found_on_page",
            "url": url,
        }

    prices = filter_prices(extract_prices(merged), min_price, max_price)

    if not prices:
        return [], {
            "status": result["status"],
            "reason": "no_price_found",
            "url": url,
        }

    return prices, {
        "status": result["status"],
        "reason": "ok",
        "url": url,
    }


# -------------------------------------------------
# Titel / Typen / Ergebnisformat
# -------------------------------------------------
def is_generic_title(title):
    text = (title or "").strip().lower()

    if not text or len(text) < 3:
        return True

    return any(bad in text for bad in GENERIC_TITLES)


def cleanup_title(title):
    title = unquote(str(title or ""))
    title = BeautifulSoup(title, "html.parser").get_text(" ", strip=True)

    replacements = {
        "&amp;": "&",
        "&quot;": '"',
        "&#39;": "'",
        "&apos;": "'",
        "\xa0": " ",
    }

    for old, new in replacements.items():
        title = title.replace(old, new)

    title = re.sub(r"\s+", " ", title).strip(" -|:;,.\n\t")

    title = re.sub(
        r"\b(DVD|Blu-ray|Bluray|CD|Book|Buch)\b\s*(online kaufen|gebraucht kaufen)?\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip(" -|:;,")

    title = re.sub(
        r"\s+-\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox|Fnac|AbeBooks|Google|YouTube).*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s+\|\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox|Fnac|AbeBooks|Google|YouTube).*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s*\|\s*(DVD|Blu-ray|Bluray|CD)\s*\|\s*Condition\s+.*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s*\|\s*Condition\s+.*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    return title.strip(" -|:;,.")


def clean_upc_listing_title(title):
    original = str(title or "")
    title = cleanup_title(original)

    title = re.sub(
        r"\s*\|\s*(DVD|Blu-ray|Bluray|CD)\s*\|\s*Condition\s+.*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s*\|\s*Condition\s+.*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s*\|\s*(DVD|Blu-ray|Bluray|CD)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = re.sub(
        r"\s+By\s+(.+)$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    title = title.replace("Dvd", "DVD").replace("Blu Ray", "Blu-ray")

    title = re.sub(r"\s+", " ", title).strip(" -|:;,.")

    return title


def extract_by_creator(title):
    match = re.search(r"\bBy\s+([^|]+)", str(title or ""), flags=re.IGNORECASE)

    if match:
        creator = match.group(1).strip(" -|:;,.")

        if creator:
            return creator

    return ""


def detect_item_type(code, title="", author="", source=""):
    text = f"{code} {title} {author} {source}".lower()

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
            "20th century fox",
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
            "game",
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


def make_info_line(
    item_type,
    creator="",
    publisher="",
    year="",
    language="",
    medium="",
    platform="",
    manufacturer="",
    extra="",
):
    parts = []

    if creator:
        if item_type in ["DVD", "Blu-ray"]:
            if not creator.lower().startswith("regie"):
                parts.append(f"Regie/Info: {creator}")
            else:
                parts.append(creator)
        elif item_type == "Buch":
            parts.append(creator)
        elif item_type in ["CD", "Schallplatte"]:
            parts.append(creator)
        elif item_type in ["Konsolenspiel", "Konsole"]:
            parts.append(creator)
        else:
            parts.append(creator)

    if publisher:
        parts.append(publisher)

    if platform:
        parts.append(platform)

    if manufacturer:
        parts.append(manufacturer)

    if medium:
        parts.append(medium)

    if language:
        parts.append(language)

    if year:
        parts.append(str(year))

    if extra:
        parts.append(extra)

    clean_parts = []

    for part in parts:
        part = str(part or "").strip()
        if part and part not in clean_parts:
            clean_parts.append(part)

    return " · ".join(clean_parts) if clean_parts else "-"


def make_result(
    title,
    author="-",
    source="unknown",
    item_type=None,
    confidence=50,
    details="",
    creator="",
    publisher="",
    year="",
    language="",
    medium="",
    platform="",
    manufacturer="",
):
    title = cleanup_title(title)

    if not title or is_generic_title(title):
        return None

    item_type = item_type or detect_item_type("", title, author, source)

    if not details:
        details = make_info_line(
            item_type=item_type,
            creator=creator or author,
            publisher=publisher,
            year=year,
            language=language,
            medium=medium,
            platform=platform,
            manufacturer=manufacturer,
        )

    if not author or author == "-":
        author = details or "-"

    return {
        "title": title,
        "author": author or "-",
        "details": details or author or "-",
        "creator": creator or "",
        "publisher": publisher or "",
        "year": str(year or ""),
        "language": language or "",
        "medium": medium or "",
        "platform": platform or "",
        "manufacturer": manufacturer or "",
        "source": source,
        "item_type": item_type,
        "confidence": int(confidence),
    }


def best_candidate(candidates):
    valid = []

    for item in candidates:
        if not item:
            continue

        title = item.get("title", "")

        if not title or is_generic_title(title):
            continue

        valid.append(item)

    if not valid:
        return None

    valid.sort(key=lambda x: int(x.get("confidence", 0)), reverse=True)
    return valid[0]


# -------------------------------------------------
# Bücher international
# -------------------------------------------------
def fetch_google_books(code):
    try:
        code_clean = clean_code(code)
        queries = [f"isbn:{code_clean}"]

        isbn10 = isbn13_to_isbn10(code_clean)

        if isbn10:
            queries.append(f"isbn:{isbn10}")

        countries = ["DE", "US", "GB", "FR", "IT", "TR", "RU"]
        candidates = []

        for query in queries:
            for country in countries:
                url = (
                    "https://www.googleapis.com/books/v1/volumes"
                    f"?q={quote(query)}&country={country}&maxResults=5"
                )

                response = requests.get(url, headers=JSON_HEADERS, timeout=6)
                data = response.json()

                for item in data.get("items", []) or []:
                    info = item.get("volumeInfo", {}) or {}
                    title = cleanup_title(info.get("title", ""))

                    if not title:
                        continue

                    authors = info.get("authors", []) or []
                    publisher = info.get("publisher", "") or ""
                    published = info.get("publishedDate", "") or ""
                    language = info.get("language", "") or ""

                    creator = ", ".join(authors).strip() if authors else ""
                    author_line = make_info_line(
                        item_type="Buch",
                        creator=creator,
                        publisher=publisher,
                        year=published,
                        language=language.upper() if language else "",
                        medium="Buch",
                    )

                    result = make_result(
                        title=title,
                        author=author_line,
                        details=author_line,
                        creator=creator,
                        publisher=publisher,
                        year=published,
                        language=language.upper() if language else "",
                        medium="Buch",
                        source=f"google_books_{country}",
                        item_type="Buch",
                        confidence=88,
                    )

                    if result:
                        candidates.append(result)

        return best_candidate(candidates)

    except Exception:
        return None


def fetch_openlibrary(code):
    try:
        codes = [clean_code(code)]
        isbn10 = isbn13_to_isbn10(code)

        if isbn10:
            codes.append(isbn10)

        candidates = []

        for code_item in codes:
            url = (
                "https://openlibrary.org/api/books"
                f"?bibkeys=ISBN:{quote(code_item)}&format=json&jscmd=data"
            )

            response = requests.get(url, headers=JSON_HEADERS, timeout=6)
            data = response.json()
            item = data.get(f"ISBN:{code_item}")

            if not item:
                continue

            title = cleanup_title(item.get("title", ""))

            if not title:
                continue

            authors = [
                author.get("name", "")
                for author in item.get("authors", [])
                if author.get("name")
            ]

            publishers = [
                publisher.get("name", "")
                for publisher in item.get("publishers", [])
                if publisher.get("name")
            ]

            creator = ", ".join(authors)
            publisher = ", ".join(publishers)
            year = item.get("publish_date", "")

            author_line = make_info_line(
                item_type="Buch",
                creator=creator,
                publisher=publisher,
                year=year,
                medium="Buch",
            )

            result = make_result(
                title=title,
                author=author_line,
                details=author_line,
                creator=creator,
                publisher=publisher,
                year=year,
                medium="Buch",
                source="openlibrary",
                item_type="Buch",
                confidence=84,
            )

            if result:
                candidates.append(result)

        return best_candidate(candidates)

    except Exception:
        return None


def fetch_crossref(code):
    try:
        code_clean = clean_code(code)
        url = f"https://api.crossref.org/works?filter=isbn:{quote(code_clean)}&rows=3"
        response = requests.get(url, headers=JSON_HEADERS, timeout=6)
        data = response.json()

        items = data.get("message", {}).get("items", []) or []

        if not items:
            return None

        candidates = []

        for item in items:
            titles = item.get("title") or []
            title = cleanup_title(titles[0] if titles else "")

            if not title:
                continue

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

            author_line = make_info_line(
                item_type="Buch",
                creator=creator,
                publisher=publisher,
                year=year,
                medium="Buch",
            )

            result = make_result(
                title=title,
                author=author_line,
                details=author_line,
                creator=creator,
                publisher=publisher,
                year=year,
                medium="Buch",
                source="crossref",
                item_type="Buch",
                confidence=78,
            )

            if result:
                candidates.append(result)

        return best_candidate(candidates)

    except Exception:
        return None


def fetch_dnb(code):
    try:
        code_clean = clean_code(code)
        url = (
            "https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve"
            f"&query=isbn={quote(code_clean)}&recordSchema=MARC21-xml"
        )

        response = requests.get(url, headers=HEADERS, timeout=6)

        if response.status_code != 200:
            return None

        root = ET.fromstring(response.text)
        namespace = {"marc": "http://www.loc.gov/MARC21/slim"}

        title = None
        creator = None
        publisher = None
        year = None

        for field in root.findall(".//marc:datafield", namespace):
            tag = field.attrib.get("tag")

            if tag == "245":
                parts = []
                for sub in field.findall("marc:subfield", namespace):
                    if sub.attrib.get("code") in ["a", "b"] and sub.text:
                        parts.append(sub.text.strip())
                if parts:
                    title = cleanup_title(" ".join(parts).strip(" /:"))

            if tag == "100":
                for sub in field.findall("marc:subfield", namespace):
                    if sub.attrib.get("code") == "a" and sub.text:
                        creator = sub.text.strip(" ,")

            if tag == "260":
                for sub in field.findall("marc:subfield", namespace):
                    if sub.attrib.get("code") == "b" and sub.text:
                        publisher = sub.text.strip(" ,")
                    if sub.attrib.get("code") == "c" and sub.text:
                        year = sub.text.strip(" ,.")

        if not title:
            return None

        author_line = make_info_line(
            item_type="Buch",
            creator=creator or "",
            publisher=publisher or "",
            year=year or "",
            medium="Buch",
        )

        return make_result(
            title=title,
            author=author_line,
            details=author_line,
            creator=creator or "",
            publisher=publisher or "",
            year=year or "",
            medium="Buch",
            source="dnb",
            item_type="Buch",
            confidence=75,
        )

    except Exception:
        return None


def fetch_bnf(code):
    try:
        code_clean = clean_code(code)
        url = (
            "https://catalogue.bnf.fr/api/SRU?version=1.2&operation=searchRetrieve"
            f"&query=bib.isbn%20all%20%22{quote(code_clean)}%22&maximumRecords=5"
        )

        response = requests.get(url, headers=HEADERS, timeout=7)

        if response.status_code != 200:
            return None

        text = html_to_text(response.text)

        if not page_contains_code(response.text + " " + text, code_clean):
            return None

        soup = BeautifulSoup(response.text, "xml")
        title = None
        creator = ""

        for tag in soup.find_all():
            name = tag.name.lower()

            if name.endswith("title") and tag.get_text(strip=True):
                title = cleanup_title(tag.get_text(" ", strip=True))
                break

        for tag in soup.find_all():
            name = tag.name.lower()

            if (name.endswith("creator") or name.endswith("author")) and tag.get_text(strip=True):
                creator = tag.get_text(" ", strip=True)
                break

        if not title:
            return None

        author_line = make_info_line(
            item_type="Buch",
            creator=creator,
            medium="Buch",
            language="FR",
        )

        return make_result(
            title=title,
            author=author_line,
            details=author_line,
            creator=creator,
            language="FR",
            medium="Buch",
            source="bnf_france",
            item_type="Buch",
            confidence=82,
        )

    except Exception:
        return None


# -------------------------------------------------
# Medien / Produkte
# -------------------------------------------------
def fetch_upcitemdb(code):
    try:
        code_clean = clean_code(code)

        if len(code_clean) < 8:
            return None

        url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={quote(code_clean)}"
        response = requests.get(url, headers=JSON_HEADERS, timeout=8)
        data = response.json()

        items = data.get("items", []) or []

        if not items:
            return None

        candidates = []

        for item in items:
            raw_title = item.get("title", "") or ""
            title = clean_upc_listing_title(raw_title)

            if not title or is_generic_title(title):
                continue

            brand = item.get("brand", "") or ""
            category = item.get("category", "") or ""
            description = item.get("description", "") or ""
            creator = extract_by_creator(raw_title)

            item_type = detect_item_type(
                code_clean,
                raw_title,
                f"{brand} {category} {description}",
                "upcitemdb",
            )

            medium = ""
            if item_type in ["DVD", "Blu-ray", "CD", "Schallplatte"]:
                medium = item_type

            publisher = brand
            manufacturer = brand

            if "dvd & blu-ray players" in category.lower():
                category = ""
                manufacturer = ""
                publisher = ""

            author_line = make_info_line(
                item_type=item_type,
                creator=creator,
                publisher=publisher,
                medium=medium,
                manufacturer=manufacturer if item_type not in ["DVD", "Blu-ray"] else "",
                extra=category if category and "dvd & blu-ray players" not in category.lower() else "",
            )

            confidence = 76

            if "condition" in raw_title.lower():
                confidence -= 12

            if " by " in raw_title.lower():
                confidence -= 5

            if item_type in ["DVD", "Blu-ray", "CD", "Schallplatte", "Konsolenspiel"]:
                confidence += 6

            result = make_result(
                title=title,
                author=author_line,
                details=author_line,
                creator=creator,
                publisher=publisher,
                medium=medium,
                manufacturer=manufacturer,
                source="upcitemdb_cleaned",
                item_type=item_type,
                confidence=confidence,
            )

            if result:
                candidates.append(result)

        return best_candidate(candidates)

    except Exception:
        return None


def fetch_musicbrainz(code):
    try:
        code_clean = clean_code(code)

        url = (
            "https://musicbrainz.org/ws/2/release/"
            f"?query=barcode:{quote(code_clean)}&fmt=json&limit=5"
        )

        headers = {
            "User-Agent": "Books2Cash/1.0 (github.com/georgeasherov-boop/books2cash-api)",
            "Accept": "application/json",
        }

        response = requests.get(url, headers=headers, timeout=8)
        data = response.json()

        releases = data.get("releases", []) or []

        if not releases:
            return None

        candidates = []

        for release in releases:
            title = cleanup_title(release.get("title", ""))

            if not title:
                continue

            artists = []
            formats = []

            for credit in release.get("artist-credit", []) or []:
                name = credit.get("name") or (credit.get("artist") or {}).get("name")
                if name:
                    artists.append(name)

            for media in release.get("media", []) or []:
                if media.get("format"):
                    formats.append(media.get("format"))

            item_type = "Schallplatte" if any("vinyl" in fmt_item.lower() for fmt_item in formats) else "CD"
            creator = ", ".join(artists)
            medium = ", ".join(formats) if formats else item_type

            author_line = make_info_line(
                item_type=item_type,
                creator=creator,
                medium=medium,
            )

            result = make_result(
                title=title,
                author=author_line,
                details=author_line,
                creator=creator,
                medium=medium,
                source="musicbrainz",
                item_type=item_type,
                confidence=86,
            )

            if result:
                candidates.append(result)

        return best_candidate(candidates)

    except Exception:
        return None


def fetch_wikidata_gtin(code):
    try:
        code_clean = clean_code(code)

        sparql = f"""
        SELECT ?item ?itemLabel ?itemDescription WHERE {{
          VALUES ?gtin {{ "{code_clean}" }}
          ?item wdt:P3962 ?gtin.
          SERVICE wikibase:label {{
            bd:serviceParam wikibase:language "de,en,fr,it,tr,ru".
          }}
        }}
        LIMIT 5
        """

        url = "https://query.wikidata.org/sparql?format=json&query=" + quote(sparql)

        response = requests.get(
            url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": "application/sparql-results+json",
            },
            timeout=9,
        )

        data = response.json()
        rows = data.get("results", {}).get("bindings", []) or []

        if not rows:
            return None

        candidates = []

        for row in rows:
            title = cleanup_title(row.get("itemLabel", {}).get("value", ""))
            description = row.get("itemDescription", {}).get("value", "") or "-"

            item_type = detect_item_type(code_clean, title, description, "wikidata gtin")
            author_line = make_info_line(
                item_type=item_type,
                extra=description,
            )

            result = make_result(
                title=title,
                author=author_line,
                details=author_line,
                source="wikidata_gtin",
                item_type=item_type,
                confidence=74,
            )

            if result:
                candidates.append(result)

        return best_candidate(candidates)

    except Exception:
        return None


# -------------------------------------------------
# Kostenloser Web-Fallback über DuckDuckGo HTML
# -------------------------------------------------
def extract_search_results_from_duckduckgo(html):
    soup = BeautifulSoup(html or "", "html.parser")
    results = []

    for link in soup.select("a.result__a"):
        title = cleanup_title(link.get_text(" ", strip=True))
        href = link.get("href", "")

        if title and not is_generic_title(title):
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": "",
                }
            )

    if results:
        return results[:10]

    for link in soup.find_all("a"):
        title = cleanup_title(link.get_text(" ", strip=True))
        href = link.get("href", "")

        if len(title) > 8 and not is_generic_title(title) and href:
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": "",
                }
            )

    return results[:10]


def clean_search_title(raw_title, code):
    title = cleanup_title(raw_title)
    code_clean = clean_code(code)

    title = re.sub(re.escape(code_clean), "", title, flags=re.IGNORECASE).strip(" -|:;,.")

    remove_patterns = [
        r"\s*\|\s*.*$",
        r"\s+-\s+(DVD|Blu-ray|CD|Buch|Book|Amazon|eBay|medimops|reBuy|Booklooker|AbeBooks|Fnac).*$",
        r"\s+online kaufen.*$",
        r"\s+gebraucht kaufen.*$",
        r"\s+\(DVD\).*$",
        r"\s+\[DVD\].*$",
        r"\s+\(Blu-ray\).*$",
        r"\s+\[Blu-ray\].*$",
    ]

    for pattern in remove_patterns:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE).strip(" -|:;,")

    return cleanup_title(title)


def fetch_duckduckgo_product(code):
    code_clean = clean_code(code)

    if not code_clean:
        return None

    if is_isbn(code_clean):
        queries = [
            f'"{code_clean}" ISBN book title author',
            f'"{code_clean}" livre auteur',
            f'"{code_clean}" libro autore',
            f'"{code_clean}" kitap yazar',
            f'"{code_clean}" книга автор',
        ]

        isbn10 = isbn13_to_isbn10(code_clean)

        if isbn10:
            queries.extend(
                [
                    f'"{isbn10}" ISBN book title author',
                    f'"{isbn10}" livre auteur',
                ]
            )

    else:
        queries = [
            f'"{code_clean}" DVD film title',
            f'"{code_clean}" Blu-ray film title',
            f'"{code_clean}" reBuy medimops Booklooker',
            f'"{code_clean}" Amazon DVD',
            f'"{code_clean}" eBay DVD',
            f'"{code_clean}" PS5 PS4 Xbox Nintendo Switch game',
            f'"{code_clean}" CD vinyl Discogs MusicBrainz',
        ]

    candidates = []

    for query in queries:
        url = "https://duckduckgo.com/html/?q=" + quote(query)
        result = fetch(url, timeout=10)

        if not result["ok"]:
            continue

        rows = extract_search_results_from_duckduckgo(result["text"])

        for row in rows:
            title = clean_search_title(row["title"], code_clean)

            if not title or is_generic_title(title):
                continue

            source_text = f"{query} {row.get('url', '')} {row.get('snippet', '')}"
            item_type = detect_item_type(code_clean, title, row.get("snippet", ""), source_text)

            score = 62

            if item_type != "Sonstiges":
                score += 8

            trusted = [
                "rebuy",
                "medimops",
                "booklooker",
                "amazon",
                "ebay",
                "abebooks",
                "fnac",
                "lisez",
                "worldcat",
                "discogs",
            ]

            if any(x in row.get("url", "").lower() for x in trusted):
                score += 5

            author_line = make_info_line(
                item_type=item_type,
                medium=item_type if item_type in ["DVD", "Blu-ray", "CD", "Schallplatte"] else "",
                extra=row.get("snippet", "") or "",
            )

            result_item = make_result(
                title=title,
                author=author_line,
                details=author_line,
                source="duckduckgo_free_fallback",
                item_type=item_type,
                confidence=score,
            )

            if result_item:
                result_item["url"] = row.get("url", "")
                candidates.append(result_item)

    return best_candidate(candidates)


# -------------------------------------------------
# Gesamtsuche
# -------------------------------------------------
def get_media_info(code):
    code_clean = clean_code(code)

    if not code_clean:
        return {
            "title": "Nicht gefunden",
            "author": "-",
            "details": "-",
            "item_type": "Sonstiges",
            "source": "invalid_code",
            "confidence": 0,
        }

    if code_clean in KNOWN_ITEMS:
        return KNOWN_ITEMS[code_clean]

    cached = cache_get(code_clean)

    if cached:
        cached["source"] = "sqlite_cache"
        cached["confidence"] = max(int(cached.get("confidence", 90)), 90)
        return cached

    candidates = []

    if is_isbn(code_clean):
        for fn in [
            fetch_google_books,
            fetch_openlibrary,
            fetch_bnf,
            fetch_crossref,
            fetch_dnb,
            fetch_duckduckgo_product,
        ]:
            result = fn(code_clean)
            if result:
                candidates.append(result)
    else:
        for fn in [
            fetch_musicbrainz,
            fetch_wikidata_gtin,
            fetch_duckduckgo_product,
            fetch_upcitemdb,
        ]:
            result = fn(code_clean)
            if result:
                candidates.append(result)

    best = best_candidate(candidates)

    if best:
        cache_save(
            code=code_clean,
            title=best.get("title", ""),
            author=best.get("author", "-"),
            details=best.get("details", best.get("author", "-")),
            creator=best.get("creator", ""),
            publisher=best.get("publisher", ""),
            year=best.get("year", ""),
            language=best.get("language", ""),
            medium=best.get("medium", ""),
            platform=best.get("platform", ""),
            manufacturer=best.get("manufacturer", ""),
            item_type=best.get(
                "item_type",
                detect_item_type(
                    code_clean,
                    best.get("title", ""),
                    best.get("author", ""),
                    best.get("source", ""),
                ),
            ),
            source=best.get("source", "auto_cache"),
            confidence=best.get("confidence", 80),
        )
        return best

    return {
        "title": "Nicht gefunden",
        "author": "-",
        "details": "-",
        "creator": "",
        "publisher": "",
        "year": "",
        "language": "",
        "medium": "",
        "platform": "",
        "manufacturer": "",
        "item_type": "Buch" if is_isbn(code_clean) else "Sonstiges",
        "source": "none",
        "confidence": 0,
    }


# -------------------------------------------------
# Preise
# -------------------------------------------------
def buy_momox(code):
    urls = [
        f"https://www.momox.de/offer/{quote(code)}",
        f"https://www.momox.de/verkaufen/?search={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.01, max_price=300)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def buy_rebuy(code):
    urls = [
        f"https://www.rebuy.de/verkaufen/suchen?query={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.01, max_price=300)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def buy_zoxs(code):
    urls = [
        f"https://www.zoxs.de/ankauf/search?search={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.01, max_price=300)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_booklooker(code):
    urls = [
        f"https://www.booklooker.de/B%C3%BCcher/Angebote/isbn={quote(code)}",
        f"https://www.booklooker.de/Filme/Angebote?keywords={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_medimops(code):
    urls = [
        f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_rebuy(code):
    urls = [
        f"https://www.rebuy.de/kaufen/suchen?q={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_zoxs(code):
    urls = [
        f"https://www.zoxs.de/kaufen/search?search={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_amazon(code):
    urls = [
        f"https://www.amazon.de/s?k={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_min(all_prices), trace


def sell_ebay_active(code):
    urls = [
        f"https://www.ebay.de/sch/i.html?_nkw={quote(code)}&LH_BIN=1",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


def sell_ebay_sold(code):
    urls = [
        f"https://www.ebay.de/sch/i.html?_nkw={quote(code)}&LH_Sold=1&LH_Complete=1",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


def sell_willhaben(code):
    urls = [
        f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


def sell_vinted(code):
    urls = [
        f"https://www.vinted.at/catalog?search_text={quote(code)}",
    ]

    all_prices = []
    trace = []

    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)

    return safe_median(all_prices), trace


# -------------------------------------------------
# API Response
# -------------------------------------------------
def build_lookup_response(code):
    code_clean = clean_code(code)
    info = get_media_info(code_clean)

    jobs = {
        "buy_momox": lambda: buy_momox(code_clean),
        "buy_rebuy": lambda: buy_rebuy(code_clean),
        "buy_zoxs": lambda: buy_zoxs(code_clean),
        "sell_medimops": lambda: sell_medimops(code_clean),
        "sell_rebuy": lambda: sell_rebuy(code_clean),
        "sell_zoxs": lambda: sell_zoxs(code_clean),
        "sell_amazon": lambda: sell_amazon(code_clean),
        "sell_ebay": lambda: sell_ebay_active(code_clean),
        "sell_ebay_sold": lambda: sell_ebay_sold(code_clean),
        "sell_booklooker": lambda: sell_booklooker(code_clean),
        "sell_willhaben": lambda: sell_willhaben(code_clean),
        "sell_vinted": lambda: sell_vinted(code_clean),
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

        except Exception as exc:
            results[name] = None
            debug[name] = {
                "status": "error",
                "error": str(exc),
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

    buy_values_clean = [value for value in buy_values if value is not None]
    sell_values_clean = [value for value in sell_values if value is not None]

    best_buy = max(buy_values_clean) if buy_values_clean else None
    best_sell = max(sell_values_clean) if sell_values_clean else None
    avg_sell = (sum(sell_values_clean) / len(sell_values_clean)) if sell_values_clean else None

    item_type = info.get(
        "item_type",
        detect_item_type(
            code_clean,
            info.get("title", ""),
            info.get("author", ""),
            info.get("source", ""),
        ),
    )

    return {
        "ok": True,
        "version": VERSION,
        "isbn": code_clean,
        "code": code_clean,
        "title": info.get("title", ""),
        "author": info.get("author", info.get("details", "")),
        "details": info.get("details", info.get("author", "")),
        "creator": info.get("creator", ""),
        "publisher": info.get("publisher", ""),
        "year": info.get("year", ""),
        "language": info.get("language", ""),
        "medium": info.get("medium", ""),
        "platform": info.get("platform", ""),
        "manufacturer": info.get("manufacturer", ""),
        "source": info.get("source", "none"),
        "item_type": item_type,
        "confidence": info.get("confidence", 0),
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
    }


# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.route("/")
def home():
    return jsonify(
        {
            "ok": True,
            "status": "Books2Cash API läuft",
            "version": VERSION,
            "hint": "Nutze /isbn/<code>, /lookup/<code>, /learn/<code> oder /cache/<code>",
            "fields": {
                "title": "Titel / Artikelname",
                "author": "Kompatibles Info-Feld für aktuelle Android-App",
                "details": "Ausführliche Info für neue App-Version",
                "creator": "Autor / Regie / Künstler / Entwickler",
                "publisher": "Verlag / Studio / Label / Publisher",
                "medium": "Buch / DVD / Blu-ray / CD / Vinyl",
                "platform": "PS5 / PS4 / Xbox / Switch usw.",
                "manufacturer": "Hersteller",
                "item_type": "Kategorie",
            },
            "features": [
                "kostenlose Quellen",
                "SQLite-Cache",
                "manuelles Lernen über /learn",
                "internationale ISBN-Suche",
                "DVD/Blu-ray/CD/Vinyl/Games via EAN",
                "bereinigte UPCitemdb-Titel",
                "Google Books",
                "OpenLibrary",
                "Crossref",
                "DNB",
                "BnF",
                "UPCitemdb",
                "MusicBrainz",
                "Wikidata",
                "DuckDuckGo-Free-Fallback",
            ],
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


@app.route("/isbn/<code>")
def lookup_isbn_compatible(code):
    return jsonify(build_lookup_response(code))


@app.route("/lookup/<code>")
def lookup_universal(code):
    return jsonify(build_lookup_response(code))


@app.route("/cache/<code>")
def cache_lookup_route(code):
    code_clean = clean_code(code)
    cached = cache_get(code_clean)

    return jsonify(
        {
            "ok": cached is not None,
            "version": VERSION,
            "code": code_clean,
            "cached": cached,
        }
    )


@app.route("/learn/<code>", methods=["GET", "POST"])
def learn_route(code):
    code_clean = clean_code(code)

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = request.args.to_dict()

    title = data.get("title", "").strip()
    author = data.get("author", data.get("info", "-")).strip()
    details = data.get("details", author).strip()
    item_type = data.get("type", data.get("item_type", "Sonstiges")).strip()
    source = data.get("source", "manual_learn").strip()

    creator = data.get("creator", "").strip()
    publisher = data.get("publisher", "").strip()
    year = data.get("year", "").strip()
    language = data.get("language", "").strip()
    medium = data.get("medium", "").strip()
    platform = data.get("platform", "").strip()
    manufacturer = data.get("manufacturer", "").strip()

    try:
        confidence = int(data.get("confidence", 100))
    except Exception:
        confidence = 100

    if not code_clean:
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
                "example": f"/learn/{code_clean}?title=Sex%20and%20the%20City&type=DVD&author=Regie",
            }
        ), 400

    if not details:
        details = author

    if not item_type or item_type == "Sonstiges":
        item_type = detect_item_type(code_clean, title, author + " " + details, source)

    saved = cache_save(
        code=code_clean,
        title=title,
        author=author or details or "-",
        details=details or author or "-",
        creator=creator,
        publisher=publisher,
        year=year,
        language=language,
        medium=medium,
        platform=platform,
        manufacturer=manufacturer,
        item_type=item_type,
        source=source,
        confidence=confidence,
    )

    return jsonify(
        {
            "ok": saved,
            "version": VERSION,
            "code": code_clean,
            "saved": {
                "title": cleanup_title(title),
                "author": author or details or "-",
                "details": details or author or "-",
                "creator": creator,
                "publisher": publisher,
                "year": year,
                "language": language,
                "medium": medium,
                "platform": platform,
                "manufacturer": manufacturer,
                "item_type": item_type,
                "source": source,
                "confidence": confidence,
            },
        }
    )


@app.route("/cache")
def cache_all_route():
    try:
        conn = db_connect()
        rows = conn.execute(
            """
            SELECT code, title, author, details, creator, publisher, year, language,
                   medium, platform, manufacturer, item_type, source, confidence, updated_at
            FROM media_cache
            ORDER BY updated_at DESC
            LIMIT 500
            """
        ).fetchall()
        conn.close()

        items = []

        for row in rows:
            items.append(
                {
                    "code": row["code"],
                    "title": row["title"],
                    "author": row["author"],
                    "details": row["details"],
                    "creator": row["creator"],
                    "publisher": row["publisher"],
                    "year": row["year"],
                    "language": row["language"],
                    "medium": row["medium"],
                    "platform": row["platform"],
                    "manufacturer": row["manufacturer"],
                    "item_type": row["item_type"],
                    "source": row["source"],
                    "confidence": row["confidence"],
                    "updated_at": row["updated_at"],
                }
            )

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

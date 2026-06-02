from flask import Flask, jsonify, request
import os
import re
import time
import sqlite3
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median
from urllib.parse import quote, unquote, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

VERSION = "books2cash_backend_v20_dvd_cache_and_videobuster_verified"
DB_PATH = os.environ.get("BOOKS2CASH_DB_PATH", "books2cash_cache.sqlite3")
TIMEOUT = 7
LOOKUP_TIMEOUT_SECONDS = 12
PRICE_TIMEOUT_SECONDS = 13

HEADERS = {
    "User-Agent": (
        "Books2Cash/20.0 (+https://github.com/georgeasherov-boop/books2cash-api) "
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
    "duckduckgo", "google", "suchergebnisse", "search results", "search", "suche",
    "medimops", "gebrauchte produkte", "online kaufen", "amazon.de", "amazon.com",
    "ebay", "booklooker", "rebuy", "momox", "vinted", "willhaben", "captcha",
    "access denied", "not found", "seite nicht gefunden", "error", "condition very good",
    "condition good", "condition acceptable", "used very good", "warenkorb", "login",
    "products search", "privacy policy", "datenschutz", "cookie",
]

TRUSTED_PRODUCT_DOMAINS = [
    "medimops.de", "rebuy.de", "booklooker.de", "jpc.de", "worldofbooks.com",
    "abebooks.", "fnac.", "lisez.com", "thalia.", "buecher.de", "bol.com",
    "discogs.com", "musicbrainz.org",
    "filminfos.de", "cede.de", "product-search.net", "melando.de", "dvd-palace.de", "ofdb.de", "videobuster.de",
]

KNOWN_ITEMS = {
    "4009750214589": {
        "title": "Sophie & Shiba",
        "details": "Regie: Leif Bristow · Brittany Bristow / John Rhys-Davies / Deborah Kara Unger · DVD · FSK 0 · 104 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
        "verified": True,
    },
    "7321921144790": {
        "title": "Glimmer Man",
        "details": "Regie: John Gray · Steven Seagal / Keenen Ivory Wayans · DVD · USA 1996 · FSK 16 · 88 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
        "verified": True,
    },
    "4260110580984": {
        "title": "Zombie & Vampire Box Collection",
        "details": "DVD-Box · Horror · 2 DVDs · FSK 18 · ca. 521 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
        "verified": True,
    },
    "9006472027690": {
        "title": "Everyday Rebellion",
        "details": "DVD · Dokumentarfilm · Österreich/Schweiz/Deutschland 2013",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
        "verified": True,
    },
    "4010324025463": {
        "title": "Candy",
        "details": "DVD · Deutsche und englische Version",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
        "verified": True,
    },
    "4006680034072": {
        "title": "Three Seasons",
        "details": "Regie: Tony Bui · DVD · Drama · Vietnam/USA 1999 · FSK 12 · 104 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
        "verified": True,
    },
    "4030521307896": {
        "title": "Snatch - Schweine und Diamanten",
        "details": "Guy Ritchie · DVD",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
    "5060018490496": {
        "title": "The Haunted Airman",
        "details": "DVD · Film",
        "item_type": "DVD",
        "source": "known_cache",
        "confidence": 99,
        "verified": True,
    },
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
        "details": "Adam Shankman · DVD",
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


def clean_code(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def is_isbn(code):
    c = clean_code(code)
    return bool(re.fullmatch(r"\d{9}[0-9X]", c) or re.fullmatch(r"97[89]\d{10}", c))


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
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout, allow_redirects=True)
        return {"ok": 200 <= r.status_code < 300, "status": r.status_code, "url": r.url, "text": r.text or ""}
    except Exception as exc:
        return {"ok": False, "status": "exception", "url": url, "text": "", "error": str(exc)}


def html_to_text(html):
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    except Exception:
        return ""


def title_case_small_words(title):
    # Avoid ugly all-titlecase German/English particles coming from catalogue feeds.
    replacements = {
        r"\bUnd\b": "und",
        r"\bDer\b": "Der",
        r"\bDie\b": "Die",
        r"\bDas\b": "Das",
        r"\bThe\b": "The",
        r"\bAnd\b": "and",
        r"\bOf\b": "of",
    }
    out = title
    for pattern, repl in replacements.items():
        out = re.sub(pattern, repl, out)
    return out.strip()


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
    text = re.sub(r"\s+", " ", text).strip(" -|:,.\t\n")
    patterns = [
        r"\s*\|\s*(DVD|Dvd|Blu-ray|Bluray|Blu Ray|CD)\s*\|\s*Condition\s+.*$",
        r"\s*\|\s*Condition\s+.*$",
        r"\s*\|\s*(DVD|Dvd|Blu-ray|Bluray|Blu Ray|CD)\s*$",
        r"\s+-\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox|Fnac|AbeBooks|Google|YouTube|jpc).*$",
        r"\s+\|\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox|Fnac|AbeBooks|Google|YouTube|jpc).*$",
        r"\s+(online kaufen|gebraucht kaufen).*$",
        r"\s+\[(DVD|Blu-ray|CD)\].*$",
        r"\s+\((DVD|Blu-ray|CD)\).*$",
        r"\s*,\s*\d{8,14}$",
        r"\s*\[\s*(?:NON-USA FORMAT|PAL|Reg\.?0|Region|Import|Germany|UK Import|US Import|NTSC).*?\]\s*$",
        r"\s*,?\s*\d+\s*(?:DVD|Dvd|Blu-ray|Blu Ray|Bluray|CD)\s*,\s*(?:Deutsche|Deutsch|Englische|English|Französische|Franzoesische).*?$",
        r"\s*,?\s*\d+\s*(?:DVD|Dvd|Blu-ray|Blu Ray|Bluray|CD)\s*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip(" -|:,;")
    return title_case_small_words(text.strip(" -|:, ."))


def split_title_creator(raw_title):
    raw = str(raw_title or "")
    creator = ""
    match = re.search(r"\bBy\s+([^|]+)", raw, flags=re.IGNORECASE)
    if match:
        creator = cleanup_title(match.group(1))
        raw = raw[: match.start()].strip()
    title = cleanup_title(raw)
    return title, creator


def normalize_duplicate_title(title):
    t = cleanup_title(title)
    # Convert "Candy - Candy" or "Candy - Candy, ..." to "Candy".
    parts = [p.strip(" -|:;,. ") for p in re.split(r"\s+-\s+", t) if p.strip(" -|:;,. ")]
    if len(parts) >= 2:
        first = parts[0]
        second = parts[1]
        second_clean = re.sub(r",.*$", "", second).strip()
        if first.lower() == second_clean.lower():
            return first
    return t


def normalize_title_order(title):
    # Convert catalog form "Haunted Airman The" -> "The Haunted Airman".
    t = normalize_duplicate_title(cleanup_title(title))
    match = re.fullmatch(r"(.+?)\s+(The|A|An|Der|Die|Das|Le|La|Les|Il|Lo|Gli|I|El|Los|Las)", t, flags=re.IGNORECASE)
    if match:
        body = match.group(1).strip(" ,")
        article = match.group(2).strip()
        if len(body.split()) <= 6:
            return cleanup_title(f"{article} {body}")
    return t


def is_bad_title(title):
    t = cleanup_title(title).lower()
    if not t or len(t) < 3:
        return True
    if any(bad in t for bad in BAD_TITLE_PARTS):
        return True
    if re.fullmatch(r"(dvd|blu-ray|bluray|cd|buch|book|film|movie|product|produkt)", t, flags=re.IGNORECASE):
        return True
    if len(t) > 170:
        return True
    return False


def detect_item_type(code, title="", details="", source=""):
    text = f"{code} {title} {details} {source}".lower()
    if is_isbn(code):
        return "Buch"
    if any(x in text for x in ["blu-ray", "bluray", "blu ray", "bd-rom"]):
        return "Blu-ray"
    if any(x in text for x in [" dvd", "dvd ", "dvd-", "| dvd", " film", "movie", "fsk", "regie", "director", "warner", "universal pictures", "paramount", "sony pictures"]):
        return "DVD"
    if any(x in text for x in ["vinyl", "schallplatte", " lp", "gramophone record", "12 inch", "7 inch"]):
        return "Schallplatte"
    if any(x in text for x in ["audio cd", "compact disc", "musicbrainz", "album", "soundtrack", "cd ", "| cd"]):
        return "CD"
    if any(x in text for x in ["playstation", "ps5", "ps4", "xbox", "nintendo switch", "nintendo", "videospiel", "video game"]):
        if any(x in text for x in ["konsole", "console", "controller", "joy-con", "dualsense", "dualshock"]):
            return "Konsole"
        return "Konsolenspiel"
    if any(x in text for x in ["brettspiel", "board game", "gesellschaftsspiel", "ravensburger", "hasbro", "asmodee", "kosmos", "pegasus spiele"]):
        return "Brettspiel"
    if any(x in text for x in ["comic", "manga", "graphic novel"]):
        return "Comic"
    if any(x in text for x in ["pokemon", "pokémon", "trading card", "sammelkarte", "funko", "lego", "collectible"]):
        return "Sammelobjekt"
    return "Sonstiges"


def make_result(title, details="-", source="unknown", item_type=None, confidence=50, verified=False, **extra):
    title = normalize_title_order(cleanup_title(title))
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
        "suggested": bool(extra.get("suggested", False)),
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
    keys = ["title", "author", "details", "item_type", "source", "confidence", "verified", "suggested", "creator", "publisher", "year", "language", "medium", "platform", "manufacturer"]
    return {key: item.get(key, "") for key in keys}


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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(media_cache)").fetchall()}
    for column, definition in {
        "details": "TEXT DEFAULT ''", "creator": "TEXT DEFAULT ''", "publisher": "TEXT DEFAULT ''", "year": "TEXT DEFAULT ''", "language": "TEXT DEFAULT ''", "medium": "TEXT DEFAULT ''", "platform": "TEXT DEFAULT ''", "manufacturer": "TEXT DEFAULT ''"
    }.items():
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
            row["title"], row["details"] or "-", "sqlite_cache", row["item_type"], max(int(row["confidence"] or 90), 90), True,
            creator=row["creator"] or "", publisher=row["publisher"] or "", year=row["year"] or "", language=row["language"] or "", medium=row["medium"] or "", platform=row["platform"] or "", manufacturer=row["manufacturer"] or "",
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
            INSERT INTO media_cache (code,title,details,item_type,source,confidence,creator,publisher,year,language,medium,platform,manufacturer,created_at,updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title=excluded.title, details=excluded.details, item_type=excluded.item_type,
                source=excluded.source, confidence=excluded.confidence, creator=excluded.creator,
                publisher=excluded.publisher, year=excluded.year, language=excluded.language,
                medium=excluded.medium, platform=excluded.platform, manufacturer=excluded.manufacturer,
                updated_at=excluded.updated_at
            """,
            (c, item.get("title", ""), item.get("details") or item.get("author") or "-", item.get("item_type", "Sonstiges"), source or item.get("source", "auto_cache"), int(item.get("confidence", 80)), item.get("creator", ""), item.get("publisher", ""), item.get("year", ""), item.get("language", ""), item.get("medium", ""), item.get("platform", ""), item.get("manufacturer", ""), now, now),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


init_db()


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
                url = f"https://www.googleapis.com/books/v1/volumes?q={quote(query)}&country={country}&maxResults=5"
                data = requests.get(url, headers=JSON_HEADERS, timeout=6).json()
                for item in data.get("items", []) or []:
                    info = item.get("volumeInfo", {}) or {}
                    title = info.get("title", "")
                    authors = ", ".join(info.get("authors", []) or [])
                    publisher = info.get("publisher", "") or ""
                    year = info.get("publishedDate", "") or ""
                    language = (info.get("language", "") or "").upper()
                    details = " · ".join(x for x in [authors, publisher, year, language, "Buch"] if x)
                    result = make_result(title, details, f"google_books_{country}", "Buch", 92, True, creator=authors, publisher=publisher, year=year, language=language, medium="Buch")
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
            url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{quote(code_item)}&format=json&jscmd=data"
            data = requests.get(url, headers=JSON_HEADERS, timeout=6).json()
            item = data.get(f"ISBN:{code_item}")
            if not item:
                continue
            authors = ", ".join(author.get("name", "") for author in item.get("authors", []) if author.get("name"))
            publishers = ", ".join(publisher.get("name", "") for publisher in item.get("publishers", []) if publisher.get("name"))
            year = item.get("publish_date", "") or ""
            details = " · ".join(x for x in [authors, publishers, year, "Buch"] if x)
            result = make_result(item.get("title", ""), details, "openlibrary", "Buch", 88, True, creator=authors, publisher=publishers, year=year, medium="Buch")
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
            title = (item.get("title") or [""])[0]
            creators = []
            for author in item.get("author", []) or []:
                name = f"{author.get('given', '')} {author.get('family', '')}".strip()
                if name:
                    creators.append(name)
            creator = ", ".join(creators)
            publisher = item.get("publisher", "") or ""
            year = ""
            parts = item.get("published-print", {}).get("date-parts") or item.get("published-online", {}).get("date-parts") or []
            if parts and parts[0]:
                year = str(parts[0][0])
            details = " · ".join(x for x in [creator, publisher, year, "Buch"] if x)
            result = make_result(title, details, "crossref", "Buch", 82, True, creator=creator, publisher=publisher, year=year, medium="Buch")
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
        url = f"https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve&query=isbn={quote(c)}&recordSchema=MARC21-xml"
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
                parts = [sub.text.strip() for sub in field.findall("marc:subfield", namespace) if sub.attrib.get("code") in ["a", "b"] and sub.text]
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
        details = " · ".join(x for x in [creator, publisher, year, "Buch"] if x)
        result = make_result(title, details, "dnb", "Buch", 80, True, creator=creator, publisher=publisher, year=year, medium="Buch")
        return [result] if result else []
    except Exception:
        return []


def fetch_bnf(code):
    if not is_isbn(code):
        return []
    try:
        c = clean_code(code)
        url = f"https://catalogue.bnf.fr/api/SRU?version=1.2&operation=searchRetrieve&query=bib.isbn%20all%20%22{quote(c)}%22&maximumRecords=5"
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
            if not creator and (name.endswith("creator") or name.endswith("author")) and tag.get_text(strip=True):
                creator = tag.get_text(" ", strip=True)
        details = " · ".join(x for x in [creator, "FR", "Buch"] if x)
        result = make_result(title, details, "bnf", "Buch", 84, True, creator=creator, language="FR", medium="Buch")
        return [result] if result else []
    except Exception:
        return []


def fetch_musicbrainz(code):
    c = clean_code(code)
    if len(c) < 8 or is_isbn(c):
        return []
    try:
        url = f"https://musicbrainz.org/ws/2/release/?query=barcode:{quote(c)}&fmt=json&limit=5"
        headers = {
            "User-Agent": "Books2Cash/20.0 (github.com/georgeasherov-boop/books2cash-api)",
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
            item_type = "Schallplatte" if any("vinyl" in fmt.lower() for fmt in formats) else "CD"
            creator = ", ".join(artists)
            medium = ", ".join(formats) if formats else item_type
            details = " · ".join(x for x in [creator, medium] if x)
            result = make_result(title, details, "musicbrainz", item_type, 92, True, creator=creator, medium=medium)
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
        sparql = f'''
        SELECT ?item ?itemLabel ?itemDescription WHERE {{
          VALUES ?gtin {{ "{c}" }}
          ?item wdt:P3962 ?gtin.
          SERVICE wikibase:label {{
            bd:serviceParam wikibase:language "de,en,fr,it,tr,ru".
          }}
        }}
        LIMIT 5
        '''
        url = "https://query.wikidata.org/sparql?format=json&query=" + quote(sparql)
        data = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/sparql-results+json"}, timeout=9).json()
        candidates = []
        for row in data.get("results", {}).get("bindings", []) or []:
            title = row.get("itemLabel", {}).get("value", "")
            description = row.get("itemDescription", {}).get("value", "") or "-"
            item_type = detect_item_type(c, title, description, "wikidata")
            result = make_result(title, description, "wikidata_gtin", item_type, 88, True)
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
            title, creator = split_title_creator(raw_title)
            title = normalize_title_order(title)
            brand = item.get("brand", "") or ""
            category = item.get("category", "") or ""
            description = item.get("description", "") or ""
            item_type = detect_item_type(c, raw_title, f"{brand} {category} {description}", "upcitemdb")
            raw_lower = raw_title.lower()
            # UPCitemdb often misclassifies DVDs/Blu-rays through generic shop categories.
            # If the actual title says DVD and not Blu-ray, force DVD.
            if re.search(r"\b(dvd|dvd-rom|d v d)\b", raw_lower) and not re.search(r"blu[ -]?ray|bluray", raw_lower):
                item_type = "DVD"
            bad_category_words = ["dvd & blu-ray players", "vehicles", "vehicle parts", "accessories", "electronics > video > video players"]
            if any(w in category.lower() for w in bad_category_words):
                category = ""
                if brand.lower() in ["colombia", "columbia", "sony pictures home entertainment"]:
                    brand = ""
            medium = item_type if item_type in ["DVD", "Blu-ray", "CD", "Schallplatte"] else ""
            details_parts = []
            if creator:
                details_parts.append(creator)
            if medium:
                details_parts.append(medium)
            if brand and brand.lower() not in ["colombia", "columbia", "sony pictures home entertainment", "media"]:
                details_parts.append(brand)
            # Only keep category for non-media product types where it is actually useful.
            if category and item_type not in ["DVD", "Blu-ray", "CD", "Schallplatte"]:
                details_parts.append(category)
            details = " · ".join(details_parts) if details_parts else (medium or item_type or "-")
            confidence = 76
            raw_lower = raw_title.lower()
            if "condition" in raw_lower:
                confidence -= 8
            if " by " in raw_lower:
                confidence += 4
            if item_type != "Sonstiges":
                confidence += 6
            result = make_result(
                title,
                details,
                "upcitemdb_cleaned",
                item_type,
                confidence,
                False,
                suggested=True,
                creator=creator,
                publisher=brand,
                manufacturer=brand,
                medium=medium,
            )
            if result:
                candidates.append(result)
        return candidates
    except Exception:
        return []


def decode_duckduckgo_url(url):
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = "https://duckduckgo.com" + url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])
    return url


def is_trusted_product_url(url):
    lower = (url or "").lower()
    return any(domain in lower for domain in TRUSTED_PRODUCT_DOMAINS)


def extract_duckduckgo_links(html):
    soup = BeautifulSoup(html or "", "html.parser")
    urls = []
    for link in soup.select("a.result__a"):
        href = decode_duckduckgo_url(link.get("href", ""))
        if href and is_trusted_product_url(href):
            urls.append(href)
    for link in soup.find_all("a"):
        href = decode_duckduckgo_url(link.get("href", ""))
        if href and is_trusted_product_url(href):
            urls.append(href)
    unique = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique[:10]


def extract_meta_title(soup):
    selectors = [
        ("meta", {"property": "og:title"}),
        ("meta", {"name": "og:title"}),
        ("meta", {"name": "twitter:title"}),
        ("meta", {"property": "twitter:title"}),
    ]
    for name, attrs in selectors:
        tag = soup.find(name, attrs=attrs)
        if tag and tag.get("content"):
            return tag.get("content", "")
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    if soup.title and soup.title.string:
        return soup.title.string
    return ""


def parse_product_title_from_page(raw_title, url, page_text):
    raw = cleanup_title(raw_title)
    host = urlparse(url).netloc.lower()
    creator = ""
    medium = detect_item_type("", raw, page_text[:500], host)
    if any(domain in host for domain in ["medimops", "rebuy"]):
        parts = [cleanup_title(part) for part in re.split(r"\s+-\s+", raw) if cleanup_title(part)]
        if len(parts) >= 3 and parts[-1].lower() in ["dvd", "blu-ray", "bluray", "cd"]:
            medium_text = parts[-1]
            creator = parts[0]
            title = " - ".join(parts[1:-1])
            medium = "Blu-ray" if "blu" in medium_text.lower() else "CD" if medium_text.lower() == "cd" else "DVD"
            return normalize_title_order(cleanup_title(title)), creator, medium
        if len(parts) >= 2 and parts[-1].lower() in ["dvd", "blu-ray", "bluray", "cd"]:
            title = " - ".join(parts[:-1])
            medium_text = parts[-1]
            medium = "Blu-ray" if "blu" in medium_text.lower() else "CD" if medium_text.lower() == "cd" else "DVD"
            return normalize_title_order(cleanup_title(title)), creator, medium
    title, creator_by = split_title_creator(raw)
    if creator_by and not creator:
        creator = creator_by
    # Generic DVD/product database title cleanup. Examples:
    # "Three Seasons DVD bei CeDe" -> "Three Seasons"
    # "Three Seasons - Film auf DVD" -> "Three Seasons"
    title = re.sub(r"\s+DVD\s+(bei|auf|kaufen|bestellen).*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+-\s+Film\s+auf\s+(DVD|Blu-ray).*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+auf\s+(DVD|Blu-ray|CD).*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*\((DVD|Blu-ray|Blu Ray|CD)\).*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+-\s+(DVD|Blu-ray|Blu Ray|CD).*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+\|\s+(DVD|Blu-ray|Blu Ray|CD).*$", "", title, flags=re.IGNORECASE).strip()
    return normalize_title_order(cleanup_title(title)), creator, medium


def fetch_product_page_candidate_from_url(code, url):
    c = clean_code(code)
    result = fetch(url, timeout=10)
    if not result["ok"]:
        return None
    html = result["text"]
    text = html_to_text(html)
    merged_digits = re.sub(r"[^0-9Xx]", "", html + " " + text).upper()
    if c not in merged_digits:
        return None
    soup = BeautifulSoup(html, "html.parser")
    raw_title = extract_meta_title(soup)
    title, creator, medium = parse_product_title_from_page(raw_title, result["url"], text)
    if not title or is_bad_title(title):
        return None
    item_type = detect_item_type(c, title, f"{creator} {medium} {text[:1000]}", result["url"])
    if medium and item_type == "Sonstiges":
        item_type = medium
    details_parts = []
    if creator:
        details_parts.append(creator)
    if medium:
        details_parts.append(medium)
    if item_type and item_type not in details_parts and item_type != medium:
        details_parts.append(item_type)
    details = " · ".join(details_parts) if details_parts else item_type
    return make_result(title, details, "trusted_product_page", item_type, 94, True, creator=creator, medium=medium if medium else item_type)


def fetch_trusted_product_pages(code):
    c = clean_code(code)
    if not c:
        return []
    if is_isbn(c):
        queries = [f'"{c}" medimops OR rebuy OR booklooker OR abebooks OR fnac', f'"{c}" book title author']
    else:
        queries = [
            f'"{c}" medimops OR rebuy OR booklooker',
            f'"{c}" DVD OR Blu-ray film',
            f'"{c}" jpc DVD',
            f'"{c}" filminfos DVD',
            f'"{c}" cede DVD',
            f'"{c}" product-search DVD',
            f'"{c}" melando DVD',
            f'"{c}" world of books',
        ]
    urls = []
    for query in queries:
        result = fetch("https://duckduckgo.com/html/?q=" + quote(query), timeout=10)
        if result["ok"]:
            urls.extend(extract_duckduckgo_links(result["text"]))
    unique_urls = []
    for url in urls:
        if url not in unique_urls:
            unique_urls.append(url)
    candidates = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_product_page_candidate_from_url, c, url) for url in unique_urls[:8]]
        try:
            for future in as_completed(futures, timeout=10):
                try:
                    item = future.result()
                    if item:
                        candidates.append(item)
                except Exception:
                    pass
        except Exception:
            pass
    return candidates


def fetch_duckduckgo_weak_candidates(code):
    c = clean_code(code)
    if not c:
        return []
    queries = [f'"{c}" DVD film title', f'"{c}" Blu-ray film', f'"{c}" CD vinyl', f'"{c}" PS5 PS4 Xbox Nintendo Switch']
    if is_isbn(c):
        queries = [f'"{c}" ISBN Buch Autor', f'"{c}" book title author', f'"{c}" livre auteur']
    candidates = []
    for query in queries:
        result = fetch("https://duckduckgo.com/html/?q=" + quote(query), timeout=10)
        if not result["ok"]:
            continue
        soup = BeautifulSoup(result["text"], "html.parser")
        for link in soup.select("a.result__a")[:6]:
            raw_title = link.get_text(" ", strip=True)
            title, creator = split_title_creator(raw_title)
            title = normalize_title_order(title)
            if is_bad_title(title):
                continue
            item_type = detect_item_type(c, title, link.get("href", ""), query)
            details_parts = []
            if creator:
                details_parts.append(creator)
            if item_type != "Sonstiges":
                details_parts.append(item_type)
            details = " · ".join(details_parts) if details_parts else "-"
            candidate = make_result(title, details, "duckduckgo_weak_candidate", item_type, 45 + (10 if item_type != "Sonstiges" else 0), False, suggested=True, creator=creator)
            if candidate:
                candidates.append(candidate)
    return candidates


def canonical_title(title):
    text = cleanup_title(title).lower()
    text = re.sub(r"[^a-z0-9äöüßàâçéèêëîïôûùüÿñæœа-яА-Я]+", " ", text, flags=re.IGNORECASE).strip()
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
            score = min(score, 62)
        if sources <= {"upcitemdb_cleaned"}:
            score = min(score, 86)
        best = dict(best)
        best["confidence"] = min(score, 99)
        best["sources"] = sorted(sources)
        best["verified"] = bool(verified_count >= 1)
        best["suggested"] = not best["verified"]
        ranked.append(best)
    ranked.sort(key=lambda item: int(item.get("confidence", 0)), reverse=True)
    return ranked


def collect_candidates(code):
    c = clean_code(code)
    if not c:
        return []
    if c in KNOWN_ITEMS:
        known = dict(KNOWN_ITEMS[c])
        known.setdefault("details", known.get("author", "-"))
        known.setdefault("author", known.get("details", "-"))
        known.setdefault("suggested", False)
        return [known]
    cached = cache_get(c)
    if cached:
        return [cached]
    if is_isbn(c):
        sources = [fetch_google_books, fetch_openlibrary, fetch_bnf, fetch_crossref, fetch_dnb, fetch_trusted_product_pages]
    else:
        sources = [fetch_musicbrainz, fetch_wikidata_gtin, fetch_trusted_product_pages, fetch_upcitemdb, fetch_duckduckgo_weak_candidates]
    candidates = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(source, c) for source in sources]
        try:
            for future in as_completed(futures, timeout=LOOKUP_TIMEOUT_SECONDS):
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
    found = False
    message = "Kein verlässlicher Treffer gefunden"
    if best:
        item_type = best.get("item_type", "Sonstiges")
        confidence = int(best.get("confidence", 0))
        if best.get("verified") or (confidence >= 58 and item_type != "Sonstiges" and not is_bad_title(best.get("title", ""))):
            found = True
            message = "Treffer gefunden" if best.get("verified") else "Vorschlag gefunden – bitte prüfen"
            if best.get("verified"):
                cache_save(c, best, source=best.get("source", "auto_cache"))
        else:
            best = None
    empty_type = "Buch" if is_isbn(c) else "Sonstiges"
    return {
        "ok": True,
        "version": VERSION,
        "code": c,
        "isbn": c,
        "found": found,
        "verified": bool(best and best.get("verified")),
        "suggested": bool(best and best.get("suggested")),
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


def normalize_price(value):
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").replace("EUR", "€").replace("Euro", "€")
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
    patterns = [r"(\d{1,5}[,.]\d{2})\s*€", r"€\s*(\d{1,5}[,.]\d{2})", r"(\d{1,5}[,.]\d{2})\s*EUR"]
    for pattern in patterns:
        for match in re.finditer(pattern, text.replace("\xa0", " "), flags=re.IGNORECASE):
            number = normalize_price(match.group(1))
            if number is not None:
                prices.append(number)
    return prices


def strict_prices_from_url(url, code, min_price=0.50, max_price=1000):
    result = fetch(url, timeout=10)
    if not result["ok"]:
        return [], {"status": result["status"], "url": url, "reason": "http_error"}
    merged = result["text"] + " " + html_to_text(result["text"])
    page_digits = re.sub(r"[^0-9Xx]", "", merged).upper()
    if clean_code(code) not in page_digits:
        return [], {"status": result["status"], "url": url, "reason": "code_not_found_on_page"}
    prices = [price for price in extract_prices(merged) if min_price <= price <= max_price]
    return prices, {"status": result["status"], "url": url, "reason": "ok" if prices else "no_price"}


PRICE_SOURCES = {
    "buy_momox": ["https://www.momox.de/offer/{code}", "https://www.momox.de/verkaufen/?search={code}"],
    "buy_rebuy": ["https://www.rebuy.de/verkaufen/suchen?query={code}"],
    "buy_zoxs": ["https://www.zoxs.de/ankauf/search?search={code}"],
    "sell_medimops": ["https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={code}"],
    "sell_rebuy": ["https://www.rebuy.de/kaufen/suchen?q={code}"],
    "sell_zoxs": ["https://www.zoxs.de/kaufen/search?search={code}"],
    "sell_amazon": ["https://www.amazon.de/s?k={code}"],
    "sell_ebay": ["https://www.ebay.de/sch/i.html?_nkw={code}&LH_BIN=1"],
    "sell_ebay_sold": ["https://www.ebay.de/sch/i.html?_nkw={code}&LH_Sold=1&LH_Complete=1"],
    "sell_booklooker": ["https://www.booklooker.de/B%C3%BCcher/Angebote/isbn={code}", "https://www.booklooker.de/Filme/Angebote?keywords={code}"],
    "sell_willhaben": ["https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={code}"],
    "sell_vinted": ["https://www.vinted.at/catalog?search_text={code}"],
}


def get_price_source(name, code):
    all_prices = []
    trace = []
    for template in PRICE_SOURCES.get(name, []):
        url = template.format(code=quote(clean_code(code)))
        prices, info = strict_prices_from_url(url, code, min_price=0.01 if name.startswith("buy_") else 0.50, max_price=300 if name.startswith("buy_") else 2000)
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
        futures = {executor.submit(get_price_source, name, c): name for name in PRICE_SOURCES.keys()}
        try:
            for future in as_completed(futures, timeout=PRICE_TIMEOUT_SECONDS):
                name = futures[future]
                try:
                    value, trace = future.result()
                    results[name] = value
                    debug[name] = {"status": "ok" if value is not None else "no_reliable_price", "trace": trace}
                except Exception as exc:
                    results[name] = None
                    debug[name] = {"status": "error", "error": str(exc)}
        except Exception:
            pass
    buy_values = [results.get("buy_momox"), results.get("buy_rebuy"), results.get("buy_zoxs")]
    sell_values = [results.get("sell_medimops"), results.get("sell_rebuy"), results.get("sell_zoxs"), results.get("sell_amazon"), results.get("sell_ebay"), results.get("sell_ebay_sold"), results.get("sell_booklooker"), results.get("sell_willhaben"), results.get("sell_vinted")]
    buy_clean = [value for value in buy_values if value is not None]
    sell_clean = [value for value in sell_values if value is not None]
    return {
        "ok": True,
        "version": VERSION,
        "code": c,
        "ankauf": {"momox": fmt(results.get("buy_momox")), "rebuy": fmt(results.get("buy_rebuy")), "zoxs": fmt(results.get("buy_zoxs")), "1000books": None, "buchmaxe": None},
        "verkauf": {"momox": None, "rebuy": fmt(results.get("sell_rebuy")), "zoxs": fmt(results.get("sell_zoxs")), "1000books": None, "buchmaxe": None, "medimops": fmt(results.get("sell_medimops")), "amazon": fmt(results.get("sell_amazon")), "amazon_new": None, "ebay": fmt(results.get("sell_ebay")), "ebay_sold": fmt(results.get("sell_ebay_sold")), "booklooker": fmt(results.get("sell_booklooker")), "willhaben": fmt(results.get("sell_willhaben")), "vinted": fmt(results.get("sell_vinted"))},
        "analyse": {"best_buy": fmt(max(buy_clean) if buy_clean else None), "best_sell": fmt(max(sell_clean) if sell_clean else None), "avg_sell": fmt(sum(sell_clean) / len(sell_clean) if sell_clean else None)},
        "debug": debug,
    }


def combined_response(code):
    lookup = lookup_product(code)
    prices = get_prices(code)
    response = dict(lookup)
    response.update({"ankauf": prices["ankauf"], "verkauf": prices["verkauf"], "analyse": prices["analyse"], "debug": prices["debug"], "error": None})
    return response


@app.route("/")
def home():
    return jsonify({"ok": True, "version": VERSION, "status": "Books2Cash API läuft", "endpoints": ["/health", "/lookup/<code>", "/prices/<code>", "/isbn/<code>", "/candidates/<code>", "/learn/<code>", "/cache/<code>"], "principle": "V19 ergänzt verifizierte DVD-Cache-Treffer und nutzt weiter bereinigte Medien-/UPC-Resolver."})


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": VERSION, "time": int(time.time()), "cache_db": DB_PATH})


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
    return jsonify({"ok": True, "version": VERSION, "code": c, "accepted": public_candidate(ranked[0]) if ranked else None, "candidates": [public_candidate(item) for item in ranked[:25]]})


@app.route("/learn/<code>", methods=["GET", "POST"])
def learn_route(code):
    c = clean_code(code)
    data = request.get_json(silent=True) or {} if request.method == "POST" else request.args.to_dict()
    title = (data.get("title") or "").strip()
    details = (data.get("details") or data.get("author") or data.get("info") or "-").strip()
    item_type = (data.get("type") or data.get("item_type") or "Sonstiges").strip()
    if not c:
        return jsonify({"ok": False, "version": VERSION, "error": "code fehlt oder ist ungültig"}), 400
    if not title:
        return jsonify({"ok": False, "version": VERSION, "error": "title fehlt", "example": f"/learn/{c}?title=Hairspray&type=DVD&details=Adam%20Shankman%20%C2%B7%20DVD"}), 400
    if item_type == "Sonstiges":
        item_type = detect_item_type(c, title, details, "manual")
    item = make_result(title, details, "manual_learn", item_type, 100, True, creator=data.get("creator", ""), publisher=data.get("publisher", ""), year=data.get("year", ""), language=data.get("language", ""), medium=data.get("medium", ""), platform=data.get("platform", ""), manufacturer=data.get("manufacturer", ""))
    saved = cache_save(c, item, "manual_learn")
    return jsonify({"ok": saved, "version": VERSION, "code": c, "saved": public_candidate(item)})


@app.route("/cache/<code>")
def cache_code_route(code):
    c = clean_code(code)
    cached = cache_get(c)
    return jsonify({"ok": cached is not None, "version": VERSION, "code": c, "cached": public_candidate(cached) if cached else None})


@app.route("/cache")
def cache_all_route():
    try:
        conn = db_connect()
        rows = conn.execute("SELECT code, title, details, item_type, source, confidence, updated_at FROM media_cache ORDER BY updated_at DESC LIMIT 500").fetchall()
        conn.close()
        items = [dict(row) for row in rows]
        return jsonify({"ok": True, "version": VERSION, "count": len(items), "items": items})
    except Exception as exc:
        return jsonify({"ok": False, "version": VERSION, "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

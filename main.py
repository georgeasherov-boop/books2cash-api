from flask import Flask, jsonify
import json
import re
import time
import xml.etree.ElementTree as ET
from statistics import median
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8,fr;q=0.7,it;q=0.6,tr;q=0.5,ru;q=0.4",
}

TIMEOUT = 8

GENERIC_TITLES = [
    "medimops", "gebrauchte produkte", "online kaufen", "amazon.de", "ebay", "willhaben",
    "booklooker", "rebuy", "momox", "vinted", "google", "captcha", "access denied",
    "seite nicht gefunden", "not found", "error", "search", "suche", "suchergebnisse",
]

KNOWN_ITEMS = {
    "4049834002961": {
        "title": "Love and Other Disasters",
        "author": "Alek Keshishian / Brittany Murphy / Matthew Rhys",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "4042564128512": {
        "title": "Der Duft der grünen Papaya",
        "author": "Regie: Tran Anh Hung · DVD · Frankreich 1993 · FSK 6 · ca. 100 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "7321925014167": {
        "title": "Sex and the City – Der Film",
        "author": "Regie: Michael Patrick King · DVD · FSK 12 · 139 Minuten",
        "item_type": "DVD",
        "source": "known_dvd_cache",
        "confidence": 99,
    },
    "9782266353267": {
        "title": "Les Assassins de l'aube",
        "author": "Michel Bussi · Pocket · Französisches Buch · ISBN-10: 2266353268",
        "item_type": "Buch",
        "source": "known_book_cache",
        "confidence": 99,
    },
}


def clean_code(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def is_isbn(code):
    code = clean_code(code)
    return bool(re.fullmatch(r"\d{9}[0-9X]", code) or re.fullmatch(r"97[89]\d{10}", code))


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


def normalize_price(value):
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").replace("EUR", "€").replace("Euro", "€").strip()
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
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout, allow_redirects=True)
        return {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "url": r.url,
            "text": r.text or "",
        }
    except Exception as e:
        return {"ok": False, "status": "exception", "url": url, "text": "", "error": str(e)}


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
    return clean_code(code) in re.sub(r"[^0-9Xx]", "", text).upper()


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
    return min(values) if values else None


def safe_median(values):
    values = filter_prices(values)
    return median(values) if values else None


def get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000):
    result = fetch(url)
    if not result["ok"]:
        return [], {"status": result["status"], "reason": "http_error", "url": url}
    html = result["text"]
    text = html_to_text(html)
    merged = html + " " + text
    if not page_contains_code(merged, code):
        return [], {"status": result["status"], "reason": "code_not_found_on_page", "url": url}
    prices = filter_prices(extract_prices(merged), min_price, max_price)
    if not prices:
        return [], {"status": result["status"], "reason": "no_price_found", "url": url}
    return prices, {"status": result["status"], "reason": "ok", "url": url}


def is_generic_title(title):
    t = (title or "").strip().lower()
    if not t or len(t) < 3:
        return True
    return any(bad in t for bad in GENERIC_TITLES)


def cleanup_title(title):
    title = unquote(str(title or ""))
    title = BeautifulSoup(title, "html.parser").get_text(" ", strip=True)
    title = title.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    title = re.sub(r"\s+", " ", title).strip(" -|:;,.\n\t")
    title = re.sub(r"\b(DVD|Blu-ray|Bluray|CD|Book|Buch)\b\s*(online kaufen|gebraucht kaufen)?\s*$", "", title, flags=re.I).strip(" -|:;,")
    title = re.sub(r"\s+-\s+(Amazon|eBay|medimops|reBuy|Booklooker|momox).*$", "", title, flags=re.I).strip()
    return title


def detect_item_type(code, title="", author="", source=""):
    text = f"{code} {title} {author} {source}".lower()
    if is_isbn(code):
        return "Buch"
    if any(x in text for x in ["blu-ray", "bluray", "blu ray", "bd-rom"]):
        return "Blu-ray"
    if any(x in text for x in [" dvd", "dvd ", "film", "movie", "fsk", "regie", "director", "warner", "universal pictures"]):
        return "DVD"
    if any(x in text for x in ["vinyl", "schallplatte", " lp", "gramophone record"]):
        return "Schallplatte"
    if any(x in text for x in ["audio cd", "compact disc", "musicbrainz", "album", "soundtrack"]):
        return "CD"
    if any(x in text for x in ["playstation", "ps5", "ps4", "xbox", "nintendo switch", "videospiel", "video game"]):
        if any(x in text for x in ["konsole", "console", "controller", "joy-con", "dualsense"]):
            return "Konsole"
        return "Konsolenspiel"
    if any(x in text for x in ["brettspiel", "board game", "gesellschaftsspiel", "ravensburger", "hasbro", "asmodee", "kosmos"]):
        return "Brettspiel"
    if any(x in text for x in ["comic", "manga", "graphic novel"]):
        return "Comic"
    if any(x in text for x in ["pokemon", "pokémon", "trading card", "sammelkarte", "funko", "lego", "collectible"]):
        return "Sammelobjekt"
    return "Sonstiges"


def make_result(title, author="-", source="unknown", item_type=None, confidence=50):
    title = cleanup_title(title)
    if not title or is_generic_title(title):
        return None
    item_type = item_type or detect_item_type("", title, author, source)
    return {
        "title": title,
        "author": author or "-",
        "source": source,
        "item_type": item_type,
        "confidence": confidence,
    }


# -------------------------------------------------
# Buchdaten international
# -------------------------------------------------
def fetch_google_books(code):
    try:
        queries = []
        c = clean_code(code)
        queries.append(f"isbn:{c}")
        isbn10 = isbn13_to_isbn10(c)
        if isbn10:
            queries.append(f"isbn:{isbn10}")
        countries = ["DE", "US", "GB", "FR", "IT", "TR", "RU"]
        best = None
        for query in queries:
            for country in countries:
                url = f"https://www.googleapis.com/books/v1/volumes?q={quote(query)}&country={country}&maxResults=5"
                r = requests.get(url, headers=HEADERS, timeout=6)
                data = r.json()
                for item in data.get("items", []) or []:
                    info = item.get("volumeInfo", {}) or {}
                    title = cleanup_title(info.get("title", ""))
                    if not title:
                        continue
                    authors = info.get("authors", []) or []
                    publisher = info.get("publisher", "") or ""
                    published = info.get("publishedDate", "") or ""
                    author = ", ".join(authors).strip() if authors else "-"
                    if publisher or published:
                        author = f"{author} · {publisher} {published}".strip(" ·")
                    result = make_result(title, author, f"google_books_{country}", "Buch", 88)
                    if result:
                        best = result
                        return best
    except Exception:
        pass
    return None


def fetch_openlibrary(code):
    try:
        codes = [clean_code(code)]
        isbn10 = isbn13_to_isbn10(code)
        if isbn10:
            codes.append(isbn10)
        for c in codes:
            url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{quote(c)}&format=json&jscmd=data"
            r = requests.get(url, headers=HEADERS, timeout=6)
            data = r.json()
            item = data.get(f"ISBN:{c}")
            if not item:
                continue
            title = cleanup_title(item.get("title", ""))
            if not title:
                continue
            authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
            publishers = [p.get("name", "") for p in item.get("publishers", []) if p.get("name")]
            author = ", ".join(authors) if authors else "-"
            if publishers or item.get("publish_date"):
                author = f"{author} · {', '.join(publishers)} {item.get('publish_date', '')}".strip(" ·")
            return make_result(title, author, "openlibrary", "Buch", 84)
    except Exception:
        pass
    return None


def fetch_crossref(code):
    try:
        c = clean_code(code)
        url = f"https://api.crossref.org/works?filter=isbn:{quote(c)}&rows=3"
        r = requests.get(url, headers=HEADERS, timeout=6)
        data = r.json()
        items = data.get("message", {}).get("items", []) or []
        if not items:
            return None
        item = items[0]
        titles = item.get("title") or []
        title = cleanup_title(titles[0] if titles else "")
        if not title:
            return None
        authors = []
        for a in item.get("author", []) or []:
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)
        publisher = item.get("publisher", "") or ""
        year = ""
        parts = item.get("published-print", {}).get("date-parts") or item.get("published-online", {}).get("date-parts") or []
        if parts and parts[0]:
            year = str(parts[0][0])
        author = ", ".join(authors) if authors else "-"
        if publisher or year:
            author = f"{author} · {publisher} {year}".strip(" ·")
        return make_result(title, author, "crossref", "Buch", 78)
    except Exception:
        return None


def fetch_dnb(code):
    try:
        c = clean_code(code)
        url = (
            "https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve"
            f"&query=isbn={quote(c)}&recordSchema=MARC21-xml"
        )
        r = requests.get(url, headers=HEADERS, timeout=6)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
        ns = {"marc": "http://www.loc.gov/MARC21/slim"}
        title = None
        author = None
        for field in root.findall(".//marc:datafield", ns):
            tag = field.attrib.get("tag")
            if tag == "245":
                parts = []
                for sub in field.findall("marc:subfield", ns):
                    if sub.attrib.get("code") in ["a", "b"] and sub.text:
                        parts.append(sub.text.strip())
                if parts:
                    title = cleanup_title(" ".join(parts).strip(" /:"))
            if tag == "100":
                for sub in field.findall("marc:subfield", ns):
                    if sub.attrib.get("code") == "a" and sub.text:
                        author = sub.text.strip(" ,")
        if not title:
            return None
        return make_result(title, author or "-", "dnb", "Buch", 75)
    except Exception:
        return None


def fetch_bnf(code):
    try:
        c = clean_code(code)
        url = (
            "https://catalogue.bnf.fr/api/SRU?version=1.2&operation=searchRetrieve"
            f"&query=bib.isbn%20all%20%22{quote(c)}%22&maximumRecords=5"
        )
        r = requests.get(url, headers=HEADERS, timeout=7)
        if r.status_code != 200:
            return None
        text = html_to_text(r.text)
        if not page_contains_code(r.text + " " + text, c):
            return None
        soup = BeautifulSoup(r.text, "xml")
        title = None
        author = "-"
        for tag in soup.find_all():
            name = tag.name.lower()
            if name.endswith("title") and tag.get_text(strip=True):
                title = cleanup_title(tag.get_text(" ", strip=True))
                break
        for tag in soup.find_all():
            name = tag.name.lower()
            if (name.endswith("creator") or name.endswith("author")) and tag.get_text(strip=True):
                author = tag.get_text(" ", strip=True)
                break
        if not title:
            return None
        return make_result(title, author, "bnf_france", "Buch", 82)
    except Exception:
        return None


def get_book_info(code):
    if clean_code(code) in KNOWN_ITEMS:
        return KNOWN_ITEMS[clean_code(code)]
    for fn in [fetch_google_books, fetch_openlibrary, fetch_bnf, fetch_crossref, fetch_dnb]:
        result = fn(code)
        if result:
            return result
    return None


# -------------------------------------------------
# Produkt/Medien-Daten
# -------------------------------------------------
def fetch_upcitemdb(code):
    try:
        c = clean_code(code)
        if len(c) < 8:
            return None
        url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={quote(c)}"
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"}, timeout=8)
        data = r.json()
        items = data.get("items", []) or []
        if not items:
            return None
        item = items[0]
        title = cleanup_title(item.get("title", ""))
        brand = item.get("brand", "") or ""
        category = item.get("category", "") or ""
        description = item.get("description", "") or ""
        if not title or is_generic_title(title):
            return None
        item_type = detect_item_type(c, title, f"{brand} {category} {description}", "upcitemdb")
        author = " · ".join([x for x in [brand, category] if x]) or description[:160] or "-"
        if "dvd & blu-ray players" in author.lower():
            author = brand or "-"
        return make_result(title, author, "upcitemdb", item_type, 76)
    except Exception:
        return None


def fetch_musicbrainz(code):
    try:
        c = clean_code(code)
        url = f"https://musicbrainz.org/ws/2/release/?query=barcode:{quote(c)}&fmt=json&limit=5"
        r = requests.get(url, headers={"User-Agent": "Books2Cash/1.0 (contact: github.com/georgeasherov-boop/books2cash-api)", "Accept": "application/json"}, timeout=8)
        data = r.json()
        releases = data.get("releases", []) or []
        if not releases:
            return None
        release = releases[0]
        title = cleanup_title(release.get("title", ""))
        artists = []
        for credit in release.get("artist-credit", []) or []:
            name = credit.get("name") or (credit.get("artist") or {}).get("name")
            if name:
                artists.append(name)
        formats = []
        for media in release.get("media", []) or []:
            if media.get("format"):
                formats.append(media.get("format"))
        item_type = "Schallplatte" if any("vinyl" in f.lower() for f in formats) else "CD"
        author = ", ".join(artists) if artists else "-"
        if formats:
            author = f"{author} · {', '.join(formats)}".strip(" ·")
        return make_result(title, author, "musicbrainz", item_type, 86)
    except Exception:
        return None


def fetch_wikidata_gtin(code):
    try:
        c = clean_code(code)
        sparql = f'''
        SELECT ?item ?itemLabel ?itemDescription WHERE {{
          VALUES ?gtin {{ "{c}" }}
          ?item wdt:P3962 ?gtin.
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "de,en,fr,it,tr,ru". }}
        }} LIMIT 5
        '''
        url = "https://query.wikidata.org/sparql?format=json&query=" + quote(sparql)
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/sparql-results+json"}, timeout=9)
        data = r.json()
        rows = data.get("results", {}).get("bindings", []) or []
        if not rows:
            return None
        row = rows[0]
        title = cleanup_title(row.get("itemLabel", {}).get("value", ""))
        desc = row.get("itemDescription", {}).get("value", "") or "-"
        item_type = detect_item_type(c, title, desc, "wikidata gtin")
        return make_result(title, desc, "wikidata_gtin", item_type, 74)
    except Exception:
        return None


def extract_search_results_from_duckduckgo(html):
    soup = BeautifulSoup(html or "", "html.parser")
    results = []
    for a in soup.select("a.result__a"):
        title = cleanup_title(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if title and not is_generic_title(title):
            results.append({"title": title, "url": href, "snippet": ""})
    if results:
        return results[:10]
    for a in soup.find_all("a"):
        text = cleanup_title(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if len(text) > 8 and not is_generic_title(text) and href:
            results.append({"title": text, "url": href, "snippet": ""})
    return results[:10]


def clean_search_title(raw_title, code):
    title = cleanup_title(raw_title)
    title = re.sub(re.escape(clean_code(code)), "", title, flags=re.I).strip(" -|:;,.")
    remove_patterns = [
        r"\s*\|\s*.*$",
        r"\s+-\s+(DVD|Blu-ray|CD|Buch|Book|Amazon|eBay|medimops|reBuy|Booklooker).*$",
        r"\s+online kaufen.*$",
        r"\s+gebraucht kaufen.*$",
        r"\s+\(DVD\).*$",
        r"\s+\[DVD\].*$",
    ]
    for p in remove_patterns:
        title = re.sub(p, "", title, flags=re.I).strip(" -|:;,")
    return cleanup_title(title)


def fetch_web_search_product(code):
    c = clean_code(code)
    if not c:
        return None
    queries = []
    if is_isbn(c):
        queries = [
            f'"{c}" ISBN book',
            f'"{c}" livre',
            f'"{c}" libro',
            f'"{c}" kitap',
            f'"{c}" книга',
        ]
    else:
        queries = [
            f'"{c}" DVD OR Blu-ray film',
            f'"{c}" reBuy medimops Booklooker',
            f'"{c}" PS5 OR PS4 OR Xbox OR Nintendo Switch game',
            f'"{c}" CD vinyl Discogs MusicBrainz',
        ]
    candidates = []
    for q in queries:
        url = "https://duckduckgo.com/html/?q=" + quote(q)
        result = fetch(url, timeout=10)
        if not result["ok"]:
            continue
        for row in extract_search_results_from_duckduckgo(result["text"]):
            title = clean_search_title(row["title"], c)
            if not title or is_generic_title(title):
                continue
            source_text = f"duckduckgo {q} {row.get('url', '')} {row.get('snippet', '')}"
            item_type = detect_item_type(c, title, row.get("snippet", ""), source_text)
            score = 62
            if c in row["title"] or c in row.get("snippet", ""):
                score += 8
            if item_type != "Sonstiges":
                score += 8
            candidates.append(make_result(title, row.get("snippet", "-") or "-", f"web_search_{item_type.lower()}", item_type, score))
    candidates = [x for x in candidates if x]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return candidates[0]


def get_media_info(code):
    c = clean_code(code)
    if c in KNOWN_ITEMS:
        return KNOWN_ITEMS[c]
    if is_isbn(c):
        book = get_book_info(c)
        if book:
            return book
    for fn in [fetch_upcitemdb, fetch_musicbrainz, fetch_wikidata_gtin, fetch_web_search_product]:
        result = fn(c)
        if result:
            return result
    if is_isbn(c):
        return {"title": "Nicht gefunden", "author": "-", "source": "none", "item_type": "Buch", "confidence": 0}
    return {"title": "Nicht gefunden", "author": "-", "source": "none", "item_type": "Sonstiges", "confidence": 0}


# -------------------------------------------------
# Preise
# -------------------------------------------------
def buy_momox(code):
    urls = [f"https://www.momox.de/offer/{quote(code)}", f"https://www.momox.de/verkaufen/?search={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.01, max_price=300)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def buy_rebuy(code):
    urls = [f"https://www.rebuy.de/verkaufen/suchen?query={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.01, max_price=300)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def buy_zoxs(code):
    urls = [f"https://www.zoxs.de/ankauf/search?search={quote(code)}"]
    all_prices, trace = [], []
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
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def sell_medimops(code):
    urls = [f"https://www.medimops.de/produkte-C0/?fcIsSearch=1&searchparam={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def sell_rebuy(code):
    urls = [f"https://www.rebuy.de/kaufen/suchen?q={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def sell_zoxs(code):
    urls = [f"https://www.zoxs.de/kaufen/search?search={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def sell_amazon(code):
    urls = [f"https://www.amazon.de/s?k={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_min(all_prices), trace


def sell_ebay_active(code):
    urls = [f"https://www.ebay.de/sch/i.html?_nkw={quote(code)}&LH_BIN=1"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_median(all_prices), trace


def sell_ebay_sold(code):
    urls = [f"https://www.ebay.de/sch/i.html?_nkw={quote(code)}&LH_Sold=1&LH_Complete=1"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_median(all_prices), trace


def sell_willhaben(code):
    urls = [f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=2000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_median(all_prices), trace


def sell_vinted(code):
    urls = [f"https://www.vinted.at/catalog?search_text={quote(code)}"]
    all_prices, trace = [], []
    for url in urls:
        prices, info = get_strict_prices_from_url(url, code, min_price=0.50, max_price=1000)
        all_prices.extend(prices)
        trace.append(info)
    return safe_median(all_prices), trace


# -------------------------------------------------
# API
# -------------------------------------------------
def build_lookup_response(code):
    code = clean_code(code)
    info = get_media_info(code)

    jobs = {
        "buy_momox": lambda: buy_momox(code),
        "buy_rebuy": lambda: buy_rebuy(code),
        "buy_zoxs": lambda: buy_zoxs(code),
        "sell_medimops": lambda: sell_medimops(code),
        "sell_rebuy": lambda: sell_rebuy(code),
        "sell_zoxs": lambda: sell_zoxs(code),
        "sell_amazon": lambda: sell_amazon(code),
        "sell_ebay": lambda: sell_ebay_active(code),
        "sell_ebay_sold": lambda: sell_ebay_sold(code),
        "sell_booklooker": lambda: sell_booklooker(code),
        "sell_willhaben": lambda: sell_willhaben(code),
        "sell_vinted": lambda: sell_vinted(code),
    }

    results = {}
    debug = {}
    for name, fn in jobs.items():
        try:
            value, trace = fn()
            results[name] = value
            debug[name] = {"status": "ok" if value is not None else "no_reliable_price", "trace": trace}
        except Exception as e:
            results[name] = None
            debug[name] = {"status": "error", "error": str(e)}

    buy_values = [results.get("buy_momox"), results.get("buy_rebuy"), results.get("buy_zoxs")]
    sell_values = [
        results.get("sell_medimops"), results.get("sell_rebuy"), results.get("sell_zoxs"),
        results.get("sell_amazon"), results.get("sell_ebay"), results.get("sell_ebay_sold"),
        results.get("sell_booklooker"), results.get("sell_willhaben"), results.get("sell_vinted"),
    ]
    buy_values_clean = [v for v in buy_values if v is not None]
    sell_values_clean = [v for v in sell_values if v is not None]
    best_buy = max(buy_values_clean) if buy_values_clean else None
    best_sell = max(sell_values_clean) if sell_values_clean else None
    avg_sell = (sum(sell_values_clean) / len(sell_values_clean)) if sell_values_clean else None

    return {
        "ok": True,
        "isbn": code,
        "code": code,
        "title": info.get("title", ""),
        "author": info.get("author", ""),
        "source": info.get("source", "none"),
        "item_type": info.get("item_type", detect_item_type(code, info.get("title", ""), info.get("author", ""), info.get("source", ""))),
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


@app.route("/")
def home():
    return jsonify({
        "status": "Books2Cash API läuft",
        "version": "media_lookup_backend_v9_international",
        "hint": "Nutze /isbn/<code> oder /lookup/<code>",
        "features": [
            "internationale ISBN-Suche", "DVD/Blu-ray/CD/Vinyl/Games via EAN",
            "UPCitemdb", "MusicBrainz", "Wikidata", "DuckDuckGo-Web-Fallback",
            "strikte Preisübernahme nur wenn Code auf Quellseite vorkommt",
        ],
    })


@app.route("/isbn/<code>")
def lookup_isbn_compatible(code):
    return jsonify(build_lookup_response(code))


@app.route("/lookup/<code>")
def lookup_universal(code):
    return jsonify(build_lookup_response(code))


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": int(time.time())})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

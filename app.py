#!/usr/bin/env python3
import base64
import concurrent.futures
import io
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from html import escape, unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
EXPORT_DIR = ROOT / "exports"
PORT = 8787
MAX_HTML_BYTES = 5_000_000
MAX_DETAIL_PAGES = 250
MAX_SALLA_PRODUCTS = 500
MAX_SALLA_CATEGORIES = 120
MAX_SALLA_TOTAL_ROWS = 5000
SALLA_DETAIL_WORKERS = 6
MAX_SHOPIFY_PRODUCTS = 5000
MAX_SHOPIFY_COLLECTIONS = 1000
SHOPIFY_PAGE_SIZE = 250
DETAIL_DELAY_SECONDS = 0.15
SALLA_API_BASE = "https://api.salla.dev/store/v1"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
SSL_FALLBACK_WARNING_RE = re.compile(
    r"The HTTPS certificate could not be verified by this Python installation,?\s*"
    r"so the page was fetched with relaxed certificate checking\.?",
    re.I,
)


PRICE_RE = re.compile(
    r"(?:(?:USD|EUR|GBP|AED|SAR|QAR|KWD|OMR|BHD|EGP|INR|PKR|CAD|AUD)\s*)?"
    r"(?:[$€£₹]|د\.إ|ر\.س|ريال|AED|SAR)?\s*"
    r"\d{1,3}(?:[,\s]\d{3})*(?:[.,]\d{2})?"
    r"\s*(?:USD|EUR|GBP|AED|SAR|QAR|KWD|OMR|BHD|EGP|INR|PKR|CAD|AUD|ريال)?",
    re.I,
)
CARD_HINT_RE = re.compile(
    r"(product|listing|item|card|result|tile|offer|property|vehicle|grid|catalog|search)",
    re.I,
)
NOISE_RE = re.compile(r"(header|footer|nav|menu|breadcrumb|pagination|modal|cookie|banner)", re.I)
CANDIDATE_TAGS = {"article", "li", "div", "section", "a", "tr"}
MODEL_LABEL_RE = re.compile(
    r"\b(?:model(?:\s*(?:no\.?|number|#|code))?|item\s*(?:no\.?|number|#)|part\s*(?:no\.?|number|#)|sku|mpn)\b"
    r"\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/\- ]{1,60})",
    re.I,
)
DETAIL_KEYS = {
    "brand": ["brand", "manufacturer"],
    "sku": ["sku"],
    "mpn": ["mpn"],
    "model_number": ["model", "modelNumber", "productID", "sku", "mpn"],
    "availability": ["availability"],
}
SALLA_LIST_INCLUDES = ["images", "metadata"]
JOBS = {}
JOBS_LOCK = threading.Lock()


class Node:
    def __init__(self, tag="document", attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.parent = parent
        self.children = []
        self.text_parts = []

    def attr(self, name, default=""):
        return self.attrs.get(name, default) or ""

    def text(self):
        parts = list(self.text_parts)
        for child in self.children:
            value = child.text()
            if value:
                parts.append(value)
        return clean_text(" ".join(parts))


class SiteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.title = ""
        self._in_title = False
        self._current_script = None
        self.meta = []
        self.images = []
        self.links = []
        self.structured = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack.append(node)

        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = attrs_dict.get("name") or attrs_dict.get("property") or attrs_dict.get("itemprop")
            content = attrs_dict.get("content")
            if name and content:
                self.meta.append({"name": name, "content": clean_text(content)})
        elif tag == "img":
            src = image_src_from_attrs(attrs_dict)
            if src:
                self.images.append(
                    {
                        "src": src,
                        "alt": clean_text(attrs_dict.get("alt", "")),
                        "title": clean_text(attrs_dict.get("title", "")),
                    }
                )
        elif tag == "a":
            href = attrs_dict.get("href")
            if href:
                self.links.append({"href": href, "text": ""})
        elif tag == "script":
            script_type = attrs_dict.get("type", "").lower()
            if "ld+json" in script_type:
                self._current_script = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._current_script is not None:
            raw = "".join(self._current_script).strip()
            if raw:
                self.structured.extend(parse_json_ld(raw))
            self._current_script = None
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        if self._current_script is not None:
            self._current_script.append(data)
            return
        value = clean_text(data)
        if not value:
            return
        self.stack[-1].text_parts.append(value)
        if self._in_title:
            self.title = clean_text(f"{self.title} {value}")
        if self.stack[-1].tag == "a" and self.links:
            self.links[-1]["text"] = clean_text(f"{self.links[-1]['text']} {value}")


def update_job(job_id, **updates):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def create_scrape_job(url, mode="auto"):
    if mode not in {"auto", "website", "category"}:
        raise ValueError("Invalid scrape mode.")
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "message": "Queued",
            "url": url,
            "mode": mode,
            "created_at": time.time(),
            "updated_at": time.time(),
            "counts": {},
        }

    def run():
        def progress(message, counts=None):
            update_job(job_id, status="running", message=message, counts=counts or {})

        try:
            progress("Starting scrape...")
            data = extract_site_data(url, progress=progress, mode=mode)
            update_job(job_id, status="complete", message="Scrape complete.", data=data, counts=data.get("counts", {}))
        except Exception as exc:
            update_job(job_id, status="error", message=str(exc), error=str(exc))

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        public = dict(job)
    return public


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def sanitize_warning(value):
    return clean_text(SSL_FALLBACK_WARNING_RE.sub("", value or "").replace("Note: .", ""))


def first_src_from_srcset(value):
    best_url = ""
    best_score = -1.0
    for item in (value or "").split(","):
        parts = item.strip().split()
        if not parts:
            continue
        url = parts[0]
        score = 0.0
        if len(parts) > 1:
            descriptor = parts[1].lower()
            try:
                if descriptor.endswith("w"):
                    score = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    score = float(descriptor[:-1]) * 1000
            except ValueError:
                score = 0.0
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def image_src_from_attrs(attrs):
    for key in ("src", "data-src", "data-original", "data-lazy", "data-image", "data-url"):
        if attrs.get(key):
            return attrs[key]
    for key in ("srcset", "data-srcset"):
        src = first_src_from_srcset(attrs.get(key))
        if src:
            return src
    return ""


def html_to_text(value):
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", " ", value or "")
    value = re.sub(r"(?i)</\s*(p|li|div|h[1-6]|tr|dt|dd)\s*>", " ", value)
    return clean_text(unescape(re.sub(r"<[^>]+>", " ", value)))


def parse_json_ld(raw):
    blocks = []
    candidates = [raw]
    if "}</script>" in raw:
        candidates = raw.split("</script>")
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            blocks.extend(data)
        else:
            blocks.append(data)
    return blocks


def structured_objects(value):
    if isinstance(value, dict):
        yield value
        for key in ("@graph", "itemListElement", "mainEntity", "offers"):
            if key in value:
                yield from structured_objects(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from structured_objects(item)


def read_response(request, context):
    with urllib.request.urlopen(request, timeout=25, context=context) as response:
        content_type = response.headers.get("Content-Type", "")
        html_bytes = response.read(MAX_HTML_BYTES + 1)
        if len(html_bytes) > MAX_HTML_BYTES:
            raise ValueError("The page is larger than the current 5 MB safety limit.")
        encoding = response.headers.get_content_charset() or "utf-8"
    return html_bytes.decode(encoding, errors="replace"), content_type


def is_local_issuer_error(exc):
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) and "local issuer certificate" in str(reason).lower()


def fetch_url(url):
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Please enter a valid http or https website link.")
    normalized = urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/%"),
            parsed.params,
            urllib.parse.quote(urllib.parse.unquote(parsed.query), safe="=&%[]:,/+"),
            parsed.fragment,
        )
    )
    request = urllib.request.Request(
        normalized,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    warning = ""
    try:
        html, content_type = read_response(request, ssl.create_default_context())
    except urllib.error.URLError as exc:
        if parsed.scheme != "https" or not is_local_issuer_error(exc):
            raise
        warning = ""
        html, content_type = read_response(request, ssl._create_unverified_context())
    return normalized, html, content_type, warning


def fetch_json(url, headers=None):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        if not is_local_issuer_error(exc):
            raise
        with urllib.request.urlopen(request, timeout=25, context=ssl._create_unverified_context()) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))


def absolutize(base_url, maybe_url):
    if not maybe_url:
        return ""
    return urllib.parse.urljoin(base_url, maybe_url)


def node_score(node):
    if node.tag not in CANDIDATE_TAGS:
        return -10
    identity = " ".join([node.tag, node.attr("class"), node.attr("id"), node.attr("itemtype")])
    score = 0
    if node.tag in {"article", "li"}:
        score += 2
    if CARD_HINT_RE.search(identity):
        score += 4
    if NOISE_RE.search(identity):
        score -= 5
    text = node.text()
    if PRICE_RE.search(text):
        score += 3
    if find_first(node, "img"):
        score += 2
    if find_first(node, "a"):
        score += 1
    text_len = len(text)
    if 30 <= text_len <= 900:
        score += 2
    if text_len > 1600:
        score -= 4
    return score


def find_first(node, tag):
    if node.tag == tag:
        return node
    for child in node.children:
        found = find_first(child, tag)
        if found:
            return found
    return None


def find_heading_text(node):
    for tag in ["h1", "h2", "h3", "h4", "h5", "a"]:
        found = find_first(node, tag)
        if found:
            text = found.text()
            if text:
                return text[:240]
    text = node.text()
    price = PRICE_RE.search(text)
    if price:
        text = text.replace(price.group(0), "")
    return clean_text(text)[:240]


def first_image(node, base_url):
    img = find_first(node, "img")
    if not img:
        return "", ""
    src = image_src_from_attrs(img.attrs)
    return absolutize(base_url, src), clean_text(img.attr("alt") or img.attr("title"))


def first_link(node, base_url):
    link = find_first(node, "a")
    if not link:
        return ""
    return absolutize(base_url, link.attr("href"))


def first_price(text):
    match = PRICE_RE.search(text or "")
    return clean_text(match.group(0)) if match else ""


def same_site(url, base_url):
    parsed = urllib.parse.urlparse(url)
    base = urllib.parse.urlparse(base_url)
    return parsed.scheme in {"http", "https"} and parsed.netloc == base.netloc


def extract_salla_context(html, url):
    category_match = re.search(r"/c(\d+)", url)
    if not category_match:
        category_source = re.search(
            r"<salla-products-list\b[^>]*\bsource=[\"']categories[\"'][^>]*\bsource-value=[\"'](\d+)[\"']",
            html,
            re.I,
        )
        category_match = category_source
    store_match = re.search(r'"store"\s*:\s*\{\s*"id"\s*:\s*(\d+)', html)
    page_title_match = re.search(r'"page"\s*:\s*\{[^{}]*"title"\s*:\s*"([^"]+)"', html)
    if not category_match or not store_match:
        return None
    return {
        "category_id": category_match.group(1),
        "store_id": store_match.group(1),
        "page_title": unescape(page_title_match.group(1).encode("utf-8").decode("unicode_escape"))
        if page_title_match
        else "",
    }


def extract_salla_store_context(html):
    store_match = re.search(r'"store"\s*:\s*\{\s*"id"\s*:\s*(\d+)', html)
    if not store_match:
        return None
    return {"store_id": store_match.group(1)}


def category_id_from_url(url):
    match = re.search(r"/c(\d+)", url)
    return match.group(1) if match else ""


def discover_salla_categories(html, final_url):
    parser = SiteParser()
    parser.feed(html)
    by_id = {}
    for node in walk(parser.root):
        if node.tag != "a":
            continue
        href = node.attr("href")
        category_url = absolutize(final_url, href)
        category_id = category_id_from_url(category_url)
        if not category_id:
            continue
        name = clean_text(node.text())
        if not name or name == "عرض الكل":
            name = urllib.parse.unquote(urllib.parse.urlparse(category_url).path.strip("/").split("/")[0])
        current = by_id.get(category_id)
        if not current or current["name"] == "عرض الكل":
            by_id[category_id] = {
                "category_id": category_id,
                "name": name,
                "url": category_url,
            }
    return list(by_id.values())[:MAX_SALLA_CATEGORIES]


def salla_headers(store_id):
    return {
        "S-SOURCE": "twilight",
        "S-APP-OS": "browser",
        "S-APP-VERSION": "2.14.459",
        "Store-Identifier": str(store_id),
        "currency": "SAR",
        "accept-language": "ar",
        "s-country": "sa",
        "cache-control": "no-cache",
    }


def salla_product_list_url(category_id):
    params = {
        "source": "categories",
        "source_value[0]": category_id,
        "filterable": "1",
        "per_page": "32",
    }
    for index, include in enumerate(SALLA_LIST_INCLUDES):
        params[f"includes[{index}]"] = include
    return f"{SALLA_API_BASE}/products?{urllib.parse.urlencode(params)}"


def scalar_or_json(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return "" if value is None else str(value)
    return json.dumps(value, ensure_ascii=False)


def nested_value(data, *keys):
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return scalar_or_json(value)


def canonical_image_key(url):
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path)
    path = re.sub(r"/cdn-cgi/image/[^/]+/", "/", path)
    filename = path.rsplit("/", 1)[-1]
    size_split = re.search(r"-(?:\d+(?:\.\d+)?x\d+(?:\.\d+)?)-(.+)$", filename)
    if size_split:
        filename = size_split.group(1)
    filename = re.sub(r"-(?:\d+(?:\.\d+)?x\d+(?:\.\d+)?|width=\d+|height=\d+|fit=[^-]+)(?=-|\.|$)", "", filename)
    filename = re.sub(r"-(?:\d+(?:\.\d+)?x\d+(?:\.\d+)?)-", "-", filename)
    return filename or path


def is_image_url(value):
    return isinstance(value, str) and value.startswith(("http://", "https://")) and (
        re.search(r"\.(?:png|jpe?g|webp|gif)(?:\?|$)", value, re.I)
        or "cdn.salla.sa" in value
    )


def collect_image_urls(value, urls):
    if isinstance(value, dict):
        for key in ("url", "original", "original_url", "original_image", "thumbnail", "image"):
            item = value.get(key)
            if is_image_url(item):
                urls.append(item)
        for child in value.values():
            collect_image_urls(child, urls)
    elif isinstance(value, list):
        for item in value:
            collect_image_urls(item, urls)
    elif is_image_url(value):
        urls.append(value)


def image_list(product):
    urls = []
    for key in ("original_image",):
        if product.get(key):
            urls.append(str(product[key]))
    if isinstance(product.get("image"), dict) and product["image"].get("url"):
        urls.append(product["image"]["url"])
    images = product.get("images")
    if isinstance(images, list):
        for image in images:
            if isinstance(image, dict) and image.get("url"):
                urls.append(image["url"])
            elif isinstance(image, str):
                urls.append(image)
    collect_image_urls(product, urls)
    unique = []
    seen = set()
    for url in urls:
        key = canonical_image_key(url)
        if url and key not in seen:
            seen.add(key)
            unique.append(url)
    return unique


def add_image_columns(row, images):
    for index, image_url in enumerate(images, start=1):
        row[f"image_{index}"] = image_url
    return row


def extract_image_urls_from_text(value):
    urls = []
    normalized = unescape(value or "").replace("\\/", "/")
    for match in re.finditer(r"https://cdn\.salla\.sa/[^\s\"'<>]+", normalized):
        url = match.group(0).split("&quot;")[0].split("\\")[0]
        if is_image_url(url):
            urls.append(url)
    return urls


def salla_product_page_gallery_images(product_url, product_id):
    if not product_url or not product_id:
        return []
    try:
        _, html, _, _ = fetch_url(product_url)
    except Exception:
        return []

    gallery_match = re.search(
        rf'<salla-slider[^>]+id=["\']details-slider-{re.escape(str(product_id))}["\'][\s\S]*?</salla-slider>',
        html,
        re.I,
    )
    if not gallery_match:
        return []

    urls = extract_image_urls_from_text(gallery_match.group(0))
    unique = []
    seen = set()
    for url in urls:
        key = canonical_image_key(url)
        if key not in seen:
            seen.add(key)
            unique.append(url)
    return unique


def flatten_api_product(product, prefix=""):
    row = {}
    for key, value in product.items():
        column = f"{prefix}{key}" if prefix else key
        if isinstance(value, dict):
            row[column] = json.dumps(value, ensure_ascii=False)
            for child_key, child_value in value.items():
                row[f"{column}.{child_key}"] = scalar_or_json(child_value)
        elif isinstance(value, list):
            row[column] = json.dumps(value, ensure_ascii=False)
        else:
            row[column] = scalar_or_json(value)
    return row


def product_detail_from_salla(product_id, headers):
    try:
        response = fetch_json(f"{SALLA_API_BASE}/products/{product_id}/details", headers=headers)
        return response.get("data") or {}
    except Exception:
        return {}


def product_options_from_salla(product_ids, headers):
    if not product_ids:
        return {}
    params = []
    for product_id in product_ids:
        params.append(("ids[]", product_id))
    for item in SALLA_LIST_INCLUDES:
        params.append(("with[]", item))
    url = f"{SALLA_API_BASE}/products/options?{urllib.parse.urlencode(params)}"
    try:
        response = fetch_json(url, headers=headers)
    except Exception:
        return {}
    result = {}
    for item in response.get("data", []):
        if isinstance(item, dict) and item.get("id") is not None:
            result[str(item["id"])] = item
    return result


def normalize_salla_product(product, detail, options, gallery_images=None):
    combined = {**product}
    if detail:
        combined.update({f"detail_{key}": value for key, value in detail.items()})
    if options:
        combined.update({f"options_{key}": value for key, value in options.items()})

    description = html_to_text(detail.get("description") or product.get("description") or "")
    model_number = (
        scalar_or_json(detail.get("model"))
        or scalar_or_json(product.get("model"))
        or scalar_or_json(detail.get("model_number"))
        or scalar_or_json(product.get("model_number"))
        or scalar_or_json(detail.get("sku"))
        or scalar_or_json(product.get("sku"))
        or scalar_or_json(detail.get("mpn"))
        or scalar_or_json(product.get("mpn"))
        or extract_model_number(description, [])
    )
    image_source = {**product, **detail, **options}
    if gallery_images:
        image_source["product_page_gallery_images"] = gallery_images
    images = image_list(image_source)

    row = {
        "source": "salla_api",
        "product_id": scalar_or_json(product.get("id") or detail.get("id")),
        "title": scalar_or_json(detail.get("name") or product.get("name")),
        "name": scalar_or_json(detail.get("name") or product.get("name")),
        "model_number": model_number,
        "sku": scalar_or_json(detail.get("sku") or product.get("sku")),
        "mpn": scalar_or_json(detail.get("mpn") or product.get("mpn")),
        "gtin": scalar_or_json(detail.get("gtin") or product.get("gtin")),
        "price": scalar_or_json(detail.get("price") or product.get("price")),
        "regular_price": scalar_or_json(detail.get("regular_price") or product.get("regular_price")),
        "sale_price": scalar_or_json(detail.get("sale_price") or product.get("sale_price")),
        "currency": scalar_or_json(detail.get("currency") or product.get("currency")),
        "status": scalar_or_json(detail.get("status") or product.get("status")),
        "is_available": scalar_or_json(detail.get("is_available") if "is_available" in detail else product.get("is_available")),
        "is_out_of_stock": scalar_or_json(detail.get("is_out_of_stock") if "is_out_of_stock" in detail else product.get("is_out_of_stock")),
        "quantity": scalar_or_json(detail.get("quantity") if "quantity" in detail else product.get("quantity")),
        "weight": scalar_or_json(detail.get("weight") if "weight" in detail else product.get("weight")),
        "category": nested_value(product, "category", "name"),
        "brand": nested_value(product, "brand", "name"),
        "image_count": len(images),
        "image": images[0] if images else "",
        "all_images": " | ".join(images),
        "url": scalar_or_json(detail.get("url") or product.get("url")),
        "description": description,
        "detail_description": description,
        "all_product_json": json.dumps(combined, ensure_ascii=False),
    }
    add_image_columns(row, images)
    row.update(flatten_api_product(combined, "api_"))
    return row


def extract_salla_category_products(store_id, category, total_row_budget=MAX_SALLA_PRODUCTS, progress=None, progress_counts=None):
    headers = salla_headers(store_id)
    next_url = salla_product_list_url(category["category_id"])
    products = []
    api_pages = 0
    warnings = []
    product_limit = min(MAX_SALLA_PRODUCTS, max(0, total_row_budget))

    while next_url and len(products) < product_limit:
        if progress:
            progress(f"Fetching category {category['name']} API page {api_pages + 1}...", progress_counts or {})
        response = fetch_json(next_url, headers=headers)
        api_pages += 1
        batch = response.get("data") or []
        if not batch:
            break
        if len(products) + len(batch) > product_limit:
            batch = batch[: max(0, product_limit - len(products))]
        products.extend(batch)
        next_url = (response.get("cursor") or {}).get("next")
        if len(products) >= product_limit and next_url:
            warnings.append(f"Salla product safety limit reached at {product_limit} products for {category['name']}.")
            break

    product_ids = [str(product.get("id")) for product in products if product.get("id")]
    options_by_id = product_options_from_salla(product_ids[:MAX_DETAIL_PAGES], headers)

    def enrich_product(index, product):
        product_id = str(product.get("id") or "")
        detail = product_detail_from_salla(product_id, headers) if product_id else {}
        product_options = options_by_id.get(product_id, {})
        image_source = {**product, **detail, **product_options}
        product_url = scalar_or_json(detail.get("url") or product.get("url"))
        gallery_images = []
        if len(image_list(image_source)) < 2:
            gallery_images = salla_product_page_gallery_images(product_url, product_id)
        row = normalize_salla_product(product, detail, product_options, gallery_images)
        row.update(
            {
                "source_category_id": category["category_id"],
                "source_category_name": category["name"],
                "source_category_url": category["url"],
            }
        )
        return index, row

    rows_by_index = {}
    completed = 0
    worker_count = min(SALLA_DETAIL_WORKERS, max(1, len(products)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(enrich_product, index, product) for index, product in enumerate(products, start=1)]
        for future in concurrent.futures.as_completed(futures):
            index, row = future.result()
            rows_by_index[index] = row
            completed += 1
        if progress:
            counts = dict(progress_counts or {})
            counts.update({"current_category_products": completed, "current_category_total": len(products)})
            progress(f"Reading product details {completed}/{len(products)} in {category['name']}...", counts)

    rows = [rows_by_index[index] for index in sorted(rows_by_index)]

    return {
        "context": {
            "store_id": store_id,
            "category_id": category["category_id"],
            "page_title": category["name"],
        },
        "rows": rows,
        "api_pages": api_pages,
        "warnings": warnings,
    }


def extract_salla_products(html, final_url, progress=None):
    context = extract_salla_context(html, final_url)
    if not context:
        return None
    category = {
        "category_id": context["category_id"],
        "name": context.get("page_title") or urllib.parse.unquote(urllib.parse.urlparse(final_url).path.strip("/").split("/")[0]),
        "url": final_url,
    }
    return extract_salla_category_products(context["store_id"], category, progress=progress)


def extract_salla_site_products(html, final_url, progress=None):
    if extract_salla_context(html, final_url):
        return None
    store_context = extract_salla_store_context(html)
    if not store_context:
        return None
    categories = discover_salla_categories(html, final_url)
    if not categories:
        return None
    if progress:
        progress(f"Found {len(categories)} categories. Starting category-wise scrape...", {"categories": len(categories)})

    rows = []
    api_pages = 0
    warnings = []
    scraped_categories = []
    for category_index, category in enumerate(categories, start=1):
        if len(rows) >= MAX_SALLA_TOTAL_ROWS:
            warnings.append(f"Salla total product row safety limit reached at {MAX_SALLA_TOTAL_ROWS}.")
            break
        remaining = MAX_SALLA_TOTAL_ROWS - len(rows)
        if progress:
            progress(
                f"Scraping category {category_index}/{len(categories)}: {category['name']}",
                {"categories": len(categories), "category_index": category_index, "listings": len(rows), "api_pages": api_pages},
            )
        result = extract_salla_category_products(
            store_context["store_id"],
            category,
            remaining,
            progress=progress,
            progress_counts={"categories": len(categories), "category_index": category_index, "listings": len(rows), "api_pages": api_pages},
        )
        api_pages += result["api_pages"]
        warnings.extend(result["warnings"])
        rows.extend(result["rows"])
        scraped_categories.append(
            {
                **category,
                "products_found": len(result["rows"]),
                "api_pages": result["api_pages"],
            }
        )
        if progress:
            progress(
                f"Finished {category['name']}: {len(result['rows'])} products. Total so far: {len(rows)}.",
                {"categories": len(categories), "category_index": category_index, "listings": len(rows), "api_pages": api_pages},
            )

    return {
        "context": store_context,
        "rows": rows,
        "api_pages": api_pages,
        "warnings": warnings,
        "categories": scraped_categories,
    }


def is_shopify_store(html):
    return bool(
        re.search(
            r"(?:cdn\.shopify\.com|Shopify\.theme|shopify-section|myshopify\.com)",
            html,
            re.I,
        )
    )


def shopify_collection_handle(url):
    match = re.search(r"/collections/([^/?#]+)", urllib.parse.urlparse(url).path, re.I)
    return urllib.parse.unquote(match.group(1)) if match else ""


def shopify_base_url(url):
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def fetch_shopify_pages(endpoint, key, progress=None, label="products", limit=MAX_SHOPIFY_PRODUCTS):
    items = []
    api_pages = 0
    page = 1
    while len(items) < limit:
        separator = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{separator}limit={SHOPIFY_PAGE_SIZE}&page={page}"
        if progress:
            progress(
                f"Fetching Shopify {label} page {page}...",
                {"listings": len(items), "api_pages": api_pages},
            )
        response = fetch_json(url)
        batch = response.get(key) or []
        api_pages += 1
        if not batch:
            break
        remaining = limit - len(items)
        items.extend(batch[:remaining])
        if len(batch) < SHOPIFY_PAGE_SIZE or len(items) >= limit:
            break
        page += 1
    return items, api_pages


def shopify_product_row(product, base_url, category=None):
    variants = product.get("variants") or []
    images = []
    seen_images = set()
    for image in product.get("images") or []:
        image_url = image.get("src") if isinstance(image, dict) else str(image)
        key = canonical_image_key(image_url)
        if image_url and key not in seen_images:
            seen_images.add(key)
            images.append(image_url)

    prices = []
    regular_prices = []
    available = False
    quantity = 0
    for variant in variants:
        try:
            prices.append(float(variant.get("price")))
        except (TypeError, ValueError):
            pass
        try:
            compare_price = float(variant.get("compare_at_price"))
            if compare_price:
                regular_prices.append(compare_price)
        except (TypeError, ValueError):
            pass
        available = available or bool(variant.get("available"))
        try:
            quantity += int(variant.get("inventory_quantity") or 0)
        except (TypeError, ValueError):
            pass

    first_variant = variants[0] if variants else {}
    price = min(prices) if prices else first_variant.get("price", "")
    regular_price = max(regular_prices) if regular_prices else first_variant.get("compare_at_price", "")
    sale_price = price if regular_price and price and float(price) < float(regular_price) else ""
    product_url = f"{base_url}/products/{product.get('handle', '')}"
    description = html_to_text(product.get("body_html") or "")
    category_name = (category or {}).get("title") or product.get("product_type") or ""
    category_id = (category or {}).get("id") or ""
    category_url = (category or {}).get("url") or ""

    row = {
        "source": "shopify_api",
        "product_id": scalar_or_json(product.get("id")),
        "title": scalar_or_json(product.get("title")),
        "name": scalar_or_json(product.get("title")),
        "model_number": scalar_or_json(first_variant.get("sku") or first_variant.get("barcode")),
        "sku": scalar_or_json(first_variant.get("sku")),
        "mpn": "",
        "gtin": scalar_or_json(first_variant.get("barcode")),
        "price": scalar_or_json(price),
        "regular_price": scalar_or_json(regular_price),
        "sale_price": scalar_or_json(sale_price),
        "currency": "",
        "status": "available" if available else "unavailable",
        "is_available": scalar_or_json(available),
        "is_out_of_stock": scalar_or_json(not available),
        "quantity": scalar_or_json(quantity),
        "weight": scalar_or_json(first_variant.get("grams")),
        "category": scalar_or_json(category_name),
        "brand": scalar_or_json(product.get("vendor")),
        "image_count": len(images),
        "image": images[0] if images else "",
        "all_images": " | ".join(images),
        "url": product_url,
        "description": description,
        "detail_description": description,
        "detail_status": "OK",
        "source_category_id": scalar_or_json(category_id),
        "source_category_name": scalar_or_json(category_name),
        "source_category_url": category_url,
        "variant_count": len(variants),
        "all_variants": json.dumps(variants, ensure_ascii=False),
        "all_product_json": json.dumps(product, ensure_ascii=False),
    }
    add_image_columns(row, images)
    row.update(flatten_api_product(product, "api_"))
    return row


def extract_shopify_products(html, final_url, mode="auto", progress=None):
    if not is_shopify_store(html):
        return None
    base_url = shopify_base_url(final_url)
    collection_handle = shopify_collection_handle(final_url)
    if mode == "category" and not collection_handle:
        raise ValueError("Please paste a Shopify collection/category link for Single Category mode.")

    if mode == "category" or (mode == "auto" and collection_handle):
        endpoint = f"{base_url}/collections/{urllib.parse.quote(collection_handle)}/products.json"
        products, api_pages = fetch_shopify_pages(endpoint, "products", progress, "collection products")
        category = {
            "id": collection_handle,
            "title": clean_text(urllib.parse.unquote(collection_handle).replace("-", " ").title()),
            "url": final_url,
            "products_found": len(products),
            "api_pages": api_pages,
        }
        rows = [shopify_product_row(product, base_url, category) for product in products]
        return {"rows": rows, "api_pages": api_pages, "warnings": [], "categories": [category]}

    products, product_pages = fetch_shopify_pages(
        f"{base_url}/products.json",
        "products",
        progress,
        "store products",
    )
    collections, collection_pages = fetch_shopify_pages(
        f"{base_url}/collections.json",
        "collections",
        progress,
        "collections",
        MAX_SHOPIFY_COLLECTIONS,
    )
    categories = [
        {
            "category_id": scalar_or_json(collection.get("id")),
            "name": scalar_or_json(collection.get("title")),
            "url": f"{base_url}/collections/{collection.get('handle', '')}",
            "products_found": scalar_or_json(collection.get("products_count")),
        }
        for collection in collections
    ]
    rows = [shopify_product_row(product, base_url) for product in products]
    return {
        "rows": rows,
        "api_pages": product_pages + collection_pages,
        "warnings": [],
        "categories": categories,
    }


def text_value(value):
    if isinstance(value, dict):
        if "name" in value:
            return text_value(value["name"])
        if "@id" in value:
            return text_value(value["@id"])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return ", ".join(filter(None, [text_value(item) for item in value]))
    return clean_text(str(value or ""))


def structured_value(objects, keys):
    wanted = {key.lower() for key in keys}
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for key, value in obj.items():
            if key.lower() in wanted:
                result = text_value(value)
                if result:
                    return result
    return ""


def structured_value_ordered(objects, keys):
    for key in keys:
        value = structured_value(objects, [key])
        if value:
            return value
    return ""


def structured_price(objects):
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for key in ("price", "lowPrice", "highPrice"):
            if key in obj:
                return text_value(obj[key])
        offers = obj.get("offers")
        if offers:
            found = structured_price(list(structured_objects(offers)))
            if found:
                return found
    return ""


def extract_model_number(page_text, objects):
    structured = structured_value_ordered(objects, DETAIL_KEYS["model_number"])
    if structured:
        return structured
    match = MODEL_LABEL_RE.search(page_text)
    if not match:
        return ""
    value = clean_text(match.group(1))
    value = re.split(
        r"\s+(?:price|brand|manufacturer|availability|description|category|condition|color|size|sku|mpn|model|item|part)\b",
        value,
        flags=re.I,
    )[0]
    return value.strip(" :-#")


def extract_detail_page(url):
    try:
        final_url, html, content_type, warning = fetch_url(url)
    except Exception as exc:
        return {
            "detail_status": f"Failed: {exc}",
            "detail_url": url,
            "model_number": "",
        }

    parser = SiteParser()
    parser.feed(html)
    text = parser.root.text()
    objects = list(structured_objects(parser.structured))
    price_match = PRICE_RE.search(text)
    image_urls = [
        absolutize(final_url, image["src"])
        for image in parser.images[:8]
        if image.get("src")
    ]

    return {
        "detail_status": "OK",
        "detail_url": final_url,
        "detail_title": parser.title,
        "detail_price": structured_price(objects) or (clean_text(price_match.group(0)) if price_match else ""),
        "brand": structured_value(objects, DETAIL_KEYS["brand"]),
        "sku": structured_value(objects, DETAIL_KEYS["sku"]),
        "mpn": structured_value(objects, DETAIL_KEYS["mpn"]),
        "model_number": extract_model_number(text, objects),
        "availability": structured_value(objects, DETAIL_KEYS["availability"]),
        "detail_images": " | ".join(image_urls),
        "detail_description": text[:2000],
        "detail_warning": warning,
    }


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def extract_listings(root, base_url):
    candidates = []
    seen_text = set()
    seen_urls = set()
    for node in walk(root):
        score = node_score(node)
        if score < 6:
            continue
        text = node.text()
        fingerprint = clean_text(text[:300]).lower()
        if not fingerprint or fingerprint in seen_text:
            continue
        price_match = PRICE_RE.search(text)
        image_url, image_alt = first_image(node, base_url)
        detail_url = first_link(node, base_url)
        if detail_url and detail_url in seen_urls:
            continue
        seen_text.add(fingerprint)
        if detail_url:
            seen_urls.add(detail_url)
        candidates.append(
            {
                "title": find_heading_text(node),
                "price": clean_text(price_match.group(0)) if price_match else "",
                "image": image_url,
                "image_alt": image_alt,
                "url": detail_url,
                "description": text[:1500],
            }
        )
    return candidates[:250]


def looks_like_product_url(url):
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path).lower()
    return any(token in path for token in ("/product", "/products", "/p/", "/item", "/shop", "/store")) or re.search(r"/p\d+", path)


def extract_link_image_listings(root, base_url):
    candidates = []
    seen_urls = set()
    for node in walk(root):
        if node.tag != "a":
            continue
        detail_url = absolutize(base_url, node.attr("href"))
        if not detail_url or detail_url in seen_urls or not same_site(detail_url, base_url):
            continue
        image_url, image_alt = first_image(node, base_url)
        text = node.text()
        title = clean_text(text or image_alt)
        if not image_url and not title:
            continue
        if not image_url and not looks_like_product_url(detail_url):
            continue
        if len(title) > 260:
            title = title[:260]
        seen_urls.add(detail_url)
        candidates.append(
            {
                "title": title or urllib.parse.unquote(urllib.parse.urlparse(detail_url).path.strip("/").split("/")[-1]),
                "price": first_price(text),
                "image": image_url,
                "image_alt": image_alt,
                "url": detail_url,
                "description": clean_text(text)[:1500],
                "source": "link_image_fallback",
            }
        )
    return candidates[:500]


def flatten_structured_item(item):
    if not isinstance(item, dict):
        return {}
    row = {}
    for key, value in item.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            row[key] = "" if value is None else str(value)
        elif key in {"offers", "brand", "image", "aggregateRating"}:
            row[key] = json.dumps(value, ensure_ascii=False)
    return row


def extract_site_data(url, progress=None, mode="auto"):
    if progress:
        progress("Fetching website page...", {})
    final_url, html, content_type, warning = fetch_url(url)
    if mode == "website" and extract_salla_context(html, final_url):
        parsed_url = urllib.parse.urlparse(final_url)
        homepage_url = urllib.parse.urlunparse((parsed_url.scheme, parsed_url.netloc, "/", "", "", ""))
        if progress:
            progress("Switching to the store homepage for full website discovery...", {})
        final_url, html, content_type, homepage_warning = fetch_url(homepage_url)
        warning = sanitize_warning(" ".join(filter(None, [warning, homepage_warning])))
    if progress:
        progress("Reading page structure and checking store type...", {})
    parser = SiteParser()
    parser.feed(html)

    images = [
        {**image, "src": absolutize(final_url, image["src"])}
        for image in parser.images
    ][:500]
    links = [
        {**link, "href": absolutize(final_url, link["href"])}
        for link in parser.links
    ][:1000]
    platform_result = extract_shopify_products(html, final_url, mode=mode, progress=progress)
    if not platform_result:
        if mode == "website":
            platform_result = extract_salla_site_products(html, final_url, progress=progress)
        elif mode == "category":
            platform_result = extract_salla_products(html, final_url, progress=progress)
        else:
            salla_site_result = extract_salla_site_products(html, final_url, progress=progress)
            platform_result = salla_site_result or extract_salla_products(html, final_url, progress=progress)
    categories = platform_result.get("categories", []) if platform_result else []
    if platform_result:
        listings = platform_result["rows"]
        detail_count = len(listings)
        api_pages = platform_result["api_pages"]
        warning = sanitize_warning(" ".join(filter(None, [warning, *platform_result["warnings"]])))
        for row in listings:
            row.setdefault("detail_status", "OK")
        if listings:
            product_images = []
            for listing in listings:
                for image_url in (listing.get("all_images") or listing.get("image") or "").split(" | "):
                    if image_url:
                        product_images.append({"src": image_url, "alt": listing.get("title", ""), "title": listing.get("title", "")})
            images = product_images[:1000]
    else:
        listings = extract_listings(parser.root, final_url)
        if not listings:
            listings = extract_link_image_listings(parser.root, final_url)
        api_pages = 0
        detail_count = 0
        for listing in listings[:MAX_DETAIL_PAGES]:
            if progress:
                progress(
                    f"Reading product detail page {detail_count + 1}/{min(len(listings), MAX_DETAIL_PAGES)}...",
                    {"listings": len(listings), "detail_pages": detail_count},
                )
            detail_url = listing.get("url", "")
            if not detail_url or not same_site(detail_url, final_url):
                listing.update(
                    {
                        "detail_status": "Skipped: no same-site product URL",
                        "detail_url": detail_url,
                        "model_number": "",
                    }
                )
                continue
            listing.update(extract_detail_page(detail_url))
            detail_count += 1
            time.sleep(DETAIL_DELAY_SECONDS)
        for listing in listings[MAX_DETAIL_PAGES:]:
            listing.update(
                {
                    "detail_status": f"Skipped: detail page limit is {MAX_DETAIL_PAGES}",
                    "detail_url": listing.get("url", ""),
                    "model_number": "",
                }
            )
    structured_rows = [flatten_structured_item(item) for item in parser.structured]
    model_count = sum(1 for listing in listings if listing.get("model_number"))
    if progress:
        progress(
            "Preparing table and Excel-ready data...",
            {
                "listings": len(listings),
                "detail_pages": detail_count,
                "model_numbers": model_count,
                "api_pages": api_pages,
                "categories": len(categories),
                "images": len(images),
                "links": len(links),
                "metadata": len(parser.meta),
                "structured_data": len(structured_rows),
            },
        )

    return {
        "id": str(uuid.uuid4()),
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": final_url,
        "scrape_mode": mode,
        "content_type": content_type,
        "warning": sanitize_warning(warning),
        "page_title": parser.title,
        "counts": {
            "listings": len(listings),
            "detail_pages": detail_count,
            "model_numbers": model_count,
            "api_pages": api_pages,
            "categories": len(categories),
            "images": len(images),
            "links": len(links),
            "metadata": len(parser.meta),
            "structured_data": len(structured_rows),
        },
        "summary": [
            {"field": "Website", "value": final_url},
            {"field": "Page title", "value": parser.title},
            {"field": "Content type", "value": content_type},
            {"field": "Scraped at", "value": time.strftime("%Y-%m-%d %H:%M:%S")},
            {"field": "Listings found", "value": len(listings)},
            {"field": "Product detail pages visited", "value": detail_count},
            {"field": "Model numbers found", "value": model_count},
            {"field": "API pages fetched", "value": api_pages},
            {"field": "Categories scraped", "value": len(categories)},
            {"field": "Detail page limit", "value": MAX_DETAIL_PAGES},
            {"field": "Salla product safety limit", "value": MAX_SALLA_PRODUCTS},
            {"field": "Salla category safety limit", "value": MAX_SALLA_CATEGORIES},
            {"field": "Salla total row safety limit", "value": MAX_SALLA_TOTAL_ROWS},
        ],
        "listings": listings,
        "categories": categories,
        "images": images,
        "links": links,
        "metadata": parser.meta,
        "structured_data": structured_rows,
    }


def column_letter(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def sheet_xml(rows):
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for row_index, row in enumerate(rows, start=1):
        out.append(f'<row r="{row_index}">')
        for col_index, value in enumerate(row, start=1):
            ref = f"{column_letter(col_index)}{row_index}"
            text = xml_escape(str(value or ""))
            out.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def rows_from_dicts(items):
    keys = []
    for item in items:
        for key in item.keys():
            if key not in keys:
                keys.append(key)
    if not keys:
        return [["No data found"]]
    rows = [keys]
    for item in items:
        rows.append([item.get(key, "") for key in keys])
    return rows


def build_xlsx(data):
    sheets = [
        ("Summary", [["Field", "Value"]] + [[x["field"], x["value"]] for x in data.get("summary", [])]),
        ("Listings", rows_from_dicts(data.get("listings", []))),
        ("Categories", rows_from_dicts(data.get("categories", []))),
        ("Images", rows_from_dicts(data.get("images", []))),
        ("Links", rows_from_dicts(data.get("links", []))),
        ("Metadata", rows_from_dicts(data.get("metadata", []))),
        ("Structured Data", rows_from_dicts(data.get("structured_data", []))),
    ]

    workbook_sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheets, start=1)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{content_types}</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets></workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{workbook_rels}</Relationships>",
        )
        for index, (_, rows) in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))
    return buffer.getvalue()


def export_filename(data):
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    host = urllib.parse.urlparse(data.get("url", "")).netloc or "website"
    safe_host = re.sub(r"[^A-Za-z0-9._-]+", "-", host).strip("-") or "website"
    return f"website-data-{safe_host}-{timestamp}.xlsx"


def save_xlsx_export(data):
    EXPORT_DIR.mkdir(exist_ok=True)
    filename = export_filename(data)
    path = EXPORT_DIR / filename
    path.write_bytes(build_xlsx(data))
    return filename, path


class AppHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/api/job":
            params = urllib.parse.parse_qs(parsed.query)
            job_id = (params.get("id") or [""])[0]
            job = get_job(job_id)
            if not job:
                return self.send_json({"error": "Scrape job not found."}, 404)
            return self.send_json(job)
        if path.startswith("/static/"):
            file_path = STATIC_DIR / path.removeprefix("/static/")
            content_type = "text/css" if file_path.suffix == ".css" else "application/javascript"
            return self.serve_file(file_path, content_type)
        if path.startswith("/exports/"):
            file_path = EXPORT_DIR / path.removeprefix("/exports/")
            return self.serve_file(
                file_path,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                attachment_name=file_path.name,
            )
        self.send_error(404)

    def do_POST(self):
        try:
            if self.path == "/api/scrape":
                payload = self.read_json()
                job_id = create_scrape_job(payload.get("url", ""), payload.get("mode", "auto"))
                return self.send_json(
                    {
                        "job_id": job_id,
                        "status": "queued",
                        "message": "Scrape started. The app will update progress here.",
                    }
                )
            if self.path == "/api/download":
                payload = self.read_json()
                data = payload.get("data", {})
                filename, path = save_xlsx_export(data)
                workbook = path.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(workbook)))
                self.end_headers()
                self.wfile.write(workbook)
                return
            if self.path == "/api/export":
                payload = self.read_json()
                filename, path = save_xlsx_export(payload.get("data", {}))
                return self.send_json(
                    {
                        "filename": filename,
                        "download_url": f"/exports/{urllib.parse.quote(filename)}",
                        "saved_path": str(path),
                    }
                )
            self.send_error(404)
        except (ValueError, urllib.error.URLError, TimeoutError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"Something went wrong: {exc}"}, 500)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def serve_file(self, path, content_type, attachment_name=None):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if attachment_name:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), AppHandler)
    print(f"Website data scraper running at http://127.0.0.1:{PORT}")
    server.serve_forever()

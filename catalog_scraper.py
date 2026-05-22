import argparse
import html
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

from catalog_db import init_catalog_db, upsert_product, upsert_promotion


SITEMAP_URL = "https://centr-krasok.kz/sitemap-iblock-87.xml"
PROMOTIONS_URL = "https://centr-krasok.kz/promotions/"
USER_AGENT = "ColourStoreAssistant/0.1 (+local MVP)"


def fetch_text(url: str) -> str:
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def load_catalog_urls(sitemap_url: str = SITEMAP_URL) -> list[str]:
    xml_text = fetch_text(sitemap_url)
    root = ElementTree.fromstring(xml_text)
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in root.findall(".//sm:loc", namespace) if node.text]
    return [url for url in urls if _looks_like_product_url(url)]


def _looks_like_product_url(url: str) -> bool:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    return len(parts) >= 3 and parts[0] == "catalog"


def strip_to_text(raw_html: str) -> str:
    raw_html = re.sub(r"(?is)<script.*?</script>", "\n", raw_html)
    raw_html = re.sub(r"(?is)<style.*?</style>", "\n", raw_html)
    text = re.sub(r"(?s)<[^>]+>", "\n", raw_html)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def parse_product_page(url: str, raw_html: str) -> dict | None:
    text = strip_to_text(raw_html)
    if "Артикул" not in text and "Цена" not in text:
        return None

    name = _first_group(raw_html, r"<h1[^>]*>(.*?)</h1>", flags=re.I | re.S)
    if name:
        name = re.sub(r"<[^>]+>", " ", html.unescape(name))
        name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = _first_group(raw_html, r"<title>(.*?)</title>", flags=re.I | re.S)
        name = re.sub(r"^\s*Купить\s+", "", html.unescape(name or "")).strip()
    if not name:
        return None

    sku = _line_value(text, "Артикул")
    brand = _line_value(text, "Бренд")
    category = _line_value(text, "Категория товара")
    color = _line_value(text, "Цвет")
    usage = _line_value(text, "Тип работ") or _line_value(text, "Интерьер")
    volume = _extract_volume(name) or _line_value(text, "Фасовка")
    price = _extract_price(text)
    stock = _extract_stock(text)

    description = _extract_description(text)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return {
        "site_id": _extract_site_id(raw_html),
        "sku": sku,
        "name": name,
        "brand": brand,
        "category": category,
        "color": color,
        "usage": usage,
        "volume": volume,
        "url": url,
        "description": description,
        "price_kzt": price,
        "stock": stock,
        "attributes": {
            "raw_usage": usage,
            "raw_category": category,
        },
        "fetched_at": fetched_at,
    }


def load_promotion_listing_urls(max_pages: int = 5) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        page_url = PROMOTIONS_URL if page == 1 else f"{PROMOTIONS_URL}?PAGEN_1={page}"
        raw_html = fetch_text(page_url)
        page_urls = parse_promotion_links(raw_html)
        for url in page_urls:
            if url not in seen:
                seen.add(url)
                urls.append(url)
        if not page_urls:
            break
    return urls


def parse_promotion_links(raw_html: str) -> list[str]:
    links: list[str] = []
    for href in re.findall(r'<a[^>]+href="([^"]+)"', raw_html, flags=re.I):
        if not href.startswith("/promotions/"):
            continue
        if href == "/promotions/" or "PAGEN_" in href:
            continue
        full_url = urljoin(PROMOTIONS_URL, href)
        if full_url not in links:
            links.append(full_url)
    return links


def parse_promotion_page(url: str, raw_html: str) -> dict | None:
    text = strip_to_text(raw_html)
    title = _first_group(raw_html, r"<h1[^>]*>(.*?)</h1>", flags=re.I | re.S)
    if title:
        title = re.sub(r"<[^>]+>", " ", html.unescape(title))
        title = re.sub(r"\s+", " ", title).strip()
    if not title:
        title = _first_group(raw_html, r"<title>(.*?)</title>", flags=re.I | re.S)
        title = re.sub(r"\s+", " ", html.unescape(title or "")).strip()
    if not title:
        return None

    published_at = _extract_date(text)
    discount_text = _extract_discount(text)
    summary = _extract_promotion_summary(text, title)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return {
        "title": title,
        "summary": summary,
        "url": url,
        "published_at": published_at,
        "discount_text": discount_text,
        "fetched_at": fetched_at,
    }


def _first_group(text: str, pattern: str, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _line_value(text: str, label: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == label and index + 1 < len(lines):
            return _clean_value(lines[index + 1])
        if line.startswith(label + " "):
            return _clean_value(line[len(label) :])
    return None


def _clean_value(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip(" :\t\r\n")
    return value or None


def _extract_volume(name: str) -> str | None:
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*(л|l|мл|ml|кг|kg)\b", name, re.I)
    return match.group(0).replace(",", ".") if match else None


def _extract_price(text: str) -> int | None:
    match = re.search(r"Цена\s*\n\s*([0-9 ]+)\s*KZT", text, re.I)
    if not match:
        match = re.search(r"([0-9][0-9 ]{2,})\s*KZT", text, re.I)
    if not match:
        return None
    return int(match.group(1).replace(" ", ""))


def _extract_stock(text: str) -> dict[str, float]:
    stock: dict[str, float] = {}
    for city, quantity in re.findall(r"Остаток\s+([^:\n]+):\s*([0-9]+(?:[,.][0-9]+)?)\s*шт", text):
        stock[city.strip()] = float(quantity.replace(",", "."))
    return stock


def _extract_description(text: str) -> str | None:
    marker = "Описание"
    if marker not in text:
        return None
    tail = text.split(marker, 1)[1]
    stop_markers = ["Характеристики", "Полное описание", "Купить в 1 клик", "С этим товаром покупают"]
    for stop in stop_markers:
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    tail = re.sub(r"\s+", " ", tail).strip()
    return tail[:1200] or None


def _extract_site_id(raw_html: str) -> str | None:
    return _first_group(raw_html, r"'PRODUCT':\{'ID':'([^']+)'")


def _extract_date(text: str) -> str | None:
    match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def _extract_discount(text: str) -> str | None:
    match = re.search(r"(?:скидк[аиуы]?|экономь(?:те)?|выгод[аы])[^.\n]{0,80}?-?\s*\d{1,2}\s*%", text, re.I)
    if not match:
        match = re.search(r"-?\s*\d{1,2}\s*%", text)
    return _clean_value(match.group(0)) if match else None


def _extract_promotion_summary(text: str, title: str) -> str | None:
    lines = [line for line in text.splitlines() if line.strip()]
    start = 0
    for index, line in enumerate(lines):
        if title[:30] in line:
            start = index + 1
            break

    useful: list[str] = []
    skip_words = {
        "Главная",
        "Каталог",
        "Акции",
        "Корзина",
        "Профиль",
        "Время работы:",
        "Выберите город:",
    }
    for line in lines[start:]:
        if line in skip_words:
            continue
        if line.startswith("+7"):
            continue
        if line == title:
            continue
        useful.append(line)
        if len(" ".join(useful)) > 900:
            break

    summary = re.sub(r"\s+", " ", " ".join(useful)).strip()
    return summary[:1200] or None


def crawl(
    limit: int | None = None,
    offset: int = 0,
    progress_every: int = 25,
) -> tuple[int, int]:
    init_catalog_db()
    urls = load_catalog_urls()
    total_urls = len(urls)
    if offset:
        urls = urls[offset:]
    if limit:
        urls = urls[:limit]

    planned = len(urls)
    print(
        f"product urls in sitemap: {total_urls}; offset: {offset}; planned checks: {planned}",
        flush=True,
    )

    found = 0
    saved = 0
    for index, url in enumerate(urls, start=offset + 1):
        found += 1
        try:
            product = parse_product_page(url, fetch_text(url))
            if product:
                upsert_product(product)
                saved += 1
        except Exception as exc:
            print(f"failed {url}: {type(exc).__name__}: {exc}")
        if progress_every and (found % progress_every == 0 or found == planned):
            print(
                f"progress: checked {found}/{planned} "
                f"(sitemap index {index}/{total_urls}); saved {saved}",
                flush=True,
            )
    return found, saved


def crawl_promotions(
    max_pages: int = 5,
    limit: int | None = None,
    offset: int = 0,
    progress_every: int = 25,
) -> tuple[int, int]:
    init_catalog_db()
    urls = load_promotion_listing_urls(max_pages=max_pages)
    total_urls = len(urls)
    if offset:
        urls = urls[offset:]
    if limit:
        urls = urls[:limit]

    planned = len(urls)
    print(
        f"promotion urls found: {total_urls}; offset: {offset}; planned checks: {planned}",
        flush=True,
    )

    found = 0
    saved = 0
    for index, url in enumerate(urls, start=offset + 1):
        found += 1
        try:
            promotion = parse_promotion_page(url, fetch_text(url))
            if promotion:
                upsert_promotion(promotion)
                saved += 1
        except Exception as exc:
            print(f"failed promotion {url}: {type(exc).__name__}: {exc}")
        if progress_every and (found % progress_every == 0 or found == planned):
            print(
                f"progress: checked {found}/{planned} "
                f"(listing index {index}/{total_urls}); saved {saved}",
                flush=True,
            )
    return found, saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl dynamic catalog data into SQLite.")
    parser.add_argument("--limit", type=int, default=None, help="Limit pages for a quick MVP crawl.")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N URLs from sitemap/listing.")
    parser.add_argument("--progress-every", type=int, default=25, help="Print progress every N checked pages.")
    parser.add_argument("--promotions", action="store_true", help="Crawl promotion pages instead of products.")
    parser.add_argument("--promotion-pages", type=int, default=5, help="Max promotion listing pages to crawl.")
    args = parser.parse_args()
    if args.promotions:
        found, saved = crawl_promotions(
            max_pages=args.promotion_pages,
            limit=args.limit,
            offset=args.offset,
            progress_every=args.progress_every,
        )
        print(f"promotion pages checked: {found}")
        print(f"promotions saved: {saved}")
    else:
        found, saved = crawl(
            limit=args.limit,
            offset=args.offset,
            progress_every=args.progress_every,
        )
        print(f"catalog pages checked: {found}")
        print(f"products saved: {saved}")


if __name__ == "__main__":
    main()

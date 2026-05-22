import re
from difflib import SequenceMatcher
from typing import Any

from catalog_db import ProductFilters, count_products, list_product_candidates, search_products
from deepseek_client import DeepSeekClient


FILTER_SCHEMA_HINT = {
    "query": "original product phrase without budget/city words",
    "category": "краска, грунтовка, лак, эмаль, инструмент, etc",
    "brand": "brand/manufacturer, e.g. Dulux, Marshall, Dufa, Pinotex",
    "color": "requested color, if any",
    "usage": "interior/exterior/bathroom/kitchen/metal/wood/facade/etc",
    "volume": "requested packaging volume like 0.9л, 2.5л, 9л",
    "price_min": "integer KZT",
    "price_max": "integer KZT",
    "city": "Kazakhstan city, default Алматы",
    "in_stock_only": "true unless user explicitly allows заказ/под заказ",
}


def fallback_extract_filters(message: str, default_city: str = "Алматы") -> dict[str, Any]:
    text = message.lower()
    price_min = None
    price_max = None

    range_match = re.search(r"от\s*(\d+)\s*(?:к|тыс|000)?\s*(?:до|-)\s*(\d+)\s*(?:к|тыс|000)?", text)
    if range_match:
        price_min = _normalize_money(range_match.group(1), text[range_match.start() : range_match.end()])
        price_max = _normalize_money(range_match.group(2), text[range_match.start() : range_match.end()])
    else:
        upper_match = re.search(r"до\s*(\d+)\s*(?:к|тыс|000)?", text)
        if upper_match:
            price_max = _normalize_money(upper_match.group(1), text[upper_match.start() : upper_match.end()])

    known_brands = ["dulux", "marshall", "dufa", "dufapremium", "pinotex", "hammerite", "luxium", "oikos"]
    brand = next((brand for brand in known_brands if brand in text), None)
    if brand:
        brand = {"dufapremium": "DufaPremium"}.get(brand, brand.title())

    colors = ["красн", "бел", "черн", "син", "зелен", "желт", "сер", "беж", "корич", "розов"]
    color = next((c for c in colors if c in text), None)
    color_map = {
        "красн": "красный",
        "бел": "белый",
        "черн": "черный",
        "син": "синий",
        "зелен": "зеленый",
        "желт": "желтый",
        "сер": "серый",
        "беж": "бежевый",
        "корич": "коричневый",
        "розов": "розовый",
    }

    category = None
    category_stems = {
        "краск": "Краска",
        "грунт": "Грунтовка",
        "лак": "Лак",
        "эмал": "Эмаль",
        "шпатлев": "Шпатлевка",
        "штукатур": "Штукатурка",
    }
    for stem, value in category_stems.items():
        if stem in text:
            category = value
            break

    usage = None
    usage_stems = {
        "фасад": "фасад",
        "интерьер": "интерьер",
        "внутрен": "внутрен",
        "наруж": "наруж",
        "ванн": "ванная",
        "кухн": "кухня",
        "металл": "металл",
        "дерев": "дерево",
        "пол": "пол",
    }
    for stem, value in usage_stems.items():
        if stem == "пол":
            matched = re.search(r"(^|\s)пол(а|у|ом|ы)?(\s|$)", text)
        else:
            matched = stem in text
        if matched:
            usage = value
            break

    volume_match = re.search(r"(\d+(?:[,.]\d+)?)\s*(л|литр|мл|кг)", text)
    query = None if any([category, brand, color, usage, price_min, price_max]) else message

    return {
        "query": query,
        "category": category,
        "brand": brand,
        "color": color_map.get(color) if color else None,
        "usage": usage,
        "volume": volume_match.group(0) if volume_match else None,
        "price_min": price_min,
        "price_max": price_max,
        "city": default_city,
        "in_stock_only": "под заказ" not in text,
    }


def _normalize_money(number: str, context: str) -> int:
    value = int(number)
    if value < 1000 or "к" in context or "тыс" in context:
        return value * 1000
    return value


class CatalogTools:
    def __init__(self, llm: DeepSeekClient | None = None):
        self.llm = llm or DeepSeekClient()

    def extract_filters(self, message: str, default_city: str = "Алматы") -> ProductFilters:
        fallback = fallback_extract_filters(message, default_city)
        if self.llm.is_configured:
            try:
                parsed = self.llm.json_chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Extract product recommendation/search filters from the user's Russian message. "
                                "Return only JSON. Do not invent values. Use null for unknown fields. "
                                f"Schema meaning: {FILTER_SCHEMA_HINT}"
                            ),
                        },
                        {"role": "user", "content": message},
                    ],
                    temperature=0.0,
                    max_tokens=500,
                )
                fallback.update({key: value for key, value in parsed.items() if value not in (None, "", [])})
            except Exception:
                pass

        return ProductFilters(
            query=fallback.get("query") or message,
            city=fallback.get("city") or default_city,
            category=fallback.get("category"),
            brand=fallback.get("brand"),
            color=fallback.get("color"),
            usage=fallback.get("usage"),
            volume=fallback.get("volume"),
            price_min=_as_int(fallback.get("price_min")),
            price_max=_as_int(fallback.get("price_max")),
            in_stock_only=bool(fallback.get("in_stock_only", True)),
            limit=5,
        )

    def recommend_products(self, message: str, default_city: str = "Алматы") -> dict[str, Any]:
        filters = self.extract_filters(message, default_city)
        products = search_products(filters)
        relaxed_filters: list[str] = []

        if not products and filters.query:
            relaxed_filters.append("fuzzy_query")
            products = fuzzy_search_products(filters)

        if not products and filters.color:
            relaxed_filters.append("color")
            filters = ProductFilters(
                query=filters.query,
                city=filters.city,
                category=filters.category,
                brand=filters.brand,
                color=None,
                usage=filters.usage,
                volume=filters.volume,
                price_min=filters.price_min,
                price_max=filters.price_max,
                in_stock_only=filters.in_stock_only,
                limit=filters.limit,
            )
            products = search_products(filters)
            if not products and filters.query:
                relaxed_filters.append("color+fuzzy_query")
                products = fuzzy_search_products(filters)

        return {
            "filters": filters.__dict__,
            "relaxed_filters": relaxed_filters,
            "products_count_in_db": count_products(),
            "products": products,
        }


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fuzzy_search_products(filters: ProductFilters) -> list[dict[str, Any]]:
    if not filters.query:
        return []

    scored = []
    for candidate_filters in _fuzzy_candidate_filters(filters):
        candidates = list_product_candidates(candidate_filters)
        scored = []
        for product in candidates:
            score = _product_match_score(filters.query, product)
            if score >= 0.52:
                item = dict(product)
                item["match_score"] = round(score, 3)
                scored.append(item)
        if scored:
            break

    scored.sort(
        key=lambda product: (
            -product["match_score"],
            0 if product.get("quantity", 0) else 1,
            product.get("price_kzt") or 10**12,
        )
    )
    return scored[: max(1, min(filters.limit, 10))]


def _fuzzy_candidate_filters(filters: ProductFilters) -> list[ProductFilters]:
    common = {
        "query": None,
        "city": filters.city,
        "color": filters.color,
        "volume": filters.volume,
        "price_min": filters.price_min,
        "price_max": filters.price_max,
        "in_stock_only": filters.in_stock_only,
        "limit": filters.limit,
    }
    return [
        ProductFilters(category=filters.category, brand=filters.brand, usage=filters.usage, **common),
        ProductFilters(category=filters.category, brand=None, usage=filters.usage, **common),
        ProductFilters(category=filters.category, brand=filters.brand, usage=None, **common),
        ProductFilters(category=filters.category, brand=None, usage=None, **common),
        ProductFilters(category=None, brand=None, usage=None, **common),
    ]


def _product_match_score(query: str, product: dict[str, Any]) -> float:
    query_norm = _normalize_for_match(query)
    haystack = " ".join(
        str(product.get(key) or "")
        for key in ["name", "sku", "brand", "category", "color", "usage", "volume", "description"]
    )
    haystack_norm = _normalize_for_match(haystack)
    if not query_norm or not haystack_norm:
        return 0.0

    query_tokens = _tokens(query_norm)
    product_tokens = _tokens(haystack_norm)
    if not query_tokens or not product_tokens:
        return 0.0

    token_scores = []
    for query_token in query_tokens[:8]:
        best = 0.0
        for product_token in product_tokens:
            ratio = SequenceMatcher(None, query_token, product_token).ratio()
            if query_token in product_token or product_token in query_token:
                ratio = max(ratio, 0.86)
            best = max(best, ratio)
        token_scores.append(best)

    score = sum(token_scores) / len(token_scores)
    if query_norm in haystack_norm:
        score = max(score, 0.95)
    if product.get("sku") and _normalize_for_match(str(product["sku"])) in query_norm:
        score = max(score, 0.98)
    return score


def _normalize_for_match(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _tokens(value: str) -> list[str]:
    stop_words = {
        "дай",
        "мне",
        "нужен",
        "нужна",
        "нужно",
        "товар",
        "товара",
        "покажи",
        "найди",
        "есть",
        "ли",
        "цена",
        "сколько",
        "стоит",
        "от",
        "до",
        "для",
        "фирмы",
        "бренда",
        "цвета",
        "тг",
        "kzt",
    }
    return [token for token in value.split() if len(token) > 1 and token not in stop_words]

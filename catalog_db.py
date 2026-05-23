import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CATALOG_DB_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ProductFilters:
    query: str | None = None
    city: str | None = "Алматы"
    category: str | None = None
    brand: str | None = None
    color: str | None = None
    usage: str | None = None
    volume: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    in_stock_only: bool = True
    limit: int = 5


def connect(db_path: Path = CATALOG_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_catalog_db(db_path: Path = CATALOG_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT UNIQUE,
                sku TEXT,
                name TEXT NOT NULL,
                brand TEXT,
                category TEXT,
                color TEXT,
                usage TEXT,
                volume TEXT,
                url TEXT UNIQUE NOT NULL,
                image_url TEXT,
                description TEXT,
                attributes_json TEXT NOT NULL DEFAULT '{}',
                is_active INTEGER NOT NULL DEFAULT 1,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prices (
                product_id INTEGER PRIMARY KEY,
                price_kzt INTEGER,
                old_price_kzt INTEGER,
                discount_percent INTEGER,
                fetched_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS stock (
                product_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                quantity REAL,
                status TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(product_id, city),
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT UNIQUE NOT NULL,
                published_at TEXT,
                starts_at TEXT,
                ends_at TEXT,
                discount_text TEXT,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                items_found INTEGER NOT NULL DEFAULT 0,
                items_updated INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
            CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
            CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
            CREATE INDEX IF NOT EXISTS idx_prices_price ON prices(price_kzt);
            CREATE INDEX IF NOT EXISTS idx_stock_city_quantity ON stock(city, quantity);
            """
        )


def upsert_product(product: dict[str, Any], db_path: Path = CATALOG_DB_PATH) -> int:
    init_catalog_db(db_path)
    fetched_at = product.get("fetched_at") or utc_now_iso()
    attributes = product.get("attributes") or {}

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO products (
                site_id, sku, name, brand, category, color, usage, volume, url,
                image_url, description, attributes_json, is_active, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(url) DO UPDATE SET
                site_id=excluded.site_id,
                sku=excluded.sku,
                name=excluded.name,
                brand=excluded.brand,
                category=excluded.category,
                color=excluded.color,
                usage=excluded.usage,
                volume=excluded.volume,
                image_url=excluded.image_url,
                description=excluded.description,
                attributes_json=excluded.attributes_json,
                is_active=1,
                fetched_at=excluded.fetched_at
            """,
            (
                product.get("site_id"),
                product.get("sku"),
                product["name"],
                product.get("brand"),
                product.get("category"),
                product.get("color"),
                product.get("usage"),
                product.get("volume"),
                product["url"],
                product.get("image_url"),
                product.get("description"),
                json.dumps(attributes, ensure_ascii=False),
                fetched_at,
            ),
        )
        row = conn.execute("SELECT id FROM products WHERE url = ?", (product["url"],)).fetchone()
        product_id = int(row["id"])

        conn.execute(
            """
            INSERT INTO prices (product_id, price_kzt, old_price_kzt, discount_percent, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                price_kzt=excluded.price_kzt,
                old_price_kzt=excluded.old_price_kzt,
                discount_percent=excluded.discount_percent,
                fetched_at=excluded.fetched_at
            """,
            (
                product_id,
                product.get("price_kzt"),
                product.get("old_price_kzt"),
                product.get("discount_percent"),
                fetched_at,
            ),
        )

        for city, quantity in (product.get("stock") or {}).items():
            status = "in_stock" if quantity and float(quantity) > 0 else "out_of_stock"
            conn.execute(
                """
                INSERT INTO stock (product_id, city, quantity, status, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(product_id, city) DO UPDATE SET
                    quantity=excluded.quantity,
                    status=excluded.status,
                    fetched_at=excluded.fetched_at
                """,
                (product_id, city, quantity, status, fetched_at),
            )

    return product_id


def upsert_promotion(promotion: dict[str, Any], db_path: Path = CATALOG_DB_PATH) -> int:
    init_catalog_db(db_path)
    fetched_at = promotion.get("fetched_at") or utc_now_iso()

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO promotions (
                title, summary, url, published_at, starts_at, ends_at, discount_text, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,
                summary=excluded.summary,
                published_at=excluded.published_at,
                starts_at=excluded.starts_at,
                ends_at=excluded.ends_at,
                discount_text=excluded.discount_text,
                fetched_at=excluded.fetched_at
            """,
            (
                promotion["title"],
                promotion.get("summary"),
                promotion["url"],
                promotion.get("published_at"),
                promotion.get("starts_at"),
                promotion.get("ends_at"),
                promotion.get("discount_text"),
                fetched_at,
            ),
        )
        row = conn.execute("SELECT id FROM promotions WHERE url = ?", (promotion["url"],)).fetchone()
    return int(row["id"])


def _add_like(where: list[str], params: list[Any], column: str, value: str | None) -> None:
    if value:
        variants = _case_variants(value)
        where.append("(" + " OR ".join(f"{column} LIKE ?" for _ in variants) + ")")
        params.extend(f"%{variant}%" for variant in variants)


def _case_variants(value: str) -> list[str]:
    variants = [value, value.lower(), value.upper(), value.capitalize(), value.title()]
    unique: list[str] = []
    for variant in variants:
        if variant and variant not in unique:
            unique.append(variant)
    return unique


def search_products(filters: ProductFilters, db_path: Path = CATALOG_DB_PATH) -> list[dict[str, Any]]:
    init_catalog_db(db_path)
    where = ["p.is_active = 1"]
    params: list[Any] = []

    if filters.query:
        terms = [term for term in filters.query.lower().split() if len(term) > 1]
        for term in terms[:6]:
            variants = _case_variants(term)
            one_term_where = []
            for _ in variants:
                one_term_where.append(
                    "(p.name LIKE ? OR COALESCE(p.sku, '') LIKE ? "
                    "OR COALESCE(p.brand, '') LIKE ? OR COALESCE(p.category, '') LIKE ?)"
                )
            where.append(
                "(" + " OR ".join(one_term_where) + ")"
            )
            for variant in variants:
                params.extend([f"%{variant}%"] * 4)

    _add_like(where, params, "p.brand", filters.brand)
    _add_like(where, params, "p.category", filters.category)
    _add_like(where, params, "p.color", filters.color)
    _add_like(where, params, "p.usage", filters.usage)
    _add_like(where, params, "p.volume", filters.volume)

    if filters.price_min is not None:
        where.append("pr.price_kzt >= ?")
        params.append(filters.price_min)
    if filters.price_max is not None:
        where.append("pr.price_kzt <= ?")
        params.append(filters.price_max)
    if filters.city:
        where.append("(s.city = ? OR s.city IS NULL)")
        params.append(filters.city)
    if filters.in_stock_only:
        where.append("(s.quantity > 0)")

    limit = max(1, min(filters.limit, 20))
    sql = f"""
        SELECT
            p.id, p.site_id, p.sku, p.name, p.brand, p.category, p.color, p.usage,
            p.volume, p.url, p.description, p.attributes_json, p.fetched_at,
            pr.price_kzt, pr.old_price_kzt, pr.discount_percent,
            s.city, s.quantity, s.status
        FROM products p
        LEFT JOIN prices pr ON pr.product_id = p.id
        LEFT JOIN stock s ON s.product_id = p.id
        WHERE {" AND ".join(where)}
        ORDER BY
            CASE WHEN s.quantity > 0 THEN 0 ELSE 1 END,
            CASE WHEN pr.price_kzt IS NULL THEN 1 ELSE 0 END,
            pr.price_kzt ASC,
            p.name ASC
        LIMIT ?
    """
    params.append(limit)

    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return attach_stock_by_city([_row_to_product(row) for row in rows], db_path=db_path)


def list_product_candidates(
    filters: ProductFilters,
    db_path: Path = CATALOG_DB_PATH,
    max_rows: int = 2000,
) -> list[dict[str, Any]]:
    """Return products with hard filters only, for fuzzy ranking in Python."""
    hard_filters = ProductFilters(
        query=None,
        city=filters.city,
        category=filters.category,
        brand=filters.brand,
        color=filters.color,
        usage=filters.usage,
        volume=filters.volume,
        price_min=filters.price_min,
        price_max=filters.price_max,
        in_stock_only=filters.in_stock_only,
        limit=max_rows,
    )
    return _search_products_uncapped(hard_filters, db_path=db_path, limit=max_rows)


def _search_products_uncapped(
    filters: ProductFilters,
    db_path: Path = CATALOG_DB_PATH,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    init_catalog_db(db_path)
    where = ["p.is_active = 1"]
    params: list[Any] = []

    _add_like(where, params, "p.brand", filters.brand)
    _add_like(where, params, "p.category", filters.category)
    _add_like(where, params, "p.color", filters.color)
    _add_like(where, params, "p.usage", filters.usage)
    _add_like(where, params, "p.volume", filters.volume)

    if filters.price_min is not None:
        where.append("pr.price_kzt >= ?")
        params.append(filters.price_min)
    if filters.price_max is not None:
        where.append("pr.price_kzt <= ?")
        params.append(filters.price_max)
    if filters.city:
        where.append("(s.city = ? OR s.city IS NULL)")
        params.append(filters.city)
    if filters.in_stock_only:
        where.append("(s.quantity > 0)")

    sql = f"""
        SELECT
            p.id, p.site_id, p.sku, p.name, p.brand, p.category, p.color, p.usage,
            p.volume, p.url, p.description, p.attributes_json, p.fetched_at,
            pr.price_kzt, pr.old_price_kzt, pr.discount_percent,
            s.city, s.quantity, s.status
        FROM products p
        LEFT JOIN prices pr ON pr.product_id = p.id
        LEFT JOIN stock s ON s.product_id = p.id
        WHERE {" AND ".join(where)}
        ORDER BY
            CASE WHEN s.quantity > 0 THEN 0 ELSE 1 END,
            CASE WHEN pr.price_kzt IS NULL THEN 1 ELSE 0 END,
            pr.price_kzt ASC,
            p.name ASC
        LIMIT ?
    """
    params.append(max(1, min(limit, 10000)))

    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return attach_stock_by_city([_row_to_product(row) for row in rows], db_path=db_path)


def _row_to_product(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["attributes"] = json.loads(item.pop("attributes_json") or "{}")
    except json.JSONDecodeError:
        item["attributes"] = {}
    return item


def attach_stock_by_city(products: list[dict[str, Any]], db_path: Path = CATALOG_DB_PATH) -> list[dict[str, Any]]:
    if not products:
        return products

    product_ids = sorted({int(product["id"]) for product in products if product.get("id") is not None})
    if not product_ids:
        return products

    with connect(db_path) as conn:
        city_rows = conn.execute("SELECT DISTINCT city FROM stock ORDER BY city").fetchall()
        cities = [str(row["city"]) for row in city_rows]

        placeholders = ",".join("?" for _ in product_ids)
        rows = conn.execute(
            f"""
            SELECT product_id, city, quantity, status
            FROM stock
            WHERE product_id IN ({placeholders})
            ORDER BY city
            """,
            product_ids,
        ).fetchall()

    stock_by_product: dict[int, dict[str, dict[str, Any]]] = {
        product_id: {
            city: {"quantity": 0, "status": "out_of_stock"}
            for city in cities
        }
        for product_id in product_ids
    }
    for row in rows:
        stock_by_product[int(row["product_id"])][str(row["city"])] = {
            "quantity": row["quantity"],
            "status": row["status"],
        }

    seen_product_ids: set[int] = set()
    unique_products: list[dict[str, Any]] = []
    for product in products:
        product_id = int(product["id"])
        if product_id in seen_product_ids:
            continue
        seen_product_ids.add(product_id)
        product["stock_by_city"] = stock_by_product.get(product_id, {})
        unique_products.append(product)
    return unique_products


def count_products(db_path: Path = CATALOG_DB_PATH) -> int:
    init_catalog_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM products").fetchone()
    return int(row["count"])


def list_promotions(limit: int = 8, db_path: Path = CATALOG_DB_PATH) -> list[dict[str, Any]]:
    init_catalog_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                id, title, summary, url, published_at, starts_at, ends_at,
                discount_text, fetched_at
            FROM promotions
            ORDER BY
                CASE WHEN published_at IS NULL THEN 1 ELSE 0 END,
                published_at DESC,
                id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 30)),),
        ).fetchall()
    return [dict(row) for row in rows]


def count_promotions(db_path: Path = CATALOG_DB_PATH) -> int:
    init_catalog_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM promotions").fetchone()
    return int(row["count"])

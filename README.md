# Colour Store Assistant

Telegram AI assistant for Centre Krasok #1. The bot answers company questions from static RAG data and uses dynamic catalog tools for prices, stock, product recommendations, and promotions.

## Stack

- Python 3.12
- aiogram
- DeepSeek Chat API for responses
- Gemini embeddings for static RAG
- Qdrant for vector search
- SQLite for dynamic catalog data
- Docker Compose for EC2 deployment

## Project Structure

```text
telegram_bot.py       Telegram polling bot
brain.py              Routing and answer generation
static_rag.py         Static Qdrant retrieval
catalog_scraper.py    Product and promotion scraper
catalog_db.py         SQLite schema and queries
catalog_tools.py      Product search and recommendation tools
promotion_tools.py    Promotion listing tool
session_store.py      Per-chat context storage
data/                 Static data, SQLite DB, session files
qdrant_data/          Qdrant persistent storage
```

## Environment

Copy `.env.example` to `.env` and fill in the keys:

```bash
cp .env.example .env
```

Required variables:

```env
TELEGRAM_BOT_TOKEN=
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
```

For Docker Compose, keep:

```env
QDRANT_HOST=qdrant
QDRANT_PORT=6333
CATALOG_DB_PATH=/app/data/catalog.sqlite3
SESSIONS_DIR=/app/data/sessions
```

## Local Docker Run

Build and start the bot with Qdrant:

```bash
docker compose up --build -d
```

View logs:

```bash
docker compose logs -f bot
```

Stop:

```bash
docker compose down
```

## Initial Data Load

Start Qdrant first:

```bash
docker compose up -d qdrant
```

Load static data into Qdrant:

```bash
docker compose run --rm bot python ingest_data.py --upsert
```

Load dynamic catalog data:

```bash
docker compose run --rm bot python catalog_scraper.py
```

Load promotions:

```bash
docker compose run --rm bot python catalog_scraper.py --promotions
```

For a quick test:

```bash
docker compose run --rm bot python catalog_scraper.py --limit 100 --progress-every 10
```

Resume from a later sitemap position:

```bash
docker compose run --rm bot python catalog_scraper.py --offset 1000 --progress-every 10
```

## EC2 Deployment

1. Create an EC2 instance with Docker and Docker Compose installed.
2. Clone the repository.
3. Create `.env` from `.env.example`.
4. Start Qdrant and load static data.
5. Run the product and promotion scrapers.
6. Start the bot.

```bash
docker compose up --build -d
docker compose run --rm bot python ingest_data.py --upsert
docker compose run --rm bot python catalog_scraper.py
docker compose run --rm bot python catalog_scraper.py --promotions
docker compose restart bot
```

## Scheduled Updates

Use host cron on EC2.

Example:

```cron
0 3 * * * cd /opt/colour-store-assistant && docker compose run --rm bot python catalog_scraper.py >> logs/catalog.log 2>&1
0 */4 * * * cd /opt/colour-store-assistant && docker compose run --rm bot python catalog_scraper.py --promotions >> logs/promotions.log 2>&1
```

Create the logs directory before adding cron:

```bash
mkdir -p logs
```

## Runtime Notes

- The bot uses Telegram polling.
- Session context is stored in `data/sessions`.
- Product, price, stock, and promotion data is stored in `data/catalog.sqlite3`.
- Qdrant data is persisted in `qdrant_data`.
- Do not commit `.env`, `qdrant_data`, or generated SQLite/session files.

## Useful Commands

Run a direct brain test:

```bash
docker compose run --rm bot python brain.py "какие у вас сейчас акции"
```

Check parsed product count:

```bash
docker compose run --rm bot python -c "from catalog_db import count_products; print(count_products())"
```

Check promotion count:

```bash
docker compose run --rm bot python -c "from catalog_db import count_promotions; print(count_promotions())"
```

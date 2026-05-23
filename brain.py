import argparse
import sys
from typing import Any

from catalog_tools import CatalogTools
from deepseek_client import DeepSeekClient
from promotion_tools import PromotionTools
from static_rag import format_static_context, retrieve_static_context


PROMOTION_KEYWORDS = (
    "акци",
    "скидк",
    "промо",
    "спецпредлож",
    "выгод",
)


STATIC_POLICY_KEYWORDS = (
    "вернуть",
    "возврат",
    "обмен",
    "поменять",
    "заменить",
    "гарант",
    "услов",
    "правил",
    "можно ли вернуть",
    "как вернуть",
    "вскрыл",
    "вскрыт",
    "открыл",
    "открыт",
    "не пригод",
    "непригод",
    "качество",
    "не понравилось",
    "не устроило",
    "брак",
    "бракован",
    "дефект",
    "поврежд",
    "претенз",
    "жалоб",
    "акт о браке",
)

STATIC_COMPANY_KEYWORDS = (
    "какие бренды",
    "какие именно бренды",
    "бренды",
    "марки",
    "производители",
    "ассортимент",
    "что производит",
    "кто ваши клиенты",
    "кто наши клиенты",
    "клиенты компании",
    "о компании",
)

COMPANY_DYNAMIC_INTENT_KEYWORDS = (
    "цена",
    "цену",
    "стоит",
    "стоимость",
    "налич",
    "остат",
    "есть ли",
    "купить",
    "подбери",
    "посовет",
    "порекомендуй",
    "тг",
    "kzt",
)


DYNAMIC_KEYWORDS = (
    "цена",
    "цену",
    "стоит",
    "стоимость",
    "налич",
    "остат",
    "есть ли",
    "товар",
    "артикул",
    "подбери",
    "посоветуй",
    "порекомендуй",
    "рекоменд",
    "фирм",
    "бренд",
    "цвет",
    "краск",
    "эмал",
    "лак",
    "грунт",
    "шпатлев",
    "штукатур",
    "тг",
    "kzt",
)

CITY_ALIASES = {
    "алматы": ("алматы", "алмат", "almaty"),
    "астана": ("астана", "астан", "astana"),
}

LOCATION_QUERY_KEYWORDS = (
    "адрес",
    "где",
    "наход",
    "магазин",
    "офис",
    "филиал",
    "бутик",
    "контакт",
    "телефон",
    "режим",
    "работа",
)

ALL_CITY_QUERY_KEYWORDS = (
    "все",
    "всех",
    "другие",
    "других",
    "филиалы",
    "магазины",
    "города",
    "городах",
)


SYSTEM_PROMPT = """
Ты внимательный консультант интернет-магазина Центр Красок #1.
Пиши на русском так, будто отвечаешь клиенту в Telegram: тепло, спокойно, по-человечески.
Не звучишь как отчет или база данных. Можно использовать короткие фразы вроде: "Проверил", "Нашел", "Сейчас вижу".
Не здоровайся повторно, если диалог уже идет.
Не обращайся к клиенту по имени в каждом сообщении. Имя можно использовать редко: в первом нормальном ответе или когда это действительно звучит естественно.
Если в последних сообщениях ассистент уже обращался по имени, не используй имя снова.
Используй только предоставленный контекст и результаты инструментов.
Цены, остатки, наличие, скидки и рекомендации не придумывай.
Если данных недостаточно, скажи честно и предложи простой следующий шаг.
Не начинай ответ с жирного заголовка. Не пиши длинные дисклеймеры.
Не пиши клиенту фразы вроде "из предоставленной информации", "судя по контексту", "в базе указано" или "по данным RAG".
Если контекст достаточен, отвечай сразу по сути, как консультант магазина, без объяснения источников.
Если клиент спрашивает, что компания производит, а по контексту это магазин, скажи мягко: "скорее точнее сказать, что мы не производим, а подбираем и продаем..." и дальше коротко перечисли ассортимент.
Если клиент спрашивает, кто клиенты компании, отвечай напрямую: частные клиенты для ремонта, дизайнеры, строители, декораторы и профессионалы отделочных работ. Не начинай с неуверенного "не совсем понятно".
Не используй эмодзи.
""".strip()


def normalize_city(value: str | None) -> str:
    text = (value or "").strip().lower()
    for canonical, aliases in CITY_ALIASES.items():
        if any(alias in text for alias in aliases):
            return canonical
    return text


def query_mentions_city(query: str) -> bool:
    lower = query.lower()
    return any(alias in lower for aliases in CITY_ALIASES.values() for alias in aliases)


def query_asks_all_locations(query: str) -> bool:
    lower = query.lower()
    return any(keyword in lower for keyword in ALL_CITY_QUERY_KEYWORDS)


def is_location_query(query: str) -> bool:
    lower = query.lower()
    return any(keyword in lower for keyword in LOCATION_QUERY_KEYWORDS)


def is_static_company_query(query: str) -> bool:
    lower = query.lower()
    if not any(keyword in lower for keyword in STATIC_COMPANY_KEYWORDS):
        return False
    return not any(keyword in lower for keyword in COMPANY_DYNAMIC_INTENT_KEYWORDS)


def document_city(payload: dict[str, Any]) -> str:
    city = payload.get("city")
    return normalize_city(str(city)) if city else ""


def prioritize_static_documents(documents: list[Any], city: str, query: str) -> list[Any]:
    user_city = normalize_city(city)
    if not documents or not user_city or not is_location_query(query):
        return documents

    if query_mentions_city(query) or query_asks_all_locations(query):
        return documents

    preferred = []
    neutral = []
    other_city = []
    for document in documents:
        payload = getattr(document, "payload", {}) or {}
        doc_city = document_city(payload)
        if not doc_city:
            neutral.append(document)
        elif doc_city == user_city:
            preferred.append(document)
        else:
            other_city.append(document)

    if preferred:
        return (preferred + neutral)[:5]
    return (neutral + other_city)[:5]


class ColourStoreBrain:
    def __init__(
        self,
        llm: DeepSeekClient | None = None,
        catalog_tools: CatalogTools | None = None,
        promotion_tools: PromotionTools | None = None,
    ):
        self.llm = llm or DeepSeekClient()
        self.catalog_tools = catalog_tools or CatalogTools(self.llm)
        self.promotion_tools = promotion_tools or PromotionTools()

    def ask(self, message: str, *, city: str = "Алматы", history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        route = self.route(message)
        if route == "promotions":
            tool_result = self.promotion_tools.list_current_promotions()
            answer = self._answer_with_promotions(message, tool_result, history=history)
            return {"route": route, "answer": answer, "tool_result": tool_result}

        if route == "dynamic":
            tool_result = self.catalog_tools.recommend_products(message, default_city=city)
            answer = self._answer_with_dynamic_data(message, tool_result, history=history)
            return {"route": route, "answer": answer, "tool_result": tool_result}

        try:
            documents = retrieve_static_context(message, limit=8)
            documents = prioritize_static_documents(documents, city, message)
            context = format_static_context(documents)
        except Exception as exc:
            answer = (
                "Похоже, база справочной информации сейчас недоступна. "
                f"Техническая ошибка: {type(exc).__name__}. "
                "Лучше уточнить этот вопрос у менеджера или повторить чуть позже."
            )
            return {"route": route, "answer": answer, "sources": []}

        answer = self._answer_with_static_context(message, context, city=city, history=history)
        return {
            "route": route,
            "answer": answer,
            "sources": [doc.__dict__ for doc in documents],
        }

    def route(self, message: str) -> str:
        lower = message.lower()
        if any(keyword in lower for keyword in STATIC_POLICY_KEYWORDS):
            return "static"
        if any(keyword in lower for keyword in PROMOTION_KEYWORDS):
            return "promotions"
        if is_static_company_query(message):
            return "static"
        if any(keyword in lower for keyword in DYNAMIC_KEYWORDS):
            return "dynamic"
        return "static"

    def _answer_with_static_context(
        self,
        message: str,
        context: str,
        *,
        city: str = "Алматы",
        history: list[dict[str, str]] | None = None,
    ) -> str:
        if not self.llm.is_configured:
            return f"DeepSeek не настроен. Найденный static context:\n\n{context}"

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend((history or [])[-6:])
        messages.append(
            {
                "role": "user",
                "content": (
                    "Ответь клиенту по static RAG context. "
                    "Если это вопрос про адрес, доставку, режим работы или условия, дай прямой ответ. "
                    f"Город клиента из постоянного контекста: {city}. "
                    "Если клиент спрашивает адрес, офис, магазин, бутик, режим работы или телефон, сначала отвечай только по городу клиента. "
                    "Не перечисляй другие города и филиалы, если клиент прямо не спросил про все города, другие филиалы или не назвал другой город. "
                    "Если клиент пишет 'офис', не придумывай отдельные офисы: используй адрес магазина или юридический адрес из контекста и формулируй аккуратно. "
                    "Не начинай с 'из предоставленной информации', 'судя по контексту' или похожих служебных фраз. "
                    "Отвечай так, будто это обычная переписка с клиентом: коротко, уверенно и по делу. "
                    "Сохраняй живой Telegram-тон: не слишком официально, без сухих заголовков.\n\n"
                    f"STATIC RAG CONTEXT:\n{context}\n\n"
                    f"ВОПРОС КЛИЕНТА:\n{message}"
                ),
            }
        )
        try:
            return self.llm.chat(messages, temperature=0.35, max_tokens=900)
        except Exception as exc:
            return (
                "Сейчас не получилось получить ответ от DeepSeek, но данные из базы я нашел. "
                f"Техническая ошибка: {type(exc).__name__}. Проверьте ключ/сеть и попробуйте еще раз."
            )

    def _answer_with_dynamic_data(
        self,
        message: str,
        tool_result: dict[str, Any],
        *,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        products = tool_result.get("products", [])
        if not products and tool_result.get("products_count_in_db", 0) == 0:
            return (
                "Каталог товаров пока пустой. Сначала нужно запустить парсер: "
                "`tbot\\Scripts\\python.exe catalog_scraper.py --limit 100` для теста "
                "или полный запуск без `--limit`."
            )

        if not self.llm.is_configured:
            return f"DeepSeek не настроен. Dynamic tool result:\n\n{tool_result}"

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend((history or [])[-6:])
        messages.append(
            {
                "role": "user",
                "content": (
                    "Сформируй живой ответ клиенту по результатам dynamic catalog tool.\n"
                    "Начни естественно, например: 'Проверил базу, сейчас вижу такие варианты...' "
                    "или 'Нашел несколько подходящих вариантов...'.\n"
                    "Не начинай с 'По вашему запросу' и не делай вид, что это отчет.\n"
                    "Не здоровайся и не обращайся по имени, если это уже было в последних сообщениях.\n"
                    "Если есть товары, покажи 2-5 лучших вариантов: название, объем/фасовка если есть, цену, наличие/город если есть.\n"
                    "Если у товара есть stock_by_city, показывай остатки по городам из этого поля, особенно Алматы и Астана. "
                    "Не ограничивай ответ только городом клиента, если в stock_by_city есть остатки по нескольким городам.\n"
                    "Если вариантов несколько одного товара по объему, можно сгруппировать их компактно.\n"
                    "Если товаров нет, скажи мягко, что по текущим фильтрам не нашлось, и предложи один-два простых варианта расширения.\n"
                    "Если цвет может требовать колеровку, упомяни это по-человечески, без длинного предупреждения.\n"
                    "В конце можно предложить уточнить площадь, поверхность или желаемый бренд, если это уместно.\n\n"
                    f"TOOL_RESULT:\n{tool_result}\n\n"
                    f"ВОПРОС КЛИЕНТА:\n{message}"
                ),
            }
        )
        try:
            return self.llm.chat(messages, temperature=0.35, max_tokens=900)
        except Exception as exc:
            return (
                "Я проверил базу, но DeepSeek сейчас не ответил. "
                f"Техническая ошибка: {type(exc).__name__}. Найдено товаров: {len(products)}."
            )

    def _answer_with_promotions(
        self,
        message: str,
        tool_result: dict[str, Any],
        *,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        promotions = tool_result.get("promotions", [])
        if not promotions and tool_result.get("promotions_count_in_db", 0) == 0:
            return (
                "База акций пока пустая. Запусти парсер акций: "
                "`tbot\\Scripts\\python.exe catalog_scraper.py --promotions`."
            )

        if not self.llm.is_configured:
            return f"DeepSeek не настроен. Promotions tool result:\n\n{tool_result}"

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend((history or [])[-6:])
        messages.append(
            {
                "role": "user",
                "content": (
                    "Ответь клиенту по списку акций живым Telegram-тоном.\n"
                    "Начни естественно: 'Сейчас посмотрел страницу акций...' или 'На сайте сейчас вижу такие акции...'.\n"
                    "Не здоровайся и не обращайся по имени, если это уже было в последних сообщениях.\n"
                    "Если у акции нет ends_at, не утверждай категорично, что она точно действует; лучше: 'на сайте опубликовано' или 'отображается на странице акций'.\n"
                    "Дай 3-6 свежих акций с датами и ссылками, без тяжелого официального стиля.\n\n"
                    f"PROMOTIONS_TOOL_RESULT:\n{tool_result}\n\n"
                    f"ВОПРОС КЛИЕНТА:\n{message}"
                ),
            }
        )
        try:
            return self.llm.chat(messages, temperature=0.35, max_tokens=900)
        except Exception as exc:
            return (
                "Я посмотрел список акций, но DeepSeek сейчас не ответил. "
                f"Техническая ошибка: {type(exc).__name__}. Найдено акций: {len(promotions)}."
            )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Test Colour Store assistant brain without Telegram.")
    parser.add_argument("message", nargs="*", help="User message. If empty, starts interactive mode.")
    parser.add_argument("--city", default="Алматы")
    args = parser.parse_args()

    brain = ColourStoreBrain()
    if args.message:
        result = brain.ask(" ".join(args.message), city=args.city)
        print(result["answer"])
        return

    print("Colour Store brain. Ctrl+C to exit.")
    while True:
        try:
            message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            continue
        result = brain.ask(message, city=args.city)
        print(result["answer"])


if __name__ == "__main__":
    main()

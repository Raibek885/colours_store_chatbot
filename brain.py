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
Не используй эмодзи.
""".strip()


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

        documents = retrieve_static_context(message)
        context = format_static_context(documents)
        answer = self._answer_with_static_context(message, context, history=history)
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
        if any(keyword in lower for keyword in DYNAMIC_KEYWORDS):
            return "dynamic"
        return "static"

    def _answer_with_static_context(
        self,
        message: str,
        context: str,
        *,
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

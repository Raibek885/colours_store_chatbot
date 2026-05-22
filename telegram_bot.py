import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.types import Message

from brain import ColourStoreBrain
from config import TELEGRAM_BOT_TOKEN
from session_store import SessionStore


logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
dp = Dispatcher()
brain = ColourStoreBrain()
sessions = SessionStore()


@dp.message(F.text)
async def handle_text(message: Message) -> None:
    if not message.text:
        return

    session = sessions.load(message.chat.id)
    user_text = message.text.strip()
    stage = session.get("onboarding_stage", "new")

    if user_text.startswith("/") and stage != "ready":
        reply = onboarding_prompt(session)
        sessions.append_message(session, "assistant", reply)
        sessions.save(session)
        await message.answer(reply)
        return

    if stage == "awaiting_name":
        sessions.append_message(session, "user", user_text)
        name = sessions.set_name(session, user_text)
        session["onboarding_stage"] = "awaiting_city"
        reply = f"Очень приятно, {name}. Подскажите, пожалуйста, ваш город?"
        sessions.append_message(session, "assistant", reply)
        sessions.save(session)
        await message.answer(reply)
        return

    if stage == "awaiting_city":
        sessions.append_message(session, "user", user_text)
        city = sessions.set_city(session, user_text)
        session["onboarding_stage"] = "ready"
        reply = f"Отлично, буду ориентироваться на {city}. Чем могу помочь?"
        sessions.append_message(session, "assistant", reply)
        sessions.save(session)
        await message.answer(reply)
        return

    if stage == "new":
        sessions.append_message(session, "user", user_text)
        session["onboarding_stage"] = "awaiting_name"
        reply = "Здравствуйте! Я помогу с товарами, наличием, ценами и вопросами по магазину. Как я могу к вам обращаться?"
        sessions.append_message(session, "assistant", reply)
        sessions.save(session)
        await message.answer(reply)
        return

    if not session.get("name"):
        sessions.append_message(session, "user", user_text)
        name = sessions.set_name(session, user_text)
        session["onboarding_stage"] = "awaiting_city"
        reply = f"Очень приятно, {name}. Подскажите, пожалуйста, ваш город?"
        sessions.append_message(session, "assistant", reply)
        sessions.save(session)
        await message.answer(reply)
        return

    if not session.get("city"):
        sessions.append_message(session, "user", user_text)
        city = sessions.set_city(session, user_text)
        session["onboarding_stage"] = "ready"
        reply = f"Отлично, буду ориентироваться на {city}. Чем могу помочь?"
        sessions.append_message(session, "assistant", reply)
        sessions.save(session)
        await message.answer(reply)
        return

    route = brain.route(user_text)
    lead = lead_message(route, session)
    if lead:
        await message.answer(lead)

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    llm_history = sessions.llm_history(session)
    if lead:
        llm_history.append({"role": "assistant", "content": lead})
    try:
        result = await asyncio.to_thread(
            brain.ask,
            user_text,
            city=session["city"],
            history=llm_history,
        )
        reply = result["answer"]
    except Exception:
        logging.exception("Failed to handle message")
        reply = "Извините, сейчас не получилось обработать вопрос. Попробуйте еще раз чуть позже."

    sessions.append_message(session, "user", user_text)
    if lead:
        sessions.append_message(session, "assistant", lead)
    sessions.append_message(session, "assistant", reply)
    sessions.save(session)
    await answer_in_chunks(message, reply)


def lead_message(route: str, session: dict) -> str | None:
    if route == "dynamic":
        return "Сейчас проверю базу по наличию и ценам, минутку."
    if route == "promotions":
        return "Сейчас посмотрю, какие акции отображаются на сайте."
    return None


async def answer_in_chunks(message: Message, text: str, chunk_size: int = 3900) -> None:
    text = text or "Не нашел подходящий ответ."
    if len(text) <= chunk_size:
        await message.answer(text, disable_web_page_preview=True)
        return

    parts = []
    current = ""
    for paragraph in text.split("\n"):
        candidate = f"{current}\n{paragraph}".strip() if current else paragraph
        if len(candidate) > chunk_size:
            if current:
                parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)

    for part in parts:
        await message.answer(part, disable_web_page_preview=True)


def onboarding_prompt(session: dict) -> str:
    stage = session.get("onboarding_stage", "new")
    if stage == "new":
        session["onboarding_stage"] = "awaiting_name"
        return "Здравствуйте! Я помогу с товарами, наличием, ценами и вопросами по магазину. Как я могу к вам обращаться?"
    if stage == "awaiting_name" or not session.get("name"):
        return "Как я могу к вам обращаться?"
    if stage == "awaiting_city" or not session.get("city"):
        return f"Очень приятно, {session['name']}. Подскажите, пожалуйста, ваш город?"
    return "Чем могу помочь?"


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not bot:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in .env")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

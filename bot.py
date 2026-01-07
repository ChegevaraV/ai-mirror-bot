import os
import logging
from typing import Dict, List, Tuple

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

# ---------- Logging (so Render shows what's happening) ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai-mirror-bot")

# ---------- Env ----------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN (Telegram token). Add it in Render Environment Variables.")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY. Add it in Render Environment Variables.")

client = OpenAI(api_key=OPENAI_API_KEY)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # cheap & fast for demo

# ---------- Minimal memory (prototype) ----------
# per user: short running summary + last turns
USER_SUMMARY: Dict[int, str] = {}
USER_TURNS: Dict[int, List[Tuple[str, str]]] = {}

MAX_TURNS = 8  # keep last turns (user/assistant)


SYSTEM_PROMPT = """Ты AI-MIRROR.
Твоя функция — не утешать и не давать советов, а отражать структуру мышления.

Правила:
- Не давай психологической поп-терминологии и "успокаивания".
- Не говори человеку, что делать. Не лечи. Не ставь диагнозы.
- Делай карту процесса: триггер → эмоция/телесный сигнал → мысль → искажение/паттерн → импульс → действие.
- Показывай, где выбор был автоматическим.
- Если пользователь просит "что мне делать", отвечай: "Я могу отзеркалить варианты и последствия, но решение за тобой."
- Завершай ответ 1 вопросом на углубление (мягко, но точно).
"""

KEYBOARD = ReplyKeyboardMarkup(
    [["/mirror", "/reset"], ["/summary"]],
    resize_keyboard=True
)

def _get_history(user_id: int) -> List[dict]:
    summary = USER_SUMMARY.get(user_id, "")
    turns = USER_TURNS.get(user_id, [])

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if summary:
        messages.append({"role": "system", "content": f"Краткая память пользователя (саммари): {summary}"})

    for role, content in turns:
        messages.append({"role": role, "content": content})

    return messages

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "AI-MIRROR online.\n\n"
        "Пиши ситуацию — я отзеркалю процесс мышления.\n"
        "Команды:\n"
        "/mirror — режим зеркала (по умолчанию)\n"
        "/summary — показать текущую память\n"
        "/reset — стереть память\n",
        reply_markup=KEYBOARD,
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    USER_SUMMARY.pop(user_id, None)
    USER_TURNS.pop(user_id, None)
    await update.message.reply_text("Память очищена.", reply_markup=KEYBOARD)

async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    summary = USER_SUMMARY.get(user_id, "(пока пусто)")
    await update.message.reply_text(f"Текущее саммари:\n{summary}", reply_markup=KEYBOARD)

async def mirror_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Ок. Опиши ситуацию 2–10 предложениями: что произошло, что ты чувствуешь, что хочешь сделать.",
        reply_markup=KEYBOARD
    )

async def _update_memory(user_id: int) -> None:
    """Occasionally compress last turns into a short summary."""
    turns = USER_TURNS.get(user_id, [])
    if len(turns) < 10:
        return

    messages = [
        {"role": "system", "content": "Сожми в 5–7 строк: контекст, повторяющиеся паттерны, типовые триггеры. Без советов."}
    ]
    for role, content in turns[-10:]:
        messages.append({"role": role, "content": content})

    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.2,
    )
    new_summary = resp.choices[0].message.content.strip()

    prev = USER_SUMMARY.get(user_id, "")
    USER_SUMMARY[user_id] = (prev + "\n" + new_summary).strip()

    # keep fewer turns after summarizing
    USER_TURNS[user_id] = turns[-6:]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    # store user message
    USER_TURNS.setdefault(user_id, []).append(("user", text))
    USER_TURNS[user_id] = USER_TURNS[user_id][-MAX_TURNS:]

    messages = _get_history(user_id)

    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.3,
    )
    answer = resp.choices[0].message.content.strip()

    # store assistant message
    USER_TURNS[user_id].append(("assistant", answer))
    USER_TURNS[user_id] = USER_TURNS[user_id][-MAX_TURNS:]

    await update.message.reply_text(answer, reply_markup=KEYBOARD)

    # update memory sometimes
    try:
        await _update_memory(user_id)
    except Exception as e:
        logger.warning("Memory update failed: %s", repr(e))

def main() -> None:
    logger.info("BOOT: starting ai-mirror-bot")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("summary", summary_cmd))
    app.add_handler(CommandHandler("mirror", mirror_cmd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("BOOT: running polling")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()



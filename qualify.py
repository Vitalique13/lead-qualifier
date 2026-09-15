"""
Квалификация входящей заявки: текст клиента -> строгий JSON.

Прототип к тестовому заданию AI Solutions Architect (AI Business Lab).
Смысл прототипа: показать не то, что модель умеет отвечать, а то, что
её ответ проверяется программой и не ломает процесс, когда модель ошиблась.

Запуск без ключа API (показывает логику и проверку):
    python qualify.py --demo

Запуск с реальной моделью:
    export LLM_API_KEY=...
    python qualify.py examples/lead_1.txt
"""

import argparse
import json
import os
import sys

API_URL = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
API_KEY = os.environ.get("LLM_API_KEY")

ALLOWED_STATUS = {"qualified", "unqualified", "needs_clarification"}
REQUIRED_KEYS = ("budget_status", "project_scope_summary", "red_flags")


def load_prompt(path="system_prompt.md"):
    with open(path, encoding="utf-8") as f:
        return f.read()


def validate(raw):
    """Проверяем ответ модели. Возвращаем (данные, ошибка).

    Инструкция в промпте — это просьба, а не гарантия. Гарантию даёт вот эта
    функция: если ответ не той формы, дальше по процессу он не уйдёт.
    """
    text = raw.strip()

    # Частая поломка: модель оборачивает JSON в ```json ... ```
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"ответ не разобрался как JSON: {e}"

    if not isinstance(data, dict):
        return None, "ожидался объект JSON"

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        return None, f"нет обязательных ключей: {', '.join(missing)}"

    if data["budget_status"] not in ALLOWED_STATUS:
        return None, (
            f"budget_status = '{data['budget_status']}', "
            f"допустимы только: {', '.join(sorted(ALLOWED_STATUS))}"
        )

    if not isinstance(data["project_scope_summary"], str) or not data["project_scope_summary"].strip():
        return None, "project_scope_summary должен быть непустой строкой"

    if not isinstance(data["red_flags"], list):
        return None, "red_flags должен быть списком"

    if not all(isinstance(x, str) for x in data["red_flags"]):
        return None, "элементы red_flags должны быть строками"

    return data, None


def ask_model(system_prompt, user_text, correction=None):
    import requests

    if not API_KEY:
        sys.exit("Не задан LLM_API_KEY. Для просмотра логики запустите: python qualify.py --demo")

    messages = [
        {"role": "system", "content": system_prompt},
        # Текст клиента идёт отдельным сообщением и никогда не подмешивается
        # в системную инструкцию. Это защита от попыток клиента переписать
        # правила прямо в своём сообщении.
        {"role": "user", "content": user_text},
    ]
    if correction:
        messages.append({
            "role": "user",
            "content": f"Предыдущий ответ не прошёл проверку: {correction}. "
                       f"Верни только корректный JSON по заданной структуре.",
        })

    r = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0,            # оценка должна быть воспроизводимой
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def qualify(user_text, system_prompt):
    """Один повтор при ошибке, потом — человеку. Процесс не останавливается."""
    raw = ask_model(system_prompt, user_text)
    data, err = validate(raw)

    if err:
        print(f"[проверка не прошла] {err} — повторяю запрос", file=sys.stderr)
        raw = ask_model(system_prompt, user_text, correction=err)
        data, err = validate(raw)

    if err:
        print(f"[проверка не прошла повторно] {err} — заявка уходит человеку", file=sys.stderr)
        return {
            "budget_status": "needs_clarification",
            "project_scope_summary": "Автоматическая оценка не удалась, нужна проверка менеджером.",
            "red_flags": ["Не удалось получить корректный ответ модели"],
            "_fallback": True,
        }

    return data


DEMO = [
    ("examples/lead_1.txt", {
        "budget_status": "qualified",
        "project_scope_summary": "Компании нужна система учёта заказов с приёмом заявок от дилеров, "
                                 "расчётом себестоимости и интеграциями с 1С и складом. "
                                 "Сейчас учёт ведётся в Excel и данные расходятся.",
        "red_flags": [
            "Расстались с предыдущим подрядчиком из-за срыва сроков",
            "Жёсткий срок запуска — до конца квартала",
            "Две интеграции со стороны клиента, сроки зависят от его готовности",
        ],
    }),
    ("examples/lead_2.txt", {
        "budget_status": "needs_clarification",
        "project_scope_summary": "Клиент хочет разработать приложение. Ни платформа, ни состав работ, "
                                 "ни бюджет не названы.",
        "red_flags": ["Запрос прайса до обсуждения задачи", "Объём работ не определён"],
    }),
    ("examples/lead_3.txt", {
        "budget_status": "unqualified",
        "project_scope_summary": "Клиент просит починить форму обратной связи на сайте. "
                                 "Объём работ — около часа.",
        "red_flags": ["Объём работ заведомо ниже минимального чека компании"],
    }),
]


def run_demo():
    """Прогон без API: показывает, что проверка ответа работает как задумано."""
    for path, answer in DEMO:
        with open(path, encoding="utf-8") as f:
            print(f"\n--- {path} ---\n{f.read().strip()}\n")
        data, err = validate(json.dumps(answer, ensure_ascii=False))
        print(json.dumps(data, ensure_ascii=False, indent=2))

    print("\n--- проверка отбраковывает плохие ответы ---")
    for bad in [
        'Конечно! Вот результат: {"budget_status": "qualified"}',
        '{"budget_status": "maybe", "project_scope_summary": "x", "red_flags": []}',
        '{"budget_status": "qualified", "project_scope_summary": "x", "red_flags": "нет"}',
    ]:
        _, err = validate(bad)
        print(f"  {bad[:58]:<60} -> {err}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file", nargs="?", help="файл с сообщением клиента")
    p.add_argument("--demo", action="store_true", help="запуск без ключа API")
    args = p.parse_args()

    if args.demo or not args.file:
        run_demo()
    else:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
        print(json.dumps(qualify(text, load_prompt()), ensure_ascii=False, indent=2))

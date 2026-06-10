import json
import os
import asyncio

from langchain_core.tools import tool
from langgraph.types import Command
from langchain_core.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage

from typing_extensions import Annotated
from playwright.async_api import Page
from urllib.parse import urlparse

from core.config import settings


def get_scout_tools(page: Page) -> list:

    browser_lock = asyncio.Lock()

    @tool
    async def scan_form_signature() -> str:
        """
        Возвращает компактную сигнатуру формы/CTA на текущей странице.
        Используй как критерий: если есть форма, страница считается финальной для scout.
        """
        if settings.LLM_CALL_LOG:
            print(" -> Инструмент: Сканирую сигнатуру формы...")

        js_code = """
        () => {
            const isVisible = (el) => {
                if (!el) return false;
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
            };

            const countVisible = (selector) =>
                Array.from(document.querySelectorAll(selector)).filter(isVisible).length;

            const textList = (selector, limit = 10) =>
                Array.from(document.querySelectorAll(selector))
                    .filter(isVisible)
                    .map(el => {
                        const txt = (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '')
                            .trim()
                            .replace(/\\s+/g, ' ');
                        return txt.slice(0, 80);
                    })
                    .filter(Boolean)
                    .slice(0, limit);

            const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
            const submitCandidates = Array.from(
                document.querySelectorAll('button, input[type="submit"], input[type="button"], a[role="button"], [role="button"]')
            ).filter(isVisible).filter(el => {
                const t = (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '')
                    .trim()
                    .toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                return (
                    type === 'submit' ||
                    ['отклик', 'apply', 'submit', 'send', 'join', 'career', 'работ', 'respond', 'отправ'].some(k => t.includes(k))
                );
            });

            return {
                url: location.href,
                title: document.title || "",
                form_count: countVisible('form'),
                input_count: inputs.length,
                textarea_count: countVisible('textarea'),
                select_count: countVisible('select'),
                file_input_count: inputs.filter(el => (el.getAttribute('type') || '').toLowerCase() === 'file').length,
                submit_texts: submitCandidates.map(el => (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '')
                    .trim()
                    .replace(/\\s+/g, ' ')
                    .slice(0, 80)),
                heading_texts: textList('h1, h2, h3, [role="heading"]', 10),
                cta_texts: textList('a, button, [role="button"]', 10),
            };
        }
        """
        data = await page.evaluate(js_code)

        data["has_form_like"] = (
            data.get("form_count", 0) > 0
            or data.get("input_count", 0) > 0
            or data.get("textarea_count", 0) > 0
            or data.get("select_count", 0) > 0
            or data.get("file_input_count", 0) > 0
            or len(data.get("submit_texts", [])) > 0
        )

        return json.dumps(data, ensure_ascii=False, indent=2)

    @tool
    async def extract_search_elements() -> str:
        """Возвращает поля поиска и навигационные кнопки. Используй, чтобы найти селектор для ввода запроса."""
        if settings.LLM_CALL_LOG:
            print("\n -> [Scout DOM] Ищу поля поиска и фильтры...")

        js_code = """
        () => {
            const elements = document.querySelectorAll("input, button, [role='button']");
            return Array.from(elements).map(el => {
                let botId = el.getAttribute('data-bot-id');
                if (!botId) {
                    botId = 'bot_' + Math.random().toString(36).substr(2, 9);
                    el.setAttribute('data-bot-id', botId);
                }
                let text = el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : "";
                let aria = el.getAttribute("aria-label") || "";
                let val = el.value || "";
                let placeholder = el.placeholder || "";

                let combined = [aria, text, val].filter(t => t && t.length > 0);
                let finalLabel = [...new Set(combined)].join(" | ");
                if (!finalLabel) finalLabel = placeholder || "Без названия";
                if (finalLabel.length > 60) finalLabel = finalLabel.slice(0, 60) + "…";

                return {
                    tag: el.tagName.toLowerCase(),
                    type: el.type || el.getAttribute('role') || null,
                    name: el.name || null,
                    placeholder: placeholder ? placeholder.slice(0, 60) : null,
                    label_text: finalLabel,
                    selector: `[data-bot-id="${botId}"]`,
                    _isVisible: el.offsetWidth > 0 || el.offsetHeight > 0,
                    _isSubmit: el.type === 'submit'
                };
            });
        }
        """
        elements_data = await page.evaluate(js_code)
        visible = [
            el
            for el in elements_data
            if el.get("type") != "hidden" and el.pop("_isVisible", True)
        ]

        # Скоринг специально под ПОИСК (Игнорируем ФИО, Резюме)
        def score(el):
            label = (el.get("label_text") or "").lower()
            name_attr = (el.get("name") or "").lower()
            placeholder = (el.get("placeholder") or "").lower()
            combined = f"{label} {name_attr} {placeholder}"

            # Убиваем мусорные кнопки
            if any(
                j in label
                for j in [
                    "подробнее",
                    "показать еще",
                    "show more",
                    "далее",
                    "откликнуться",
                    "apply",
                ]
            ):
                return -1

            search_keywords = [
                "search",
                "поиск",
                "найти",
                "q",
                "query",
                "название",
                "професс",
                "специальност",
                "должность",
                "направлени",
            ]

            # Поля поиска — максимальный приоритет
            if el.get("tag") == "input" and any(k in combined for k in search_keywords):
                return 100
            if el.get("tag") == "input":
                return 50
            if el.get("_isSubmit") or any(k in combined for k in search_keywords):
                return 40

            return 0

        for el in visible:
            el["_score"] = score(el)
        filtered = [el for el in visible if el["_score"] >= 0]
        filtered.sort(key=lambda x: x["_score"], reverse=True)

        top_k = filtered[:15]
        for el in top_k:
            el.pop("_score", None)
            el.pop("_isSubmit", None)

        return json.dumps(top_k, ensure_ascii=False, indent=2)

    @tool
    async def extract_links() -> str:
        """Возвращает список всех важных ссылок на странице.
        Используй, чтобы найти страницы 'Контакты', 'Карьера', 'Вакансии', 'О нас'."""
        if settings.LLM_CALL_LOG:
            print(" -> Инструмент: Ищу полезные ссылки...")
        js_code = """
        () => {
            const links = document.querySelectorAll("a");
            return Array.from(links)
                .filter(a => a.innerText.trim().length > 0) // Только ссылки с текстом
                .map(a => ({
                    text: a.innerText.trim().replace(/\\n/g, ' '),
                    href: a.href
                }))
                // Убираем мусорные дубликаты
                .filter((v, i, a) => a.findIndex(t => (t.href === v.href)) === i);
        }
        """
        links = await page.evaluate(js_code)
        # LLM лопнет, если дать ей все ссылки. Фильтруем ключевые слова:
        keywords = [
            "контакт",
            "карьера",
            "ваканс",
            "о нас",
            "contact",
            "career",
            "job",
            "hr",
            "join",
            "vacanc",
        ]
        useful_links = [
            link
            for link in links
            if any(
                k in link["text"].lower() or k in link["href"].lower() for k in keywords
            )
        ]

        if not useful_links:
            return "Полезных ссылок для навигации не найдено. Попробуй поискать форму на текущей странице."
        return json.dumps(useful_links, ensure_ascii=False, indent=2)

    @tool  # надо тут делать защиту, чтобы на 404 страницу не прыгали и в возврате для ллм писать, что он неправильно сконструировал ссылку, пусть тогда вызовет скан и найдёт нормальную ссылку
    async def goto_url(target_url: str) -> str:
        """Переходит по указанному URL. Аргументы: target_url."""
        if target_url.strip("/") == page.url.strip("/"):
            return (
                "ОШИБКА: Ты УЖЕ находишься на этой странице! Вызови extract_form_html."
            )
        if settings.LLM_CALL_LOG:
            print(f" -> Инструмент: Проверяю доступность '{target_url}' (фоновый пинг)")

        # print(f" -> Инструмент: Перехожу на '{target_url}'...")
        try:
            ping_response = await page.context.request.get(target_url)

            if ping_response.status in [404, 400, 403, 500, 502]:
                return (
                    f"ОШИБКА {ping_response.status}: Эндпоинта не существует или доступ запрещен. "
                    f"Ты неправильно сконструировал ссылку! "
                    f"Я остался на текущей странице, чтобы не сбить твои фильтры. "
                    f"Попробуй собрать ссылку иначе или вызови scan_and_extract_vacancies."
                )
            if settings.LLM_CALL_LOG:
                print(
                    f" -> Инструмент: Ссылка рабочая (Статус {ping_response.status}), перехожу..."
                )
            # await page.goto(target_url, wait_until="networkidle")
            await page.goto(target_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            return f"Успешно перешел на {target_url}"
        except Exception as e:
            return f"Ошибка при переходе: {str(e)}"

    @tool
    async def fill_input_field(selector: str, value: str) -> str:
        """Вводит текст в поле. Аргументы: selector (CSS селектор поля), value (текст для ввода)."""
        async with browser_lock:
            if settings.LLM_CALL_LOG:
                print(f" -> Инструмент: Ввожу '{value}' в селектор '{selector}'...")
            try:
                # wait_for проверяет, что поле действительно видимо и интерактивно
                await page.wait_for_selector(selector, state="visible", timeout=3000)
                await page.fill(selector, value)
                return f"Успешно введено '{value}'"
            except Exception as e:
                return f"Ошибка при вводе в '{selector}': {str(e)}"

    @tool
    async def click_element(selector: str) -> str:
        """Кликает по элементу (кнопке). Аргументы: selector (CSS селектор)."""
        async with browser_lock:
            if settings.LLM_CALL_LOG:
                print(f" -> Инструмент: Кликаю по '{selector}'...")
            try:
                await page.wait_for_selector(selector, state="attached", timeout=3000)
                await page.click(selector, force=True)
                return f"Успешно кликнуто по {selector}"
            except Exception as e:
                try:
                    await page.evaluate(f"document.querySelector('{selector}').click()")
                    return f"Успешно кликнуто через JS по {selector}"
                except Exception as js_e:
                    return f"Ошибка при клике по '{selector}': {str(e)}"

    @tool
    async def scroll_down() -> str:
        """Прокручивает страницу вниз. Используй это, если не можешь найти нужную вакансию или форму, так как они могут подгружаться динамически при скроллинге (бесконечный скролл)."""
        if settings.LLM_CALL_LOG:
            print(" -> Инструмент: Скроллю страницу вниз...")
        try:
            # Скроллим на 1 экран вниз
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            # Ждем, пока JS подтянет новые данные
            await page.wait_for_timeout(2000)
            return "Страница прокручена вниз. Вызови extract_links или extract_form_html снова, чтобы увидеть новые элементы."
        except Exception as e:
            return f"Ошибка при скроллинге: {str(e)}"

    @tool
    async def search_and_capture_api(input_selector: str, search_text: str) -> str:
        """
        Используй этот инструмент для ПОИСКА В КАТАЛОГЕ вакансий.
        Он одновременно вводит текст и ловит API-ответ сервера.
        Аргументы:
        - input_selector: селектор поля ввода (строки поиска).
        - search_text: короткий текст для поиска (например, 'Python').
        """
        if settings.LLM_CALL_LOG:
            print(
                f"\n -> [API Interceptor] Начинаю ввод '{search_text}' и прослушку трафика..."
            )

        captured_jsons = []

        async def handle_response(response):
            try:
                if (
                    response.status == 200
                    and "application/json" in response.headers.get("content-type", "")
                ):
                    if any(
                        junk in response.url.lower()
                        for junk in ["metrika", "analytics", "tracking", "log", "cdn"]
                    ):
                        return
                    data = await response.json()
                    size = len(str(data))
                    if size > 500:
                        captured_jsons.append(
                            {"url": response.url, "size": size, "data": data}
                        )
            except Exception:
                pass

        # 1. ВКЛЮЧАЕМ ПРОСЛУШКУ (до начала ввода!)
        page.on("response", handle_response)

        # 2. ВВОДИМ ТЕКСТ (Медленно, чтобы реактивные сайты успели триггернуться)
        try:
            locator = page.locator(input_selector).first
            await locator.wait_for(state="visible", timeout=3000)
            await locator.clear()
            await locator.press_sequentially(search_text, delay=100)
            if settings.LLM_CALL_LOG:
                print(f" -> [API Interceptor] Текст введен.")

            await locator.press("Enter")
        except Exception as e:
            page.remove_listener("response", handle_response)
            return f"ОШИБКА при взаимодействии с полем поиска: {e}"

        # Ждем пока сервер ответит
        await page.wait_for_timeout(3500)

        # 3. ОТКЛЮЧАЕМ ПРОСЛУШКУ
        page.remove_listener("response", handle_response)

        if not captured_jsons:
            return (
                "API данные не пойманы. Попробуй ещё раз, но с другим текстом поиска."
            )

        captured_jsons.sort(key=lambda x: x["size"], reverse=True)
        best_match = captured_jsons[0]

        # --- УМНЫЙ СЖИМАТЕЛЬ JSON ДЛЯ LLM ---
        def shrink_json(obj):
            if isinstance(obj, dict):
                new_obj = {}
                for k, v in obj.items():
                    # Выкидываем откровенный мусор и тяжелые тексты
                    if k.lower() in [
                        "description",
                        "requirements",
                        "responsibilities",
                        "conditions",
                        "html",
                        "body",
                        "text",
                        "introduction",
                    ]:
                        continue
                    # Выкидываем любые строки длиннее 100 символов
                    if isinstance(v, str) and len(v) > 100:
                        continue
                    new_obj[k] = shrink_json(v)
                return new_obj
            elif isinstance(obj, list):
                return [shrink_json(item) for item in obj]
            else:
                return obj

        shrunk_data = shrink_json(best_match["data"])
        raw_json_str = json.dumps(shrunk_data, ensure_ascii=False)
        truncated = raw_json_str[:4000] + ("..." if len(raw_json_str) > 4000 else "")

        parsed_url = urlparse(page.url)
        base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"

        return (
            f"УСПЕШНО! Данные перехвачены.\n"
            f"ИНСТРУКЦИЯ ПО СБОРКЕ ССЫЛКИ:\n"
            f"1. Выбери из JSON вакансию, которая точнее всего совпадает с искомой.\n"
            f"2. Найди её 'id' (например, 12345).\n"
            f"3. СКОНСТРУИРУЙ ССЫЛКУ ТОЛЬКО ПО ID! Очисти url от лишних параметров и добавь ID, новая url строится на основе страницы каталога, то есть каталог/ + id из апи, но каталог может иметь разный эндпоинт и разное название, смотри на своё положение и конструируй от него. Не переходи по ссылке, если не можешь сделать правильную ссылку. Если проблемы, вызывай скан страницы через scan_and_extract_vacancies и ищи конкретные ссылки\n"
            f"Если у тебя домен выглядит как /search, то не склеивай ссылку типа /vacancies/id, она будет неправильной, потому что эндпоинт другой в каталоге."
            f"4. ИГНОРИРУЙ поля 'url', 'slug', 'path' внутри JSON (они ведут на 404). БЕРИ ТОЛЬКО ID!\n"
            f"5. Исключение: если в JSON есть готовая ссылка, начинающаяся с 'http' (например, https://...), используй её.\n"
            f"Собрав ссылку, проверь её через goto_url.\n\n"
            f"СЫРЫЕ ДАННЫЕ:\n{truncated}"
        )

    @tool
    async def scan_and_extract_vacancies() -> str:
        """
        Используй этот инструмент для КАТАЛОГОВ БЕЗ ПОИСКА.
        Он сканирует страницу, прокручивает её (если нужно) и собирает ссылки на все карточки вакансий.
        """
        if settings.LLM_CALL_LOG:
            print(
                "\n -> [DOM Scraper] Инструмент: Сканирую каталог вакансий на лету..."
            )

        js_code = """
        async () => {
            const maxScrolls = 5;
            const linksMap = new Map();
            
            const collectLinks = () => {
                document.querySelectorAll("a[href]").forEach(a => {
                    // ЕСЛИ ССЫЛКА ПУСТАЯ (Оверлей как в Яндексе), БЕРЕМ ТЕКСТ КАРТОЧКИ (РОДИТЕЛЯ)
                    let text = a.textContent ? a.textContent.trim() : "";
                    if (!text && a.parentElement) {
                        text = a.parentElement.textContent ? a.parentElement.textContent.trim() : "";
                    }
                    text = text.replace(/\\s+/g, ' ');
                    
                    if (text.length > 5 && a.href) {
                        linksMap.set(a.href, text);
                    }
                });
            };
            
            const clickShowMore = () => {
                const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                const keywords = ['показать ещ', 'загрузить ещ', 'show more', 'load more', 'далее'];
                for (let btn of buttons) {
                    const text = (btn.textContent || "").toLowerCase();
                    if (keywords.some(k => text.includes(k)) && btn.offsetParent !== null) {
                        try { btn.click(); return true; } catch(e) {}
                    }
                }
                return false;
            };
            
            collectLinks(); 
            
            for (let i = 0; i < maxScrolls; i++) {
                let prevHeight = document.body.scrollHeight;
                let clicked = clickShowMore();
                
                window.scrollTo(0, document.body.scrollHeight);
                await new Promise(r => setTimeout(r, 1000));
                
                collectLinks();
                
                if (!clicked && document.body.scrollHeight === prevHeight) {
                    await new Promise(r => setTimeout(r, 800));
                    if (document.body.scrollHeight === prevHeight) break;
                }
            }
            
            return Array.from(linksMap.entries()).map(([href, text]) => ({text, href}));
        }
        """

        try:
            links = await page.evaluate(js_code)
        except Exception as e:
            return f"Ошибка при сканировании страницы: {e}"

        # Жесткая очистка на Python от навигационного мусора
        junk_words = [
            "о компании",
            "контакты",
            "политика",
            "соглашение",
            "cookie",
            "главная",
            "войти",
            "регистрация",
            "забыли пароль",
            "vk",
            "telegram",
            "youtube",
            "dzen",
            "условия",
            "блог",
        ]

        cleaned_links = []
        for link in links:
            text_lower = link["text"].lower()
            href_lower = link["href"].lower()

            if any(junk in text_lower for junk in junk_words):
                continue
            if href_lower.endswith(("#", "#page")):
                continue
            if "?" in link["href"]:
                if len(link["href"].split("?")[1]) > 15:
                    continue

            cleaned_links.append(link)

        if not cleaned_links:
            return "Не найдено ссылок на вакансии. Возможно, страница пуста. Попробуй поискать в другом разделе."

        cleaned_links.sort(key=lambda x: len(x["text"]), reverse=True)

        return json.dumps(cleaned_links[:20], ensure_ascii=False, indent=2)

    @tool(description="Завершает работу scout и передаёт финальный URL.")
    async def finish_scout_task(
        final_url: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Терминальный выход из scout-агента."""
        return Command(
            update={
                "messages": [
                    ToolMessage(content="SCOUT_DONE", tool_call_id=tool_call_id)
                ],
                "pipeline_state": {
                    "done": True,
                    "phase": "scout_done",
                    "final_url": final_url,
                },
            },
            goto="__end__",
        )

    return [
        scan_form_signature,
        extract_search_elements,
        extract_links,
        goto_url,
        click_element,
        scroll_down,
        search_and_capture_api,
        scan_and_extract_vacancies,
        finish_scout_task,
    ]


def get_applier_tools(page: Page) -> list:

    browser_lock = asyncio.Lock()

    @tool
    async def extract_form_html() -> str:
        """Возвращает сгруппированную структуру полей ввода и кнопок."""
        if settings.LLM_CALL_LOG:
            print(
                "\n -> [DOM Test] Инструмент: Собираю структуру и раздаю уникальные bot-ID..."
            )

        js_code = """
        () => {
            const elements = document.querySelectorAll("input, textarea, button, select, [role='button']");

            return Array.from(elements).map(el => {
                let botId = el.getAttribute('data-bot-id');
                if (!botId) {
                    botId = 'bot_' + Math.random().toString(36).substr(2, 9);
                    el.setAttribute('data-bot-id', botId);
                }

                let text = el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : "";
                let aria = el.getAttribute("aria-label") || "";
                let val = el.value || "";
                let placeholder = el.placeholder || "";

                let label = "";
                if (el.labels && el.labels.length > 0) {
                    label = Array.from(el.labels).map(l => l.textContent.trim()).join(" ");
                } else if (el.id) {
                    const l = document.querySelector(`label[for="${el.id}"]`);
                    if (l) label = l.textContent.trim();
                }

                let combined = [aria, label, text, val].filter(t => t && t.length > 0);
                let finalLabel = [...new Set(combined)].join(" | ");

                if (!finalLabel) {
                    const cls = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : "";
                    if (cls.includes('reset') || cls.includes('clear')) finalLabel = "[Иконка: Очистить]";
                    else if (cls.includes('search')) finalLabel = "[Иконка: Поиск]";
                    else if (cls.includes('close')) finalLabel = "[Иконка: Закрыть]";
                    else finalLabel = "Без названия";
                }

                // Обрезаем label до 60 символов
                if (finalLabel.length > 60) finalLabel = finalLabel.slice(0, 60) + "…";

                const isFile = el.type === 'file';
                const isVisible = el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0;

                let groupTitle = "Основная форма / Без группы";
                let curr = el;
                let depth = 0;
                while (curr && curr !== document.body && depth < 5) {
                    let sibling = curr.previousElementSibling;
                    while (sibling) {
                        if (sibling.tagName.match(/^H[1-6]$/) || sibling.tagName === 'LEGEND' ||
                            (sibling.className && typeof sibling.className === 'string' && 
                             sibling.className.toLowerCase().includes('title'))) {
                            groupTitle = sibling.textContent.trim().replace(/\\s+/g, ' ').slice(0, 60);
                            break;
                        }
                        sibling = sibling.previousElementSibling;
                    }
                    if (groupTitle !== "Основная форма / Без группы") break;
                    curr = curr.parentElement;
                    depth++;
                }

                return {
                    group: groupTitle,
                    tag: el.tagName.toLowerCase(),
                    type: el.type || el.getAttribute('role') || null,
                    name: el.name || null,
                    placeholder: placeholder ? placeholder.slice(0, 60) : null,
                    label_text: finalLabel,
                    selector: `[data-bot-id="${botId}"]`,
                    _isVisible: isVisible || isFile,
                    _isFile: isFile,
                    _isSubmit: el.type === 'submit',
                };
            });
        }
        """
        elements_data = await page.evaluate(js_code)

        # Убираем hidden и невидимые
        visible = [
            el
            for el in elements_data
            if el.get("type") != "hidden" and el.pop("_isVisible", True)
        ]

        # Top-K скоринг
        RELEVANT_KEYWORDS = [
            "name",
            "email",
            "phone",
            "tel",
            "message",
            "resume",
            "cover",
            "имя",
            "фамил",
            "почт",
            "телефон",
            "резюме",
            "сообщен",
            "согласи",
            "privacy",
            "персональн",
        ]
        JUNK_LABELS = [
            "голосовой поиск",
            "поиск",
            "search",
            "подробнее",
            "показать",
            "show more",
            "далее",
        ]
        NAME_ATTRS = [
            "firstname",
            "lastname",
            "surname",
            "email",
            "phone",
            "tel",
            "message",
            "resume",
        ]

        def score(el):
            if el.get("_isFile"):
                return 100
            if el.get("type") in ["checkbox", "radio"]:
                return 95
            if el.get("_isSubmit"):
                return 60

            label = (el.get("label_text") or "").lower()
            name = (el.get("name") or "").lower()
            name_attr = (el.get("name") or "").lower()
            placeholder = (el.get("placeholder") or "").lower()
            combined = f"{label} {name} {placeholder}"

            # Явный мусор — выкидываем
            if any(j in label for j in JUNK_LABELS):
                return -1

            if any(kw in name_attr for kw in NAME_ATTRS):
                return 60
            if any(kw in combined for kw in RELEVANT_KEYWORDS):
                return 50
            if el.get("tag") == "textarea":
                return 40  # текстовые области скорее всего нужны
            if el.get("tag") in ("input", "select"):
                return 20  # любой input лучше чем кнопка без названия
            if el.get("tag") == "button":
                return 5

            return 0

        for el in visible:
            el["_score"] = score(el)

        # Убираем явный мусор (score == -1) и сортируем
        filtered = [el for el in visible if el["_score"] >= 0]
        filtered.sort(key=lambda x: x["_score"], reverse=True)

        # Берём топ-20, но всегда сохраняем file и submit
        must_have = [el for el in filtered if el["_score"] >= 90]
        rest = [el for el in filtered if el["_score"] < 90]
        top_k = (must_have + rest)[:20]

        # Убираем служебные поля перед отдачей агенту
        for el in top_k:
            el.pop("_score", None)
            el.pop("_isFile", None)
            el.pop("_isSubmit", None)

        # Группируем
        grouped_data = {}
        for el in top_k:
            group = el.pop("group")
            if group not in grouped_data:
                grouped_data[group] = []
            grouped_data[group].append(el)

        dumped = json.dumps(grouped_data, ensure_ascii=False, indent=2)
        if settings.LLM_CALL_LOG:
            print(
                f"=== [DOM РЕЗУЛЬТАТ] Сгруппировано {len(top_k)} элементов по {len(grouped_data)} зонам ==="
            )
            print(dumped[:800] + "\n... (обрезано для логов)")

        return dumped

    @tool
    async def submit_form(selector: str) -> str:
        """
        Нажимает ФИНАЛЬНУЮ кнопку отправки формы (Submit / Откликнуться).
        Используй этот инструмент ВМЕСТО click_element для отправки резюме.
        """
        from core.config import settings
        from urllib.parse import urlparse

        async with browser_lock:
            try:
                await page.wait_for_selector(selector, state="visible", timeout=3000)

                await page.locator(selector).scroll_into_view_if_needed()
                await page.wait_for_timeout(1000)

                os.makedirs("screenshots", exist_ok=True)
                domain = urlparse(page.url).netloc.replace("www.", "")
                screenshot_path = f"screenshots/{domain}_form.png"

                await page.screenshot(path=screenshot_path, full_page=True)

                if settings.DRY_RUN:
                    return f"Отправка отменена (DRY_RUN). Форма заполнена, скриншот сохранен в {screenshot_path}"

                await page.click(selector, force=True)
                return f"Успешно нажата кнопка отправки {selector}"

            except Exception as e:
                return f"Ошибка при отправке формы: {str(e)}"

    @tool
    async def fill_input_field(selector: str, value: str) -> str:
        """Вводит текст в поле. Аргументы: selector (CSS селектор поля), value (текст для ввода)."""
        async with browser_lock:
            if settings.LLM_CALL_LOG:
                print(f" -> Инструмент: Ввожу '{value}' в селектор '{selector}'...")
            try:
                # wait_for проверяет, что поле действительно видимо и интерактивно
                await page.wait_for_selector(selector, state="visible", timeout=3000)
                await page.fill(selector, value)
                return f"Успешно введено '{value}'"
            except Exception as e:
                return f"Ошибка при вводе в '{selector}': {str(e)}"

    @tool
    async def click_element(selector: str) -> str:
        """Кликает по элементу (кнопке). Аргументы: selector (CSS селектор)."""
        async with browser_lock:
            if settings.LLM_CALL_LOG:
                print(f" -> Инструмент: Кликаю по '{selector}'...")
            try:
                await page.wait_for_selector(selector, state="attached", timeout=3000)
                await page.click(selector, force=True)
                return f"Успешно кликнуто по {selector}"
            except Exception as e:
                try:
                    await page.evaluate(f"document.querySelector('{selector}').click()")
                    return f"Успешно кликнуто через JS по {selector}"
                except Exception as js_e:
                    return f"Ошибка при клике по '{selector}': {str(e)}"

    @tool
    async def scroll_down() -> str:
        """Прокручивает страницу вниз. Используй это, если не можешь найти нужную вакансию или форму, так как они могут подгружаться динамически при скроллинге (бесконечный скролл)."""
        if settings.LLM_CALL_LOG:
            print(" -> Инструмент: Скроллю страницу вниз...")
        try:
            # Скроллим на 1 экран вниз
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            # Ждем, пока JS подтянет новые данные
            await page.wait_for_timeout(2000)
            return "Страница прокручена вниз. Вызови extract_links или extract_form_html снова, чтобы увидеть новые элементы."
        except Exception as e:
            return f"Ошибка при скроллинге: {str(e)}"

    @tool
    async def upload_file(selector: str, file_path: str) -> str:
        """Загружает файл в поле <input type="file">. Аргументы: selector (CSS селектор поля), file_path (путь к файлу)."""
        async with browser_lock:
            if settings.LLM_CALL_LOG:
                print(f"Инструмент: Загружаю файл '{file_path}' в '{selector}'")
            try:
                await page.wait_for_selector(selector, state="attached", timeout=3000)

                # Создадим фейковый файл для теста, если его физически нет на диске
                if not os.path.exists(file_path):
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("Это тестовое резюме Ивана Смирнова.")

                # Специальный метод Playwright для загрузки файлов
                await page.set_input_files(selector, file_path)
                return f"Успешно загружен файл '{file_path}'"
            except Exception as e:
                return f"Ошибка при загрузке файла: {str(e)}"

    @tool
    async def fill_form_batch(plan: str) -> str:
        """
        Заполняет форму по плану за один вызов.
        plan — JSON строка с полями:
        {
          "fill": {"selector": "value", ...},
          "upload": "selector" или null,
          "check": ["selector", ...],
        }
        Кнопку submit НЕ нажимает — это делается отдельно через click_element.
        """
        async with browser_lock:
            try:
                data = json.loads(plan)
            except Exception as e:
                return f"Ошибка парсинга плана: {e}"

            results = []

            # 1. Текстовые поля
            for selector, value in data.get("fill", {}).items():
                try:
                    await page.wait_for_selector(
                        selector, state="visible", timeout=3000
                    )
                    await page.fill(selector, str(value))
                    results.append(f"✓ fill {selector}")
                except Exception as e:
                    results.append(f"✗ fill {selector}: {e}")

            # 2. Загрузка файла
            upload_selector = data.get("upload")
            if upload_selector:
                try:
                    file_path = data.get("file_path", "resume.pdf")
                    if not os.path.exists(file_path):
                        with open(file_path, "w") as f:
                            f.write("Тестовое резюме.")
                    await page.wait_for_selector(
                        upload_selector, state="attached", timeout=3000
                    )
                    await page.set_input_files(upload_selector, file_path)
                    results.append(f"✓ upload {upload_selector}")
                except Exception as e:
                    results.append(f"✗ upload {upload_selector}: {e}")

            # 3. Чекбоксы согласия
            for selector in data.get("check", []):
                try:
                    await page.wait_for_selector(
                        selector, state="attached", timeout=3000
                    )
                    try:
                        await page.check(selector, timeout=1000, force=True)
                    except:
                        await page.evaluate(
                            f"""
                            (sel) => {{
                                const el = document.querySelector(sel);
                                if (!el) return;
                                el.click(); // Симулируем клик на сам элемент
                                if (el.labels && el.labels.length > 0) {{
                                    el.labels[0].click(); // Симулируем клик на его label
                                }}
                            }}
                        """,
                            selector,
                        )
                    results.append(f"✓ check {selector}")
                except Exception as e:
                    results.append(f"✗ check {selector}: {e}")

            summary = "\n".join(results)
            if settings.LLM_CALL_LOG:
                print(f"[fill_form_batch] Результат:\n{summary}")
            return f"Форма заполнена:\n{summary}\n\nТеперь вызови submit_form для кнопки submit."

    @tool(description="Завершает работу applier и передаёт итоговый статус.")
    async def finish_applier_task(
        status: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Терминальный выход из applier-агента."""
        return Command(
            update={
                "messages": [
                    ToolMessage(content="APPLIER_DONE", tool_call_id=tool_call_id)
                ],
                "pipeline_state": {
                    "done": True,
                    "phase": "applier_done",
                    "status": status,
                },
            },
            goto="__end__",
        )

    return [
        extract_form_html,
        fill_form_batch,
        click_element,
        submit_form,
        finish_applier_task,
    ]

import json
import asyncio
from urllib.parse import urlparse
from playwright.async_api import Page
from services.llm_client import submit_prompt


class CatalogProcessor:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def _log(self, message: str):
        """вывод логов"""
        if self.verbose:
            print(message)

    def _clean_json(self, text: str) -> str:
        if not text:
            return "{}"
        return text.replace("```json", "").replace("```", "").strip()

    async def generate_search_queries(
        self,
        target_job: str,
        vacancy_title: str | None = None,
        vacancy_snippet: str | None = None,
    ) -> list[str]:
        """запросы для строки поиска на сайте компании"""
        label = vacancy_title or target_job
        self._log(f"[CatalogProcessor] Генерирую запросы каталога для '{label}'...")
        try:
            response = await submit_prompt(
                template_name="catalog_query_generator",
                context_vars={
                    "target_job": target_job,
                    "vacancy_title": vacancy_title,
                    "vacancy_snippet": vacancy_snippet,
                },
                task_name="gen_query",
                json_mode=True,
            )
            clean_text = self._clean_json(response)
            data = json.loads(clean_text)
            queries = data.get("queries", [label])
            self._log(f"[CatalogProcessor] LLM придумала запросы: {queries}")
            return queries
        except Exception as e:
            self._log(f"[CatalogProcessor] ошибка генерации запросов: {e}")
            return [label]

    async def scrape_vacancy_links(self, page: Page) -> dict:
        """скролл и сбор ссылок в каталоге"""
        self._log("[CatalogProcessor] сбор ссылок")

        js_code = """
        async () => {
            const maxScrolls = 4;
            const linksMap = new Map();
            const currentDomain = window.location.hostname.replace('www.', '');
            
            const collectLinks = () => {
                document.querySelectorAll("a").forEach(a => {
                    let href = a.getAttribute('href');
                    if (!href) return;
                    
                    let fullUrl = '';
                    try { 
                        fullUrl = new URL(href, window.location.href).href.split('#')[0]; 
                    } catch(e) { return; }

                    if (fullUrl.startsWith('javascript') || fullUrl.startsWith('mailto') || fullUrl.startsWith('tel')) return;

                    try {
                        if (!new URL(fullUrl).hostname.includes(currentDomain)) return;
                    } catch(e) { return; }

                    let text = (a.innerText || "").trim();
                    let aria = (a.getAttribute('aria-label') || "").trim();
                    let title = (a.getAttribute('title') || "").trim();
                    let name = (a.getAttribute('name') || "").trim();
                    
                    if (text.length < 5) {
                        text = [text, aria, title, name].filter(Boolean).join(' | ');
                    }
                    
                    if (text.length < 20 && a.parentElement) {
                        let parent = a.parentElement;
                        for (let d = 0; d < 3 && parent; d++) {
                            let pText = (parent.innerText || "").trim();
                            if (pText.length > text.length && pText.length < 200) {
                                text = pText;
                            }
                            parent = parent.parentElement;

                        }
                    }
                    
                    text = text.replace(/\\s+/g, ' ').substring(0, 200); 
                    
                    // Сохраняем в мапу. Если ссылка уже есть, оставляем тот вариант, где текста больше
                    if (text.length > 5) {
                        if (!linksMap.has(fullUrl) || linksMap.get(fullUrl).length < text.length) {
                            linksMap.set(fullUrl, text);
                        }
                    }
                });
            };
            
            const clickShowMore = () => {
                const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                const keywords = ['показать ещ', 'загрузить ещ', 'show more', 'load more', 'далее', 'больше'];
                for (let btn of buttons) {
                    const text = (btn.textContent || "").toLowerCase();
                    if (btn.offsetWidth > 0 && btn.offsetHeight > 0 && keywords.some(k => text.includes(k))) {
                        if (btn.tagName.toLowerCase() === 'a') {
                            const href = btn.getAttribute('href');
                            if (href && href !== '#' && !href.startsWith('javascript')) {
                                continue;
                            }
                        }
                        try { btn.click(); return true; } catch(e) {}

                    }
                }
                return false;
            };
            
            collectLinks(); 
            for (let i = 0; i < maxScrolls; i++) {
                if (!document || !document.body) break;
                let prevHeight = document.body.scrollHeight;
                let clicked = clickShowMore();
                
                if (!document || !document.body) break;
                window.scrollTo(0, document.body.scrollHeight);
                await new Promise(r => setTimeout(r, 1200)); 
                collectLinks();
                
                if (!document || !document.body) break;
                if (!clicked && document.body.scrollHeight === prevHeight) {
                    await new Promise(r => setTimeout(r, 1000));
                    if (!document || !document.body) break;
                    if (document.body.scrollHeight === prevHeight) break; 
                }
            }
            return Object.fromEntries(linksMap);
        }
        """

        raw_links = await page.evaluate(js_code)

        junk_urls = []

        junk_texts = []

        clean_links = {}
        for url, title in raw_links.items():
            url_lower = url.lower()
            title_lower = title.lower()

            if any(junk in url_lower for junk in junk_urls):
                continue
            if any(junk in title_lower for junk in junk_texts):
                continue

            clean_links[url] = title

        self._log(f"[CatalogProcessor] собрано уникальных ссылок: {len(clean_links)}")
        if clean_links:
            self._log("[CatalogProcessor] пример найденных ссылок:")
            for url, title in list(clean_links.items())[:3]:
                self._log(f"   - {title[:60]}... -> {url}")

        return clean_links

    async def find_and_fill_search(self, page: Page, query: str) -> bool:
        """ввод в строку поиска в каталоге"""
        js_search_finder = """
        () => {
            const inputs = Array.from(document.querySelectorAll('input[type="text"], input[type="search"]'));
            let bestInput = null;
            let bestScore = -1;
            
            for (let el of inputs) {
                const isVisible = el.offsetWidth > 0 && el.offsetHeight > 0;
                if (!isVisible) continue;
                
                let score = 0;
                const combined = `${el.name || ''} ${el.placeholder || ''} ${el.className || ''}`.toLowerCase();
                
                if (combined.includes('search') || combined.includes('поиск') || combined.includes('query')) score += 10;
                if (combined.includes('vacancy') || combined.includes('ваканс')) score += 15;
                if (combined.includes('email') || combined.includes('name') || combined.includes('phone')) score -= 50;
                
                if (score > bestScore) {
                    bestScore = score;
                    bestInput = el;
                }
            }
            
            if (bestInput && bestScore >= 0) {
                // Уникальный ID для Playwright
                if (!bestInput.id) bestInput.id = 'bot-search-input-' + Math.random().toString(36).substr(2, 9);
                return '#' + bestInput.id;
            }
            return null;
        }
        """

        selector = await page.evaluate(js_search_finder)
        if not selector:
            return False

        self._log(f"[CatalogProcessor] найдена строка поиска, ввод '{query}'...")
        try:
            await page.fill(selector, "")  # очищаем
            await page.type(selector, query, delay=50)
            await page.press(selector, "Enter")
            await page.wait_for_timeout(2000)
            return True
        except Exception as e:
            self._log(f"[CatalogProcessor] Ошибка при вводе в поиск: {e}")
            return False

    async def get_search_input_selector(self, page: Page) -> str | None:
        """ищем селектор поиска"""
        js_search_finder = """
        () => {
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"]):not([type="email"]):not([type="password"])'));
            let bestInput = null; let bestScore = -1;
            
            const searchKeywords = ['search', 'поиск', 'найти', 'q', 'query', 'название', 'професс', 'специальност', 'должность', 'направлени', 'кого ищете', 'ключевое'];
            const junkKeywords = ['email', 'name', 'phone', 'password', 'имя', 'телефон', 'почта', 'сообщение', 'подписк', 'newsletter'];

            for (let el of inputs) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                
                let score = 0;
                const combined = `${el.name || ''} ${el.placeholder || ''} ${el.className || ''} ${el.getAttribute('aria-label') || ''}`.toLowerCase();
                
                for (let k of searchKeywords) { if (combined.includes(k)) score += 20; }
                if (combined.includes('vacancy') || combined.includes('ваканс')) score += 15;
                for (let j of junkKeywords) { if (combined.includes(j)) score -= 50; }
                
                if (el.type === 'search') score += 10;
                if (el.type === 'text') score += 5;
                if (rect.width > 50) score += 5;

                if (score > bestScore) { bestScore = score; bestInput = el; }
            }
            
            // Если набралось хотя бы 5 баллов (обычное текстовое поле без мусора)
            if (bestInput && bestScore >= 5) {
                // Вешаем свой атрибут вместо изменения ID
                const botId = 'bot-search-' + Math.random().toString(36).substr(2, 9);
                bestInput.setAttribute('data-bot-id', botId);
                return `[data-bot-id="${botId}"]`;
            }
            return null;
        }
        """
        return await page.evaluate(js_search_finder)

    async def process_catalog(
        self, page: Page, catalog_url: str, target_job: str, queries: list[str] = None
    ) -> dict:
        self._log(
            f"\n{'='*50}\n[ЭТАП: ОБРАБОТКА КАТАЛОГА]\nURL: {catalog_url}\n{'='*50}"
        )

        await page.goto(catalog_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        try:
            words_to_click = [
                "Сбросить",
                "Очистить",
                "Все города",
                "Любой город",
                "Любой регион",
                "Россия",
            ]
            for word in words_to_click:
                elements = page.locator(
                    f"button:has-text('{word}'), a:has-text('{word}'), [role='button']:has-text('{word}')"
                )
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    if await el.is_visible():
                        self._log(f"   -> Нажимаю кнопку: '{word}'")
                        await el.click(force=True)
                        await page.wait_for_timeout(1000)

            crosses = page.locator(
                "button[aria-label*='сброс' i], button[aria-label*='очист' i], button[class*='clear' i]"
            )
            count = await crosses.count()
            for i in range(count):
                el = crosses.nth(i)
                if await el.is_visible():
                    await el.click(force=True)
                    await page.wait_for_timeout(500)

        except Exception as e:
            self._log(f"   [!] Ошибка при сбросе фильтров: {e}")

        await page.wait_for_timeout(1000)

        if not queries:
            queries = await self.generate_search_queries(target_job)
        else:
            self._log(f"[CatalogProcessor] Использую готовые запросы: {queries}")

        search_selector = await self.get_search_input_selector(page)

        all_collected_links = {}

        for query in queries:
            self._log(f"   -> Ищу: '{query}'")
            try:
                search_selector = await self.get_search_input_selector(page)

                if not search_selector:
                    self._log(
                        "[CatalogProcessor] строка поиска не найдена. собираю всё подряд"
                    )
                    links = await self.scrape_vacancy_links(page)
                    all_collected_links.update(links)
                    break

                locator = page.locator(search_selector).first
                await locator.scroll_into_view_if_needed()
                await locator.focus()
                await locator.fill("")

                await locator.press_sequentially(query, delay=100)
                await page.wait_for_timeout(500)
                await locator.press("Enter")

                await page.wait_for_timeout(3000)

                links = await self.scrape_vacancy_links(page)
                all_collected_links.update(links)

            except Exception as e:
                self._log(f"   Ошибка при поиске '{query}': {e}")

        if not all_collected_links:
            self._log("[CatalogProcessor] ссылок не найдено.")
            return {
                "url": None,
                "title": "",
                "reason": "на странице каталога не собрано ни одной ссылки "
                "(не отрендерилось / карточки не-анкорные)",
                "fail_kind": "scrape_empty",
            }

        final_links_list = [
            {"title": title, "url": url} for url, title in all_collected_links.items()
        ]

        self._log(
            f"[CatalogProcessor] всего собрано уникальных ссылок: {len(final_links_list)}"
        )
        for item in final_links_list[:3]:
            self._log(f"   - {item['title'][:45]}... -> {item['url']}")

        self._log("\n[CatalogProcessor] отправляю пул ссылок в LLM для выбора...")
        try:
            response = await submit_prompt(
                template_name="catalog_vacancy_selector",
                context_vars={
                    "target_job": target_job,
                    "links_json": json.dumps(final_links_list, ensure_ascii=False),
                },
                task_name="vacancy_selector",
                json_mode=True,
            )
            clean_text = self._clean_json(response)
            data = json.loads(clean_text)

            if isinstance(data, dict):
                selected_url = data.get("selected_url")
                reason = data.get("reason", "")
                title = data.get("title", "")
            else:
                selected_url = None
                reason = "LLM не вернула JSON объект"
                title = ""

            if selected_url:
                self._log(
                    f"[CatalogProcessor] LLM выбрала:\n   [{title}]\n   {selected_url}"
                )
                self._log(f"   Причина: {reason}")
                return {
                    "url": selected_url,
                    "title": title,
                    "reason": reason,
                    "fail_kind": None,
                }

            self._log(f"[CatalogProcessor] LLM ответила null. причина: {reason}")
            return {
                "url": None,
                "title": "",
                "reason": reason or "релевантной вакансии в каталоге не найдено",
                "fail_kind": "no_match",
            }

        except Exception as e:
            self._log(f"[CatalogProcessor] ошибка LLM: {e}")
            return {
                "url": None,
                "title": "",
                "reason": f"ошибка LLM-селектора: {e}",
                "fail_kind": "error",
            }

import asyncio
import json
import re
import collections
import random
import time
from urllib.parse import urlparse
from playwright.async_api import async_playwright, BrowserContext


from services.page_analyzer import analyze_page, analyze_external_form
from services.router_service import route_site

MAX_CONCURRENT_TABS = 2
MAX_PAGES_TO_VISIT = 20
BANNED_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rar",
    ".jpg",
    ".png",
)


async def wait_page_ready(page, timeout=15000):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except:
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except:
        pass

    await page.wait_for_timeout(1500)


_EMAIL_JUNK_KEYWORDS = [
    "press",
    "pr@",
    "marketing",
    "sales",
    "event",
    "support",
    "partner",
    "no-reply",
    "noreply",
]
_EMAIL_HR_KEYWORDS = [
    "hr",
    "job",
    "career",
    "resume",
    "talent",
    "team",
    "cv",
    "recruit",
    "people",
]
_EMAIL_GENERIC_KEYWORDS = [
    "info",
    "contact",
    "hello",
    "mail",
]


def _score_email(email: str) -> int:
    e = email.lower()
    if any(kw in e for kw in _EMAIL_JUNK_KEYWORDS):
        return -100
    if any(kw in e for kw in _EMAIL_HR_KEYWORDS):
        return 100
    if any(kw in e for kw in _EMAIL_GENERIC_KEYWORDS):
        return 50
    return 0


def choose_best_emails(emails: set) -> list[str]:
    clean = [
        e
        for e in emails
        if not e.startswith("u00")
        and not any(e.lower().endswith(ext) for ext in BANNED_EXTENSIONS)
    ]
    non_junk = [e for e in clean if _score_email(e) >= 0]
    return sorted(non_junk, key=_score_email, reverse=True)


def get_scent_score(href: str, text: str) -> int:
    """Функция 'нюха', чтобы паук бежал в сторону вакансий, а не новостей"""
    parsed = urlparse(href)
    # combined = f"{href} {text}".lower() # тут проблема была с тем, что домен может содержать слова для регекса и просто автоматом всем давать высокий балл
    combined = f"{parsed.path} {parsed.query} {text}".lower()

    if re.search(
        r"(blog|news|press|login|auth|policy|cookie|article|novost|spravoch|analitika|faq)",
        combined,
    ):
        return -100
    if re.search(
        r"[\?&](direction|directionids|specialization|specializationids|sort|napravlenie)=",
        href.lower(),
    ):
        return -100
    if re.search(
        r"(send-resume|anketa|apply|откликнуться|отклик|анкета|resume)", combined
    ) or re.search(
        r"\b(cv)\b", combined
    ):  # |cv
        return 100
    if re.search(
        r"(vacancy|vacancie|vacancies|vacan|career|вакансии|ваканс|вакансия|работа|карьера|join|vakansii|soiskatel)",  # |hr|job|jobs
        combined,
    ) or re.search(r"\b(hr|job|jobs)\b", combined):
        return 80
    if re.search(r"(about|team|company|о нас|контакт|contacts|search)", combined):
        return 40
    return 10


class SmartCrawler:
    def __init__(self, start_url: str, verbose: bool = False):
        self.start_url = start_url
        self.base_domain = urlparse(start_url).netloc.replace("www.", "")

        self.verbose = verbose

        self.queue = asyncio.PriorityQueue()
        self.visited = set()
        self.findings = []

        self.emails: set[str] = set()

        self.stop_event = asyncio.Event()
        self.pages_visited = 0
        self.lock = asyncio.Lock()

        self.parents_map = collections.defaultdict(set)

        self.load_semaphore = asyncio.Semaphore(2)
        self.cooldown_until = 0.0
        self.cooldown_lock = asyncio.Lock()
        self.needs_restart = False

    def _log(self, message: str):
        """Внутренняя функция для принтов"""
        if self.verbose:
            print(message)

    async def trigger_cooldown(self, seconds: int):
        async with self.cooldown_lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)

    async def wait_if_cooling_down(self):
        while True:
            async with self.cooldown_lock:
                remaining = self.cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.5))

    async def _worker(self, context: BrowserContext, worker_id: int):
        page = await context.new_page()

        while not self.stop_event.is_set():
            try:
                priority, depth, url, parent_url = await asyncio.wait_for(
                    self.queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            async with self.lock:
                if url in self.visited or self.pages_visited >= MAX_PAGES_TO_VISIT:
                    self.queue.task_done()
                    if self.pages_visited >= MAX_PAGES_TO_VISIT:
                        self.stop_event.set()
                    continue
                self.visited.add(url)
                self.pages_visited += 1
                page_id = self.pages_visited

            # print(
            #    f"   [Worker {worker_id}] #{self.pages_visited} | Глубина: {depth} | Запах: {-priority} | {url}"
            # )

            try:
                try:
                    await asyncio.sleep(random.uniform(0.3, 1.2))
                    await self.wait_if_cooling_down()
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception as nav_e:
                    self._log(
                        f"   [Worker {worker_id}] GOTO ERROR #{page_id} | {url} | {type(nav_e).__name__}: {nav_e}"
                    )

                await page.wait_for_timeout(2000)

                if depth == 0:
                    actual_domain = urlparse(page.url).netloc.replace("www.", "")
                    if actual_domain != self.base_domain:
                        self._log(
                            f"   [Worker {worker_id}] Редирект на старте: {self.base_domain} -> {actual_domain}"
                        )
                        self.base_domain = actual_domain

                await page.evaluate("""
                    async () => {
                        window.scrollTo(0, document.body.scrollHeight / 2);
                        await new Promise(r => setTimeout(r, 400));
                        window.scrollTo(0, document.body.scrollHeight);
                        await new Promise(r => setTimeout(r, 600));
                        window.scrollTo(0, 0); 
                    }
                """)
                await page.wait_for_timeout(1000)

                # сбор почт
                try:
                    page_emails = await page.evaluate("""() => {
                        const text = document.body.innerText || '';
                        const hrefs = Array.from(document.querySelectorAll('a[href^="mailto:"]'))
                            .map(a => a.href.replace('mailto:', '').split('?')[0].trim());
                        const inText = text.match(/[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}/g) || [];
                        return [...new Set([...hrefs, ...inText])];
                    }""")
                    if page_emails:
                        async with self.lock:
                            self.emails.update(page_emails)
                except Exception:
                    pass

                if not self.stop_event.is_set():
                    try:

                        # 'header, footer, nav, [class*="header" i], [class*="footer" i], [class*="nav" i], [id*="header" i], [id*="footer" i]'

                        links = await page.evaluate("""
                            () => {
                                const ignoreSelector = [
                                    'header', 'footer', 'nav', 
                                    '#t-header', '#t-footer',
                                    '[data-tilda-page-alias="header"]', '[data-tilda-page-alias="footer"]', '[data-tilda-page-alias="footer-html"]',
                                    '.uc-scrollmenu', '.t280', '.t794', '.t450', '.t461',
                                    '[class*="header" i]', '[class*="footer" i]', '[class*="nav" i]', 
                                    '[id*="header" i]', '[id*="footer" i]', 
                                ].join(', ');
                                
                                return Array.from(document.querySelectorAll("a[href]")).map(a => {
                                    const isNav = !!a.closest(ignoreSelector);
                                    // Учитываем, что ссылка должна быть видимой
                                    const isVisible = a.offsetWidth > 0 || a.offsetHeight > 0 || a.getClientRects().length > 0;
                                    return {
                                        text: a.innerText.trim(), 
                                        href: a.href,
                                        isNav: isNav,
                                        isVisible: isVisible
                                    };
                                });
                            }
                        """)

                        is_start_page = url == self.start_url

                        for link in links:
                            if not is_start_page and link["isNav"]:
                                continue
                            href = link["href"].split("#")[0]  # .rstrip("/")
                            if not href or href.startswith(
                                ("javascript:", "mailto:", "tel:")
                            ):
                                continue
                            if any(
                                href.lower().split("?")[0].endswith(ext)
                                for ext in BANNED_EXTENSIONS
                            ):
                                continue

                            domain = urlparse(href).netloc.replace("www.", "")
                            if domain != self.base_domain and not domain.endswith(
                                self.base_domain
                            ):
                                continue

                            score = get_scent_score(href, link["text"])

                            async with self.lock:
                                self.parents_map[href].add(url)

                            if score > 0 and href not in self.visited:
                                await self.queue.put((-score, depth + 1, href, url))
                    except Exception as link_e:
                        self._log(
                            f"   [Worker {worker_id}] Ошибка сбора ссылок: {link_e}"
                        )

                try:
                    is_start_page = depth == 0

                    result = await analyze_page(page, url, is_start_page=is_start_page)

                    page_text = ""
                    try:
                        page_text = (await page.locator("body").inner_text())[
                            :2000
                        ].lower()
                    except:
                        pass

                    blocked = any(
                        x in page_text
                        for x in [
                            # "forbidden",
                            "access denied",
                            "if you are not a bot",
                            "captcha",
                            "ddos",
                        ]
                    )

                    if blocked:
                        self._log(
                            f"   [Worker {worker_id}] BLOCKED #{page_id} | Глубина: {depth} | Запах: {-priority} | {url}"
                        )
                        async with self.lock:
                            await self.queue.put((priority, depth, url, parent_url))
                            self.visited.remove(url)
                            self.pages_visited -= 1
                            self.needs_restart = True

                        self.stop_event.set()
                        break
                    else:
                        self._log(
                            f"   [Worker {worker_id}] OK #{page_id} | Score: {result['score']} | Type: {result['type']} | Глубина: {depth} | Запах: {-priority} | {url}"
                        )

                except Exception as eval_e:
                    if "Execution context was destroyed" in str(
                        eval_e
                    ) or "Target closed" in str(eval_e):
                        self._log(
                            f"   [Worker {worker_id}] Скрипт нажал кнопку-ссылку. Контекст сброшен, идем дальше."
                        )
                        result = {
                            "type": "ERROR",
                            "score": 0,
                            "reasons": ["Краш контекста (Кнопка-ссылка)"],
                            "detected_url": url,
                            "externalForms": [],
                        }
                    else:
                        result = {
                            "type": "ERROR",
                            "score": 0,
                            "reasons": [str(eval_e)],
                            "detected_url": url,
                            "externalForms": [],
                        }

                ext_forms = result.pop("externalForms", [])
                for ext in ext_forms:
                    ext_score, ext_reasons, ext_fields, ext_heading = (
                        await analyze_external_form(
                            context.request, ext["url"], ext["context"]
                        )
                    )
                    if ext_score > result["score"]:
                        result["type"] = "YANDEX_GOOGLE_FORM"
                        result["score"] = ext_score
                        result["detected_url"] = ext["url"]
                        result["reasons"] = ext_reasons
                        result["fields"] = ext_fields
                        result["heading"] = ext_heading
                        result["modal_trigger"] = ""

                async with self.lock:
                    self.findings.append(
                        {
                            "url": result["detected_url"],
                            "score": result["score"],
                            "type": result["type"],
                            "parent": parent_url,
                            "reasons": result["reasons"],
                            "fields": result.get("fields", []),
                            "heading": result.get("heading", ""),
                            "modal_trigger": result.get("modal_trigger", ""),
                        }
                    )

                    # посмотрим
                    # good_forms = [
                    #    f for f in self.findings if f["score"] >= 80 and f["parent"]
                    # ]
                    # parent_counts = {}
                    # for f in good_forms:
                    #    parent_counts[f["parent"]] = (
                    #        parent_counts.get(f["parent"], 0) + 1
                    #    )
                    #    if parent_counts[f["parent"]] >= 2:
                    #        self._log(
                    #            f"\n[Worker {worker_id}] ДОСТАТОЧНО ДАННЫХ! Каталог подтвержден: {f['parent']}"
                    #        )
                    #        self.stop_event.set()
                    #        break

            except Exception as e:
                self._log(
                    f"   [Worker {worker_id}] ERROR #{page_id} | {url} тип:{type(e).__name__}: {e}"
                )
                pass
            finally:
                self.queue.task_done()

        await page.close()

    async def run(self, browser, use_llm=True):
        self._log(f"\n{'='*60}\запуск кроулера: {self.start_url}\n{'='*60}")

        await self.queue.put((-100, 0, self.start_url, None))

        max_restarts = 3
        restarts = 0
        current_workers_count = MAX_CONCURRENT_TABS

        while not self.queue.empty() and self.pages_visited < MAX_PAGES_TO_VISIT:
            self.needs_restart = False
            self.stop_event.clear()

            # user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            # ignore_https_errors=True,
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            workers = [
                asyncio.create_task(self._worker(context, i + 1))
                for i in range(current_workers_count)
            ]

            queue_task = asyncio.create_task(self.queue.join())
            stop_task = asyncio.create_task(self.stop_event.wait())

            done, pending = await asyncio.wait(
                [queue_task, stop_task], return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()

            self.stop_event.set()
            await asyncio.gather(*workers, return_exceptions=True)
            await context.close()

            if self.needs_restart:
                restarts += 1
                if restarts > max_restarts:
                    self._log("Слишком много блокировок (3/3). Сдаемся.")
                    break
                current_workers_count = 2
                sleep_time = 15 * max(restarts, 2)
                self._log(
                    f"\nБлокировка! Ждем {sleep_time} сек и перезапускаем чистую сессию (Попытка {restarts}/{max_restarts})..."
                )
                await asyncio.sleep(sleep_time)
            else:
                break  # Если блока не было (очередь пуста или лимит) — всё ок, выходим

        if use_llm:
            return await self.analyze_findings_llm()
        else:
            return self.analyze_findings()

    def analyze_findings(self):
        if not self.findings:
            return {"catalog": None, "top_forms": []}

        sorted_findings = sorted(self.findings, key=lambda x: x["score"], reverse=True)
        top_8 = sorted_findings[:8]

        def normalize_url(u):
            if not u:
                return ""
            u = u.split("#")[0]  # .rstrip("/")
            u = u.replace("http://", "https://").replace("https://www.", "https://")
            return u

        parsed_start = urlparse(self.start_url)
        root_domain = normalize_url(f"{parsed_start.scheme}://{parsed_start.netloc}")

        if not top_8:
            return {"catalog": None, "top_forms": []}

        max_score = top_8[0]["score"]

        parent_counts = {}
        for f in top_8:
            if f["score"] >= 50 and f["score"] >= (max_score - 20):
                parents = self.parents_map.get(f["url"], set())
                child_norm = normalize_url(f["url"])

                for p in parents:
                    parent_norm = normalize_url(p)

                    if parent_norm == child_norm:
                        continue

                    if parent_norm == root_domain:
                        continue

                    if parent_norm not in parent_counts:
                        parent_counts[parent_norm] = set()

                    parent_counts[parent_norm].add(child_norm)

                    # parent_counts[p] = parent_counts.get(p, 0) + 1

        self._log("\n" + "=" * 60)
        self._log("итог скана, топ-форм:")
        self._log("=" * 60)

        for i, f in enumerate(top_8, 1):
            url_norm = normalize_url(f["url"])
            if f["score"] <= 0:
                self._log(f"{i}. [0 баллов] {f['url']} (Мусор)")
            else:
                parents_for_form = set()
                for finding in self.findings:
                    if normalize_url(finding["url"]) == url_norm:
                        parents_for_form.add(
                            finding["parent"] if finding["parent"] else self.start_url
                        )

                valid_parents = [
                    p
                    for p in parents_for_form
                    if normalize_url(p) != url_norm and normalize_url(p) != root_domain
                ]

                self._log(f"{i}. [{f['score']} баллов] {f['url']} ({f['type']})")
                self._log(f"   Найдена на {len(valid_parents)} 'валидных' страницах:")
                for p in sorted(list(set(valid_parents)))[:5]:
                    self._log(f"      - {p}")
        self._log("-" * 60)

        catalog_url = None
        best_catalog_score = -100

        for parent, children in parent_counts.items():
            if len(children) >= 2:
                parent_scent = get_scent_score(parent, "")
                ranking_score = (
                    (len(children) * 10) + parent_scent - (len(parent) * 0.1)
                )

                if ranking_score > best_catalog_score:
                    best_catalog_score = ranking_score
                    catalog_url = parent

        if catalog_url:
            self._log(f"🟢 каталог вакансий найден")
            self._log(f"   Ссылка на каталог: {catalog_url}")
        else:
            self._log(f"🔴 каталог не найден")
            if top_8 and top_8[0]["score"] >= 80:
                self._log(f"   Но найдена отличная одиночная форма: {top_8[0]['url']}")

        self._log("=" * 60 + "\n")
        return {"catalog": catalog_url, "top_forms": top_8}

    async def analyze_findings_llm(self):
        if not self.findings:
            self._log("На сайте вообще не найдено форм с полями.")
            return {
                "decision": "NO_HR_FORMS",
                "target_url": None,
                "modal_trigger": None,
                "reasoning": "Ничего не найдено",
            }

        sorted_findings = sorted(self.findings, key=lambda x: x["score"], reverse=True)
        top_8 = sorted_findings[:5]

        def normalize_url(u):
            if not u:
                return ""
            u = u.split("#")[0]
            u = u.replace("http://", "https://").replace("https://www.", "https://")
            return u

        parsed_start = urlparse(self.start_url)
        root_domain = normalize_url(f"{parsed_start.scheme}://{parsed_start.netloc}")

        forms_for_llm = []
        for f in top_8:
            if f["score"] < 50:
                continue

            url_norm = normalize_url(f["url"])
            parents_for_form = set()
            for finding in self.findings:
                if normalize_url(finding["url"]) == url_norm:
                    parents_for_form.add(
                        finding["parent"] if finding["parent"] else self.start_url
                    )

            valid_parents = [
                p
                for p in parents_for_form
                if normalize_url(p) != url_norm and normalize_url(p) != root_domain
            ]

            forms_for_llm.append(
                {
                    "url": f["url"],
                    "type": f["type"],
                    "heading": f.get("heading", ""),
                    "fields": f.get("fields", []),
                    "modal_trigger": f.get("modal_trigger", ""),
                    "score": f["score"],
                    "found_on_parents": valid_parents,
                }
            )

        if not forms_for_llm:
            return {
                "decision": "NO_HR_FORMS",
                "target_url": None,
                "modal_trigger": None,
                "reasoning": "Формы не прошли фильтр баллов",
            }

        self._log("\n" + "=" * 60)
        self._log("данные переданы в роутер:")
        self._log(json.dumps(forms_for_llm, ensure_ascii=False, indent=2))
        self._log("=" * 60)

        decision = await route_site(forms_for_llm)
        return decision

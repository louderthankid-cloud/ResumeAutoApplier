import json
import asyncio
from playwright.async_api import Page

from services.llm_client import submit_prompt
from core.config import settings
from services.page_analyzer import analyze_page
from schemas.application import FormFillReport, FormFillStatus


class FormFillerOrchestrator:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def _log(self, message: str):
        if self.verbose:
            print(message)

    def _clean_json(self, text: str) -> str:
        if not isinstance(text, str):
            text = str(text)
        text = text.replace("```json", "").replace("```", "").strip()

        import re

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        return text

    async def extract_dom_state(self, page: Page) -> tuple[list, str]:
        self._log("[FormFiller] скан страницы")

        js_code = r"""
        () => {
            let elements = [];
            let errors = [];
            let counter = 0;

            const hrTriggerRegex = /отклик|присоедин|заполн|отправ|резюме|apply|join|cv/i;
            
            const isVisible = (el) => {
                if (['file', 'checkbox', 'radio'].includes(el.type)) return true; // Эти поля часто скрыты физически
                const style = window.getComputedStyle(el);
                const rects = el.getClientRects();
                return style.display !== 'none' && 
                       style.visibility !== 'hidden' && 
                       style.opacity !== '0' && 
                       (el.offsetWidth > 0 || el.offsetHeight > 0 || rects.length > 0);
            };
            
            const isJunk = (el) => {
                if (el.type === 'checkbox' || el.type === 'radio') {
                    if (el.closest('form')) return false;
                    if (el.closest('header, nav, [class*="header" i], [class*="lang" i], [class*="burger" i]')) return true;
                    return false;
                }

                if (el.closest('header, footer, nav')) return true;

                const formParent = el.closest('form, [class*="form" i], [class*="modal" i], [class*="popup" i]');
                if (formParent) return false; // внутри формы — не мусор никогда

                const junkSelector = '[class*="header" i], [class*="footer" i], [class*="nav" i], [class*="menu" i]';
                return !!el.closest(junkSelector);

            };

            
            const getBotId = (el) => {
                let id = el.getAttribute('data-bot-id');
                if (!id) {
                    id = 'bot_' + (++counter) + '_' + Math.random().toString(36).substr(2, 5);
                    el.setAttribute('data-bot-id', id);
                }
                return id;
            };

            const getContext = (el) => {
                let label = el.getAttribute('aria-label') || el.placeholder || '';
                if (el.labels && el.labels.length > 0) {
                    label += ' ' + Array.from(el.labels).map(l => l.innerText).join(' ');
                }
                let parent = el.parentElement;
                if (parent) {
                    label += ' | Контекст: ' + (parent.innerText || '').substring(0, 80);
                }
                return label.replace(/\s+/g, ' ').trim().substring(0, 150);
            };

            // 1. Поля ввода
            document.querySelectorAll('input:not([type="hidden"]), textarea, select').forEach(el => {
                if (!isVisible(el) && el.tagName !== 'SELECT') return;
                
                if (isJunk(el)) return;
                
                const isPhoneField = (() => {
                    const ctx = (el.getAttribute('aria-label') || el.placeholder || el.name || '').toLowerCase();
                    const inputType = (el.type || '').toLowerCase();
                    return inputType === 'tel' || /phone|телефон|тел\b|mobile|моб/.test(ctx);
                })();
                
                let info = {
                    id: getBotId(el),
                    tag: el.tagName.toLowerCase(),
                    type: el.type,
                    context: getContext(el),
                    current_value: el.value || '',
                    is_checked: el.checked || false,
                    is_phone: isPhoneField,
                    phone_prefix: isPhoneField && el.value ? el.value.trim() : ''
                };
                
                if (el.tagName.toLowerCase() === 'select') {
                    info.options = Array.from(el.querySelectorAll('option')).map(o => ({
                        value: o.value, text: o.innerText.trim()
                    }));
                }
                elements.push(info);
                
                const alreadyUploaded = el.getAttribute('data-bot-uploaded') === 'true';
                if (alreadyUploaded) info.current_value = '[файл загружен]';
                
            });

            // 2. Интерактивные div/span (кастомные чекбоксы, селекты, пункты меню)
            let customNodes = Array.from(document.querySelectorAll('[role="button"], [role="combobox"], [role="listbox"], [role="option"], [class*="checkbox" i], [class*="select" i], [class*="radio" i]'));
            
            // Убираем вложенные элементы (чтобы не отдавать LLM и обертку, и иконку внутри нее отдельно)
            customNodes = customNodes.filter(el => {
                if (!isVisible(el)) return false;
                if (el.tagName === 'INPUT' || el.tagName === 'SELECT') return false; 
                if (isJunk(el)) return false;
                
                // Если родитель тоже есть в списке, пропускаем дочерний (оставляем только контейнер)
                let parent = el.parentElement;
                while(parent && parent !== document.body) {
                    if (customNodes.includes(parent)) return false;
                    parent = parent.parentElement;
                }
                return true;
            });

            customNodes.forEach(el => {
                // Если у самого чекбокса нет текста, ищем вокруг
                let ctx = (el.innerText || '').replace(/\s+/g, ' ').trim().substring(0, 100);
                if (!ctx) ctx = getContext(el); 

                let cls = (typeof el.className === 'string' ? el.className : '').toLowerCase();
                let isChecked = el.getAttribute('aria-checked') === 'true' || 
                                cls.includes('state-true') || 
                                (cls.includes('checked') && !cls.includes('state-false')) ||
                                cls.includes('selected');
                const marker = el.querySelector('[class*="CheckboxMarker"], [class*="checkboxmarker"]');
                if (marker) {
                    const mCls = (typeof marker.className === 'string' ? marker.className : '').toLowerCase();
                    isChecked = isChecked || 
                                mCls.includes('state-true') ||
                                (mCls.includes('checked') && !mCls.includes('state-false'));
                }

                elements.push({
                    id: getBotId(el),
                    tag: el.tagName.toLowerCase(),
                    type: 'custom_interactive',
                    context: ctx,
                    // ТЕПЕРЬ LLM УВИДИТ СОСТОЯНИЕ КАСТОМНОГО ЧЕКБОКСА И СЕЛЕКТА:
                    is_checked: isChecked,
                    current_value: ctx 
                });

            });

            // 3. Кнопки (сабмиты, добавление файлов)
            document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"], [class*="btn" i], [class*="button" i], [data-qa*="submit" i], [data-qa*="send" i], [data-qa*="post_submit" i]').forEach(el => {
                if (!isVisible(el)) return;
                
                let text = (el.innerText || el.textContent || el.value || '').replace(/\s+/g, ' ').trim().substring(0, 100);
                
                if (el.tagName === 'A' && !(el.className && typeof el.className === 'string' && el.className.match(/btn|button/i)) && !el.getAttribute('role')) {
                    const isModalTrigger = el.hasAttribute('data-toggle') || el.hasAttribute('data-bs-toggle') || el.hasAttribute('data-target') || el.hasAttribute('data-bs-target');
                    if (!hrTriggerRegex.test(text) && !isModalTrigger) {
                        return; // Пропускаем обычные ссылки
                    }
                }

                if (isJunk(el)) return; 
                
                elements.push({
                    id: getBotId(el),
                    tag: el.tagName.toLowerCase(),
                    type: 'button',
                    context: text || 'Кнопка / Триггер'
                });
            });

            // 4. Каптчи (только видимые). LLM сама решит, мешает ли каптча форме
            const captchaSelector = [
                'iframe[src*="recaptcha" i]', 'iframe[src*="hcaptcha" i]',
                'iframe[src*="captcha" i]', 'iframe[src*="turnstile" i]',
                'iframe[src*="smartcaptcha" i]',
                '.g-recaptcha', '.h-captcha', '.cf-turnstile',
                '[class*="smart-captcha" i]', '[data-testid*="captcha" i]',
                '[class*="captcha" i]', '[id*="captcha" i]'
            ].join(', ');
            const seenCaptchaContainers = new Set();
            document.querySelectorAll(captchaSelector).forEach(el => {
                if (!isVisible(el)) return;
                // не плодим дубли (iframe внутри .g-recaptcha и т.п.)
                const container = el.closest('form, [class*="form" i], [class*="modal" i], [class*="popup" i]') || el;
                if (seenCaptchaContainers.has(container)) return;
                seenCaptchaContainers.add(container);

                const inForm = !!el.closest('form, [class*="form" i], [class*="modal" i], [class*="popup" i]');
                const sig = (
                    (typeof el.className === 'string' ? el.className : '') + ' ' +
                    (el.getAttribute('src') || '') + ' ' +
                    (el.getAttribute('data-testid') || '') + ' ' +
                    (el.id || '')
                ).toLowerCase();
                const provider = /smartcaptcha|smart-captcha|checkboxcaptcha|recaptcha|hcaptcha|turnstile|yandexcloud|grecaptcha/.test(sig);
                elements.push({
                    id: getBotId(el),
                    tag: el.tagName.toLowerCase(),
                    type: 'captcha',
                    context: 'ВИДЖЕТ КАПТЧИ'
                        + (inForm ? ' (внутри формы отклика!)' : '')
                        + (provider ? ' [известный провайдер — пройти автоматически нельзя]' : ''),
                    in_form: inForm,
                    provider: provider
                });
            });

            document.querySelectorAll('.error, .invalid, [class*="error"], [style*="color: red"]').forEach(el => {
                if (el.offsetWidth > 0 && el.innerText.trim().length > 0) {
                    errors.push(el.innerText.trim());
                }
            });

            const allCb = Array.from(document.querySelectorAll('input[type="checkbox"], input[type="radio"]'));
            console.log('ALL CHECKBOXES ON PAGE:', allCb.length);
            allCb.forEach(cb => {
                const vis = isVisible(cb);
                const junk = isJunk(cb);
                console.log('CB:', cb.name, cb.className, 'visible:', vis, 'junk:', junk, 'closest:', cb.closest('[class*="header" i], [class*="footer" i], [class*="nav" i]'));
            });
            
            return { elements, errors: errors.join(' | ') };
        }
        """
        # page.on(
        #    "console",
        #    lambda msg: (
        #        print(f"   [BROWSER] {msg.text}")
        #        if "CHECKBOX" in msg.text or "CB:" in msg.text
        #        else None
        #    ),
        # )

        data = await page.evaluate(js_code)

        # all_checkboxes = [
        #    e for e in data["elements"] if e.get("type") in ["checkbox", "radio"]
        # ]
        # self._log(f"   [DEBUG] чекбоксов в raw data: {len(all_checkboxes)}")
        # for cb in all_checkboxes:
        #    self._log(
        #        f"   [DEBUG cb] id={cb['id']} context={repr(cb['context'])} is_checked={cb.get('is_checked')}"
        #    )

        filtered_elements = [
            e
            for e in data["elements"]
            if e["context"] or e["type"] in ["file", "submit", "checkbox", "radio"]
        ]

        return filtered_elements, data["errors"]

    async def _try_open_modal(self, page) -> bool:
        self._log("[FormFiller] pre-step: ищу кнопку-модалку")
        try:
            result = await analyze_page(page, page.url, is_start_page=False)
        except Exception as e:
            self._log(f"[FormFiller] page_analyzer упал: {e}")
            return False

        modal_trigger = result.get("modal_trigger", "")
        page_type = result.get("type", "")

        if page_type == "IFRAME_FORM":
            iframe_url = result.get("detected_url", "")
            if iframe_url and iframe_url.startswith("http"):
                self._log(
                    f"[FormFiller] IFRAME форма — перехожу напрямую: {iframe_url}"
                )
                await page.goto(iframe_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                return True
            self._log(f"[FormFiller] IFRAME без URL — пропускаю")
            return False

        if page_type != "MODAL_FORM" or not modal_trigger:
            self._log(f"[FormFiller] модалки нет (тип: {page_type})")
            return False

        self._log(f"[FormFiller] найдена модалка, триггер: '{modal_trigger}'")

        await page.wait_for_timeout(1500)

        has_inputs = await page.evaluate(r"""
            () => Array.from(document.querySelectorAll(
                'input:not([type="hidden"]):not([type="file"]), textarea'
            )).some(el => {
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden'
                       && (el.offsetWidth > 0 || el.offsetHeight > 0);
            })
        """)

        if has_inputs:
            self._log("[FormFiller] поля уже видны — модалка открыта")
            return True

        return await self._click_modal_trigger(page, modal_trigger)

    async def _click_modal_trigger(self, page, trigger_text: str) -> bool:
        clicked = await page.evaluate(
            r"""
            (triggerText) => {
                const regex = new RegExp(
                    triggerText.replace(/[.*+?^${}()|[\\\\]\\\\\\\\]/g, '\\\\\\\\$&'), 'i'
                );
                const candidates = Array.from(document.querySelectorAll(
                    'a, button, [role="button"], [class*="btn" i], [class*="button" i], span, div'
                ));
                for (let el of candidates) {
                    const text = (el.innerText || el.textContent || '')
                        .trim().replace(/\\\\s+/g, ' ');
                    const isVisible = el.offsetWidth > 0 || el.offsetHeight > 0
                                      || el.getClientRects().length > 0;
                    if (isVisible && regex.test(text) && text.length < 80) {
                        el.scrollIntoView({block: 'center'});
                        el.dispatchEvent(
                            new MouseEvent('click', {bubbles: true, cancelable: true})
                        );
                        return text;
                    }
                }
                return null;
            }
        """,
            trigger_text,
        )

        if not clicked:
            self._log(f"[FormFiller] кнопку '{trigger_text}' не нашли в DOM")
            return False

        self._log(f"[FormFiller] JS-клик по '{clicked}', жду...")
        await page.wait_for_timeout(2500)

        has_inputs = await page.evaluate(r"""
            () => Array.from(document.querySelectorAll(
                'input:not([type="hidden"]):not([type="file"]), textarea'
            )).some(el => {
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden'
                       && (el.offsetWidth > 0 || el.offsetHeight > 0);
            })
        """)

        if has_inputs:
            self._log("[FormFiller] успех! поля появились")
            return True
        self._log("[FormFiller] клик прошёл, но поля не появились")
        return False

    async def _verify_submission(self, page: Page) -> tuple[FormFillStatus, str]:
        """
        Смотрим, не вылезли ли ошибки валидации и не встала ли каптча.
        """
        await page.wait_for_timeout(3000)

        try:
            elements, errors = await self.extract_dom_state(page)
        except Exception as e:
            return (
                FormFillStatus.SUBMITTED,
                f"страница сменилась после сабмита ({str(e).splitlines()[0]})",
            )

        captcha_block = [
            e
            for e in elements
            if e.get("type") == "captcha" and (e.get("provider") or e.get("in_form"))
        ]
        if captcha_block:
            return (
                FormFillStatus.BLOCKED_CAPTCHA,
                "после сабмита появилась/осталась каптча, блокирующая отправку",
            )

        if errors:
            return (
                FormFillStatus.FILL_FAILED,
                f"после сабмита остались ошибки валидации: {errors[:300]}",
            )

        return FormFillStatus.SUBMITTED, "ошибок после сабмита не обнаружено"

    @staticmethod
    def _parse_agent_report(plan: list) -> tuple[list, dict | None]:
        """Вынимает из плана действие 'report' (самоотчёт агента), если оно есть."""
        agent_report = None
        rest = []
        for action_obj in plan:
            if action_obj.get("action") == "report":
                agent_report = action_obj
            else:
                rest.append(action_obj)
        return rest, agent_report

    def _make_report(
        self,
        status: FormFillStatus,
        detail: str,
        step: int,
        unfilled: list | None = None,
        fields_filled: int = 0,
        captcha_detected: bool = False,
    ) -> FormFillReport:
        report = FormFillReport(
            status=status,
            detail=detail,
            unfilled_fields=unfilled or [],
            steps_used=step,
            fields_filled=fields_filled,
            captcha_detected=captcha_detected,
        )
        self._log(f"[FormFiller] итог: {report.status.value} — {report.detail}")
        return report

    async def run_loop(
        self, page: Page, candidate_resume: str, resume_path: str, max_steps: int = 4
    ) -> FormFillReport:
        self._log(f"\n[FormFiller] заполнение формы, максимум шагов - {max_steps}")

        dry_run = False

        submit_done = False

        saw_form_fields = False  # видели ли вообще поля ввода за все шаги
        fields_filled = 0  # сколько реальных полей кандидата реально заполнили
        step = 0

        await self._try_open_modal(page)

        for step in range(1, max_steps + 1):
            self._log(f"\n--- шаг {step} ---")

            elements, errors = await self.extract_dom_state(page)
            self._log(f"   собрано элементов: {len(elements)}")
            if errors:
                self._log(f"   ошибки на странице: {errors[:100]}...")

            if any(
                e.get("tag") in ("input", "textarea", "select")
                and e.get("type") not in ("submit", "button")
                for e in elements
            ):
                saw_form_fields = True

            captcha_block = any(
                e.get("type") == "captcha" and (e.get("provider") or e.get("in_form"))
                for e in elements
            )
            if captcha_block:
                self._log("   [КАПТЧА] обнаружен блокирующий виджет — стоп")
                return self._make_report(
                    FormFillStatus.BLOCKED_CAPTCHA,
                    "на странице каптча, блокирующая отправку (пройти автоматически нельзя)",
                    step,
                    fields_filled=fields_filled,
                    captcha_detected=True,
                )

            JUNK_BUTTON_TEXTS = {
                "войти",
                "login",
                "sign in",
                "register",
                "регистрация",
                "попробовать бесплатно",
                "try free",
                "все вакансии",
                "продукт",
                "партнерство",
                "ресурсы",
                "pricing",
                "docs",
                "кнопка / триггер",
            }

            def is_useful(e):
                if e["tag"] in ("input", "textarea", "select"):
                    return True
                if e.get("type") == "button" or e["tag"] in ("button",):
                    ctx = e.get("context", "").lower().strip()
                    if ctx in JUNK_BUTTON_TEXTS:
                        return False
                    if not ctx or ctx == "кнопка / триггер":
                        return False
                    return True
                if e["tag"] == "a":
                    ctx = e.get("context", "").lower().strip()
                    EXTERNAL_JUNK = {
                        "t.me",
                        "telegram",
                        "vk.com",
                        "vk ",
                        " vk",
                        "youtube",
                        "instagram",
                        "facebook",
                        "linkedin",
                        "twitter",
                        "tiktok",
                        "кнопка / триггер",
                        "",
                    }
                    if any(j in ctx for j in EXTERNAL_JUNK):
                        return False
                    if not ctx:
                        return False
                    return True
                return True

            elements_for_llm = [e for e in elements if is_useful(e)]
            self._log(f"   после фильтра мусора: {len(elements_for_llm)}")

            context_vars = {
                "resume_text": candidate_resume,
                "resume_path": resume_path,
                "form_elements": json.dumps(
                    elements_for_llm, ensure_ascii=False, indent=2
                ),
                "page_errors": errors or "Нет ошибок.",
            }

            self._log("   ожидание плана от LLM")
            # for e in elements:
            #    self._log(
            #        f"   [EL] id={e['id']} tag={e['tag']} type={e.get('type')} ctx={repr(e['context'][:50])}"
            #    )

            # if e.get("is_phone") or e.get("type") == "tel":
            #    self._log(
            #        f"   [DEBUG phone] id={e['id']} current_value={repr(e['current_value'])} phone_prefix={repr(e.get('phone_prefix',''))} is_phone={e.get('is_phone')}"
            #    )

            response = None
            try:
                response = await submit_prompt(
                    template_name="form_filler",
                    context_vars=context_vars,
                    task_name="form_agent",
                    json_mode=True,
                )

                clean_text = self._clean_json(response)
                parsed = json.loads(clean_text)

                if isinstance(parsed, dict):
                    plan = parsed.get("actions") or next(iter(parsed.values()), [])
                elif isinstance(parsed, list):
                    plan = parsed
                else:
                    plan = []

            except Exception as e:
                self._log(f"   ошибка LLM: {e}")
                self._log(f"   сырой ответ модели: {response}")
                return self._make_report(
                    FormFillStatus.FILL_FAILED, f"ошибка LLM: {e}", step
                )

            plan, agent_report = self._parse_agent_report(plan)
            if agent_report:
                raw_status = str(agent_report.get("status", "fill_failed"))
                try:
                    status = FormFillStatus(raw_status)
                except ValueError:
                    status = FormFillStatus.FILL_FAILED
                return self._make_report(
                    status,
                    agent_report.get("detail", "")
                    or f"агент вернул report: {raw_status}",
                    step,
                    agent_report.get("unfilled_fields") or [],
                    fields_filled=fields_filled,
                    captcha_detected=(status == FormFillStatus.BLOCKED_CAPTCHA),
                )

            if not plan:
                self._log("   LLM вернула пустой план (или всё заполнено). конец.")
                break

            self._log(f"   получено действий: {len(plan)}")

            current_values = {e["id"]: e.get("current_value", "") for e in elements}
            uploaded_ids = {
                e["id"] for e in elements if e.get("current_value") == "[файл загружен]"
            }

            filtered_plan = []
            for action_obj in plan:
                bid = action_obj.get("id", "")
                atype = action_obj.get("action")
                cv = current_values.get(bid, "")

                if atype == "fill":
                    real_content = (
                        cv.replace("+", "")
                        .replace("7", "")
                        .replace("8", "")
                        .replace("(", "")
                        .replace(")", "")
                        .replace("-", "")
                        .replace(" ", "")
                        .replace("_", "")
                    )
                    if cv and real_content:
                        self._log(
                            f"   [SKIP fill] [{bid}] уже заполнено: {repr(cv[:25])}"
                        )
                        continue
                elif atype == "upload_file":
                    if bid in uploaded_ids:
                        self._log(f"   [SKIP upload] [{bid}] файл уже загружен")
                        continue

                filtered_plan.append(action_obj)

            plan = filtered_plan
            if not plan:
                self._log("   все поля уже заполнены, выход.")
                break

            has_text_inputs = any(
                e.get("tag") in ["input", "textarea", "select"]
                and e.get("type") not in ["checkbox", "radio", "submit", "button"]
                for e in elements
            )

            for action_obj in plan:
                if action_obj.get("action") == "submit" and not has_text_inputs:
                    self._log(
                        f"   [ЗАЩИТА] LLM выбрал 'submit', но полей нет. Меняю на 'click' для открытия модалки"
                    )
                    action_obj["action"] = "click"

            actions_executed = 0
            for action_obj in plan:
                bot_id = action_obj.get("id")
                action_type = action_obj.get("action")
                val = action_obj.get("value", "")

                selector = f'[data-bot-id="{bot_id}"]'

                try:
                    locator = page.locator(selector).first
                    if not await locator.count():
                        continue

                    try:
                        if await locator.is_visible():
                            await locator.scroll_into_view_if_needed(timeout=1000)
                    except:
                        pass

                    if action_type == "fill":
                        self._log(f"   Ввод '{val[:20]}...' в [{bot_id}]")
                        try:
                            current_val = await locator.evaluate("el => el.value")
                            clean_val = str(val)

                            is_phone = await locator.evaluate("""(el) => {
                                const ctx = (el.getAttribute('aria-label') || el.placeholder || el.name || '').toLowerCase();
                                return el.type === 'tel' || /phone|телефон|тел\\b|mobile|моб/.test(ctx);
                            }""")

                            await locator.click(force=True, timeout=1000)
                            await page.wait_for_timeout(100)

                            if is_phone:
                                await locator.press("Control+a")
                                await locator.press("Delete")
                                await page.wait_for_timeout(150)

                                after_clear = await locator.evaluate("el => el.value")

                                digits = "".join(c for c in clean_val if c.isdigit())
                                if len(digits) == 10:
                                    digits = "7" + digits
                                mask_digits = "".join(
                                    c for c in after_clear if c.isdigit()
                                )

                                if mask_digits:
                                    tail = digits[len(mask_digits) :]
                                    await locator.press_sequentially(tail, delay=80)
                                else:
                                    await locator.press_sequentially(digits, delay=80)
                            else:
                                await locator.press("Control+a")
                                await locator.press("Delete")
                                await page.wait_for_timeout(100)
                                await locator.fill("")
                                await locator.press_sequentially(clean_val, delay=60)

                        except Exception:
                            await locator.evaluate(
                                "(el, v) => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles: true})); el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }",
                                str(val),
                            )
                        actions_executed += 1
                        fields_filled += 1  # реально заполнили поле кандидата

                    elif action_type == "click":
                        self._log(f"   Клик по [{bot_id}]")

                        has_native_input = await locator.evaluate("""(el) => {
                            if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) return true;
                            if (el.querySelector('input[type="checkbox"], input[type="radio"]')) return true;
                            return false;
                        }""")

                        if has_native_input:
                            await locator.evaluate("""(el) => {
                                let inp = (el.tagName === 'INPUT') ? el : el.querySelector('input[type="checkbox"], input[type="radio"]');
                                if (inp && !inp.checked) {
                                    let label = inp.id ? document.querySelector(`label[for="${inp.id}"]`) : null;
                                    if (!label) label = inp.closest('label');
                                    
                                    if (label) {
                                        label.click();
                                    } else {
                                        inp.click();
                                        inp.checked = true;
                                    }
                                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                            }""")
                        else:
                            await locator.evaluate("""(el) => {
                                const target =
                                    el.querySelector('[class*="Marker"], [class*="marker"]') ||
                                    el.querySelector('[tabindex]') ||
                                    el.querySelector('svg') ||
                                    el;
                                target.dispatchEvent(
                                    new MouseEvent('click', {bubbles: true, cancelable: true})
                                );
                                target.dispatchEvent(
                                    new MouseEvent('mousedown', {bubbles: true, cancelable: true})
                                );
                                target.dispatchEvent(
                                    new MouseEvent('mouseup', {bubbles: true, cancelable: true})
                                );
                            }""")

                        actions_executed += 1

                    elif action_type == "select":
                        self._log(f"   Селект '{val}' в [{bot_id}]")
                        try:
                            await locator.select_option(
                                str(val), timeout=1000, force=True
                            )
                        except Exception:
                            pass

                        await locator.evaluate(
                            """(el, targetVal) => {
                            let options = Array.from(el.options || []);
                            let opt = options.find(o => o.value === targetVal || (o.innerText || '').trim() === targetVal);
                            
                            if (opt) el.value = opt.value;
                            else el.value = targetVal;
                            
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            
                            const jq = window.jQuery || window.$;
                            if (typeof jq === 'function') {
                                jq(el).trigger('change');
                                jq(el).trigger('chosen:updated');
                                jq(el).trigger('change.select2');
                            }
                            
                            let chosenContainer = el.nextElementSibling;
                            if (chosenContainer && chosenContainer.classList.contains('chosen-container')) {
                                let single = chosenContainer.querySelector('.chosen-single');
                                if (single) {
                                    single.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                                    let results = Array.from(chosenContainer.querySelectorAll('.active-result'));
                                    let expectedText = opt ? opt.innerText.trim() : targetVal;
                                    let match = results.find(r => r.innerText.trim() === expectedText);
                                    if (match) {
                                        match.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                                    }
                                }
                            }
                        }""",
                            str(val),
                        )
                        actions_executed += 1

                    elif action_type == "upload_file":
                        self._log(f"   Загрузка файла в [{bot_id}]")
                        tag_name = await locator.evaluate(
                            "el => el.tagName.toLowerCase()"
                        )
                        el_type = await locator.evaluate("el => el.type")

                        if tag_name == "input" and el_type == "file":
                            await locator.set_input_files(resume_path)
                        else:
                            async with page.expect_file_chooser(
                                timeout=2000
                            ) as fc_info:
                                try:
                                    await locator.click(force=True, timeout=2000)
                                except:
                                    await locator.evaluate("el => el.click()")
                            file_chooser = await fc_info.value
                            await file_chooser.set_files(resume_path)
                        actions_executed += 1
                        fields_filled += (
                            1  # загрузили резюме — реальное действие по форме
                        )
                        await locator.evaluate(
                            "el => el.setAttribute('data-bot-uploaded', 'true')"
                        )

                    elif action_type == "submit":
                        self._log(f"   финальная кнопка отправки [{bot_id}]")
                        if settings.DRY_RUN:
                            self._log(f"   [DRY_RUN] отменён клик в дебаг режиме")
                            dry_run = True
                        else:
                            try:
                                await locator.click(force=True, timeout=2000)
                            except Exception:
                                await locator.evaluate("el => el.click()")
                            self._log("   сабмитнули")
                        submit_done = True
                        actions_executed += 1

                    await page.wait_for_timeout(300)

                except Exception as e:
                    self._log(
                        f"   провал действия {action_type} на {bot_id}: {str(e).splitlines()[0]}"
                    )

            if dry_run:
                if fields_filled == 0:
                    self._log(
                        "   [DRY_RUN] сабмит без единого заполненного поля — формы тут нет"
                    )
                    return self._make_report(
                        FormFillStatus.FORM_NOT_FOUND,
                        "дошли до сабмита, не заполнив ни одного поля — формы отклика на странице нет",
                        step,
                        fields_filled=0,
                    )
                self._log(
                    f"   [DRY_RUN] сабмит отменён, заполнено полей: {fields_filled}"
                )
                return self._make_report(
                    FormFillStatus.FILLED_DRY_RUN,
                    f"форма заполнена ({fields_filled} полей), сабмит отменён (DRY_RUN)",
                    step,
                    fields_filled=fields_filled,
                )

            if submit_done:
                self._log("   сабмит выполнен, проверяю результат...")
                status, detail = await self._verify_submission(page)
                return self._make_report(
                    status,
                    detail,
                    step,
                    fields_filled=fields_filled,
                    captcha_detected=(status == FormFillStatus.BLOCKED_CAPTCHA),
                )

            if actions_executed == 0:
                self._log("   ни одно действие не выполнено, выход из цикла")
                break

            self._log("   обновление DOM")
            await page.wait_for_timeout(3000)

        self._log("[FormFiller] филлер закончил работу без сабмита")

        if not saw_form_fields:
            return self._make_report(
                FormFillStatus.FORM_NOT_FOUND,
                "за все шаги не найдено ни одного поля ввода",
                step,
                fields_filled=fields_filled,
            )

        if step >= max_steps:
            return self._make_report(
                FormFillStatus.FILL_FAILED,
                f"исчерпан лимит шагов ({max_steps}), сабмит не выполнен",
                step,
                fields_filled=fields_filled,
            )

        return self._make_report(
            FormFillStatus.FILL_FAILED,
            "агент остановился, не дойдя до сабмита",
            step,
            fields_filled=fields_filled,
        )

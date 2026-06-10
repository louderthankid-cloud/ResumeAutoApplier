import asyncio
import re
from playwright.async_api import async_playwright


async def analyze_external_form(api_context, url, context_text):
    try:
        resp = await api_context.get(url, timeout=10000)
        html = await resp.text()
        final_url = resp.url

        score = 20
        reasons = ["Базовый бонус +20: Внешняя форма доступна и скачана"]

        hr_regex = re.compile(
            r"резюме|отклик|cv|career|join|apply|анкет|соискател|ваканси|resume|опыт|присоедин|команд|заполн|собеседован|должност",
            re.IGNORECASE,
        )
        junk_regex = re.compile(
            r"заявк|проект|бриф|услуг|расчет|консультац|купить|заказать|поиск|пароль|подписк|newsletter|корзин|товар|доставк|оплат|каталог",
            re.IGNORECASE,
        )

        form_text = ""

        if (
            "docs.google.com" in final_url
            or "docs.google.com" in url
            or "forms.gle" in url
        ):
            match = re.search(r"var FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);", html, re.DOTALL)
            if match:
                data_str = match.group(1)
                # Вытаскиваем все строковые значения
                strings = re.findall(r'"([^"\\]+)"', data_str)
                valid_strings = [
                    s
                    for s in strings
                    if len(s) > 2
                    and not s.startswith("rgba(")
                    and not s.startswith("#")
                ]
                form_text = " ".join(valid_strings)
                reasons.append(
                    "Инфо: Внутренняя структура Google Формы успешно распарсена"
                )
            else:
                form_text = html
        else:
            form_text = html

        combined_text = (context_text + " " + form_text).lower()

        if context_text and junk_regex.search(context_text.lower()):
            score -= 30
            reasons.append(
                "Штраф -30: Мусорные слова в контексте страницы (рядом с формой)"
            )

        if hr_regex.search(combined_text):
            score += 40
            reasons.append(
                "Бонус +40: HR-слова (отклик, собеседование, должность...) найдены"
            )

        field_keywords = [
            "имя",
            "телефон",
            "email",
            "почта",
            "опыт",
            "навык",
            "образование",
            "ссылка",
            "резюме",
            "возраст",
            "город",
            "место работы",
            "фио",
            "должность",
            "портфолио",
        ]
        fields_found = sum(1 for f in field_keywords if f in form_text.lower())

        if fields_found > 0:
            pts = min(fields_found * 10, 50)
            score += pts
            reasons.append(
                f"Бонус +{pts}: Распознано {fields_found} смысловых полей (имя, телефон и др.)"
            )

        if (
            "резюме" in form_text.lower()
            or "файл" in form_text.lower()
            or "cv" in form_text.lower()
        ):
            score += 50
            reasons.append("Бонус +50: Явное упоминание загрузки резюме/файла")

        fields_found_list = [f for f in field_keywords if f in form_text.lower()]
        heading = context_text[:100] if context_text else "Внешняя форма"

        return score, reasons, fields_found_list, heading

    except Exception as e:
        return 0, [f"Ошибка скачивания внешней формы: {e}"], [], ""


async def analyze_page(page, url, is_start_page=False):
    js_code = r"""
    async ([isStartPage]) => {
        const hrRegex = /резюме|отклик|cv|career|join|apply|анкет|соискател|ваканси|resume|опыт|присоедин|команд|заполн|собеседован|должност/i;
        const junkRegex = /заявк|проект|бриф|услуг|расчет|консультац|купить|заказать|поиск|пароль|подписк|newsletter|корзин|товар|доставк|оплат|каталог/i;
        
        let bestResult = { type: 'NOT_FOUND', score: 0, reasons: [], detected_url: window.location.href };
        let externalForms = []; // Сюда собираем ссылки на яндекс/гугл формы

        const updateScore = (type, score, reasons, targetUrl, fields = [], heading = '', triggerText = '') => {
            if (score > bestResult.score) {
                bestResult = { 
                    type, 
                    score, 
                    reasons, 
                    detected_url: targetUrl,
                    fields: fields,
                    heading: heading,
                    modal_trigger: triggerText
                };
            }
        };

        const scoreFormContainer = (container, isModal = false, triggerText = '') => {
            let score = 0;
            let reasons = [];
            
            if (container.closest('nav, footer, [class*="nav" i], [class*="footer" i]')) {     //'header, footer, nav')) {
                if (!isStartPage) {
                    return { score: 0, reasons: [] };
                } else {
                    score -= 10;
                    reasons.push("Штраф -10: Форма в хедере или футере (на стартовой)");
                }
            }
            
            const text = (container.innerText || '').toLowerCase();
            
            const isHrTrigger = /отклик|присоедин|заполн|отправ|резюме|apply|join/i.test(triggerText);
            
            if (isModal && isHrTrigger) { // нахуя?
                reasons.push(`Инфо: Форма вызвана целевой кнопкой "${triggerText}" (штрафы за мусор отключены)`);
            } else {
                if (junkRegex.test(text)) {
                    score -= 50;
                    reasons.push("Штраф -50: Найдены мусорные/коммерческие слова");
                }
            }
            
            if (hrRegex.test(text)) {
                score += 30;
                reasons.push("Бонус +30: HR-слова в тексте формы");
            }
            
            const getValidInputs = (selector, checkVisibility = true) => {
                return Array.from(container.querySelectorAll(selector)).filter(el => {
                    const isVisible = checkVisibility ? (el.offsetWidth > 0 || el.offsetHeight > 0) : true;
                    return isVisible && (isStartPage || !el.closest('nav, footer, [class*="nav" i], [class*="footer" i]'));
                });
            };
            
            const textInputs = getValidInputs('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea');

            if (textInputs.length > 0) {
                let pts = Math.min(textInputs.length * 10, 50); 
                score += pts;
                reasons.push(`Бонус +${pts}: Текстовые поля (${textInputs.length} шт. по 10 баллов)`);
            }
            
            const checkboxes = getValidInputs('input[type="checkbox"], input[type="radio"]');
            if (checkboxes.length > 0) {
                score += 20;
                reasons.push(`Бонус +20: Чекбоксы/Радио (${checkboxes.length} шт.)`);
            }
            
            const fileInputs = Array.from(container.querySelectorAll('input[type="file"]'))
                                    .filter(el => !el.closest('footer, nav'));

            const fileRegex = /прикрепить|добавить (файл|резюме)|выберите (файл|резюме)|загрузить (файл|резюме)|add file|upload file|attach (file|resume)/i;
            const customFileInputs = Array.from(container.querySelectorAll('span, div, label, a, button')).filter(el => {
                const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
                return (el.offsetWidth > 0 || el.offsetHeight > 0) 
                       && text.length > 0 
                       && text.length < 50 
                       && fileRegex.test(text);
            });

            if (fileInputs.length > 0 || customFileInputs.length > 0) {
                score += 50;
                reasons.push("Бонус +50: Поле загрузки файла (резюме)");
            }
            
            const submits = getValidInputs('button[type="submit"], input[type="submit"], [class*="submit" i], [data-qa*="submit" i]');
            
            const formButtons = container.tagName === 'FORM' ? container.querySelectorAll('button') : [];
            
            const submitRegex = /отправить|откликнуться|отправить резюме|submit|apply/i;
            const textSubmits = Array.from(container.querySelectorAll('span, div, a')).filter(el => {
                const innerText = (el.innerText || '').trim();
                return (el.offsetWidth > 0 || el.offsetHeight > 0) 
                       && innerText.length > 0 
                       && innerText.length < 30 
                       && submitRegex.test(innerText);
            });
            
            if (submits.length > 0 || formButtons.length > 0 || textSubmits.length > 0) {
                score += 40;
                reasons.push("Бонус +40: Кнопка отправки формы");
            }
            
            const fields = Array.from(container.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select')).map(el => {
                let label = (el.placeholder || '').replace(/\uFEFF/g, '').trim();
                
                if (!label && el.labels && el.labels.length > 0) {
                    let text = el.labels[0].innerText || el.labels[0].textContent || '';
                    label = text.replace(/\uFEFF/g, '').trim();
                }
                
                if (!label && el.hasAttribute('aria-label')) {
                    let aria = el.getAttribute('aria-label') || '';
                    label = aria.replace(/\uFEFF/g, '').trim();
                }
                
                if (!label) {
                    let parent = el.parentElement;
                    let depth = 0;
                    while (parent && depth < 5) {
                        const possibleLabels = parent.querySelectorAll('label, [class*="label" i], [class*="title" i], [class*="placeholder" i], [class*="caption" i]');
                        
                        for (let pl of possibleLabels) {
                            if (pl !== el) {
                                let text = pl.innerText || pl.textContent || '';
                                text = text.replace(/\uFEFF/g, '').trim();
                                if (text) {
                                    label = text;
                                    break;
                                }
                            }
                        }
                        if (label) break;
                        parent = parent.parentElement;
                        depth++;
                    }
                }

                if (!label && el.name) {
                    const nameStr = el.name.toLowerCase();
                    if (!nameStr.startsWith('ws-') && !/\d{5,}/.test(nameStr) && !nameStr.includes('token') && !nameStr.includes('hash')) {
                        label = el.name;
                    }
                }

                
                if (!label) {
                    label = "Безымянное поле (" + (el.type || el.tagName.toLowerCase()) + ")";
                }
                
                return label.replace(/\s+/g, ' ').trim().slice(0, 60);
            }).filter(t => t.length > 0);

            let heading = "";
            let curr = container;
            for (let depth = 0; depth < 3 && curr && curr !== document.body; depth++) {
                let prev = curr.previousElementSibling;
                while(prev) {
                    if(prev.tagName && prev.tagName.match(/^H[1-4]$/)) {
                        heading = prev.innerText.trim();
                        break;
                    }
                    prev = prev.previousElementSibling;
                }
                if (heading) break;
                curr = curr.parentElement;
            }
            if (!heading) heading = document.title;
            
            return { score, reasons, fields, heading };
        };

        const scanDom = (isModal = false, triggerText = '') => {
            const forms = Array.from(document.querySelectorAll('form, [role="form"]')).filter(el => el.offsetWidth > 0 || el.offsetHeight > 0);
            forms.forEach(form => {
                let { score, reasons, fields, heading } = scoreFormContainer(form, isModal, triggerText);
                if (score > 0) {
                    if (isModal) reasons.unshift(`Форма появилась после клика по кнопке: "${triggerText}"`);
                    updateScore(isModal ? 'MODAL_FORM' : 'DIRECT_FORM', score, reasons, window.location.href, fields, heading, triggerText);
                }
            });

            if (bestResult.score < 50) {
                let { score, reasons, fields, heading } = scoreFormContainer(document.body, isModal, triggerText);
                if (reasons.some(r => r.includes("Поле загрузки файла")) || reasons.some(r => r.match(/Текстовые поля \([2-9]/))) {
                    reasons.unshift("Форма без тега <form> (поля разбросаны в HTML)");
                    if (isModal) reasons.unshift(`Форма появилась после клика по кнопке: "${triggerText}"`);
                    updateScore(isModal ? 'MODAL_FORM' : 'DIRECT_FORM', score, reasons, window.location.href, fields, heading, triggerText);
                }
            }
        };

        const collectExternalForms = () => {
            const checkUrl = (u) => u && (
                u.includes('docs.google.com/forms') || 
                u.includes('forms.yandex.ru') || 
                u.includes('forms.gle') ||
                u.includes('forms.amocrm.ru') ||  
                u.includes('bitrix24.ru')         
            );
            
            document.querySelectorAll('iframe').forEach(el => {
                const src = el.src || el.getAttribute('data-src');
                if (checkUrl(src) && !externalForms.some(f => f.url === src)) {
                    let parentText = el.parentElement ? (el.parentElement.innerText || '') : '';
                    externalForms.push({url: src, context: parentText.substring(0, 500)});
                }
            });
            document.querySelectorAll('a').forEach(el => {
                const href = el.href || el.getAttribute('href');
                if (checkUrl(href) && !externalForms.some(f => f.url === href)) {
                    externalForms.push({url: href, context: (el.innerText || '').substring(0, 500)});
                }
            });
        };

        // ЭТАП 1
        scanDom(false);
        collectExternalForms();

        // ЭТАП 2
        //if (bestResult.score < 120) {
        const modalTriggerRegex = /отклик|присоедин|заполн|отправ|резюме|apply|join|cv/i;
        
        // Выносим поиск кнопок в функцию, чтобы вызывать её заново
        const getTargetButtons = () => {
            return Array.from(document.querySelectorAll('a, button, [role="button"], input[type="button"], [class*="btn" i], [class*="button" i]')).filter(btn => {
                const text = (btn.innerText || btn.textContent || btn.value || '').toLowerCase().trim();
                const isVisible = btn.offsetWidth > 0 || btn.offsetHeight > 0 || btn.getClientRects().length > 0;
                
                let isNavigation = false;
                if (btn.tagName === 'A' && btn.href) {
                    const hrefAttr = btn.getAttribute('href');
                    if (hrefAttr && hrefAttr !== '#' && !hrefAttr.startsWith('#') && !hrefAttr.startsWith('javascript')) {
                        isNavigation = true;
                    }
                }
                if (btn.closest('form')) isNavigation = true;

                // if (btn.closest('footer, nav')) isNavigation = true;
                if (!isStartPage && btn.closest('footer, nav, [class*="nav" i]')) {
                    isNavigation = true;
                }
                
                return isVisible && !isNavigation && modalTriggerRegex.test(text) && !junkRegex.test(text);
            });
        };

        // Узнаем, сколько всего кнопок было изначально
        let initialBtnCount = getTargetButtons().length;

        for (let i = 0; i < initialBtnCount; i++) {
            // ВАЖНО: Запрашиваем элементы заново на каждом шаге!
            // Это спасает от ошибки "Detached DOM node", если DOM перерисовался
            let currentButtons = getTargetButtons();
            
            // Если кнопок стало меньше (страница как-то сломалась), прерываем
            if (i >= currentButtons.length) break; 
            
            let btn = currentButtons[i];
            const btnText = (btn.innerText || btn.textContent || btn.value || '').trim().replace(/\s+/g, ' ').substring(0, 25);
            
            // Запоминаем URL до клика
            const originalUrl = window.location.href.split('?')[0].split('#')[0];

            try {
                btn.scrollIntoView({block: "center"});
                btn.click(); 
                await new Promise(r => setTimeout(r, 1000)); // Ждем реакции сайта
                
                const currentUrl = window.location.href.split('?')[0].split('#')[0];
                
                if (currentUrl !== originalUrl) {
                    // ПРОИЗОШЕЛ ПЕРЕХОД! (SPA-роутинг)
                    // Это не модалка, возвращаемся назад
                    window.history.back();
                    await new Promise(r => setTimeout(r, 1000)); // Ждем загрузки прошлой страницы
                    
                    // Делаем continue! Индекс 'i' увеличится, и мы проверим следующую кнопку
                    continue; 
                }
                
                // Если URL не изменился — проверяем модалку
                scanDom(true, btnText);
                collectExternalForms();
                
                // Если нашли топовую форму (150+ баллов), можно дальше не кликать
                if (bestResult.score >= 150) break;
                
                // Пытаемся закрыть модалку через Escape перед следующим кликом
                document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                await new Promise(r => setTimeout(r, 400));
            } catch(e) {
                // Игнорируем ошибку конкретной кнопки и идем дальше
            }
        }
        

        return { ...bestResult, externalForms };
    }
    """
    try:
        # return await page.evaluate(js_code)
        best_result = await page.evaluate(js_code, [is_start_page])
    except Exception as e:
        # return {
        best_result = {
            "type": "ERROR",
            "score": 0,
            "reasons": [str(e)],
            "detected_url": url,
            "externalForms": [],
            "fields": [],
            "heading": "",
            "modal_trigger": "",
        }

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_result = await frame.evaluate(js_code, [is_start_page])
            if frame_result["score"] > best_result["score"]:
                best_result["score"] = frame_result["score"]
                best_result["type"] = "IFRAME_FORM"
                best_result["detected_url"] = frame.url
                best_result["reasons"] = [
                    f"Найдено внутри скрытого iframe ({frame.url})"
                ] + frame_result["reasons"]
                best_result["fields"] = frame_result.get("fields", [])
                best_result["heading"] = frame_result.get("heading", "")
                best_result["modal_trigger"] = frame_result.get("modal_trigger", "")
                if "externalForms" in frame_result:
                    best_result["externalForms"].extend(
                        frame_result.get("externalForms", [])
                    )
        except Exception:
            pass

    return best_result

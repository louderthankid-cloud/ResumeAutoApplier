import re
from playwright.async_api import Page
from services.llm_client import submit_prompt
from agents_tools.browser_tools import get_scout_tools, get_applier_tools


async def _clear_cookies(page: Page):
    """Скрывает назойливые баннеры куки"""
    print("[SYSTEM] Очищаю страницу от всплывающих окон...")
    await page.evaluate("""
        () => {
            const overlays = document.querySelectorAll(
                '#accept-choices, .snigel-cmp-framework, [id*="cookie"], [class*="cookie"]'
            );
            overlays.forEach(el => el.style.display = 'none');
            document.body.style.overflow = 'auto'; // Возвращаем скролл
        }
    """)


async def run_browser_pipeline(
    page: Page, start_url: str, user_data: dict, session_id: str
) -> str:
    """
    Главный конвейер.
    ЭТАП 1: Разведчик ищет вакансию.
    ЭТАП 2: Исполнитель заполняет форму.
    """
    try:
        print(f"\n{'='*50}\n[ЭТАП 1] запуск разведчика\n{'='*50}")
        print(f"[{session_id}] Стартовый URL: {start_url}")

        await page.goto(start_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await _clear_cookies(page)

        scout_context = {"url": start_url, "target_job": user_data["target_job"]}

        scout_result = await submit_prompt(
            template_name="scout_agent",
            context_vars=scout_context,
            task_name="scout_agent",
            json_mode=False,
            tools=get_scout_tools(page),
            thread_id=f"{session_id}_scout",
        )

        target_url = None
        if not isinstance(scout_result, dict) or scout_result.get("status") != "done":
            return f"Разведчик не завершился корректно: {scout_result}"

        target_url = scout_result["payload"].get("final_url")
        if not target_url:
            return f"Разведчик завершился, но final_url не передал: {scout_result}"

        print(f"Разведчик успешно добыл ссылку: {target_url}")

        print(f"\n{'='*50}\n[ЭТАП 2] запуск исполнителя\n{'='*50}")
        print(f"[{session_id}] Перехожу на найденную страницу...")

        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await _clear_cookies(page)

        # Разрешаем подтверждение отправки формы
        custom_interrupt_policy = {
            # "click_element": {"allowed_decisions": ["approve", "reject"]},
        }

        # Обновляем URL в user_data, чтобы Исполнитель знал, где он находится
        user_data["url"] = target_url

        applier_result = await submit_prompt(
            template_name="form_agent",
            context_vars=user_data,
            task_name="form_agent",
            json_mode=False,
            tools=get_applier_tools(page),
            thread_id=f"{session_id}_applier",
            interrupt_policy=custom_interrupt_policy,
        )

        await page.wait_for_timeout(3000)

        if isinstance(applier_result, dict) and applier_result.get("status") == "done":
            return applier_result["payload"]
        return applier_result

    except Exception as e:
        return f"Критическая ошибка браузерного пайплайна: {str(e)}"

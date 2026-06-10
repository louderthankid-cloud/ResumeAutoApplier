import json
from services.llm_client import submit_prompt


async def route_site(forms_data: list) -> dict:
    print(f"\n[LLM Router] Анализирую {len(forms_data)} найденных форм")

    context = {"forms_json": json.dumps(forms_data, ensure_ascii=False, indent=2)}

    try:
        response_text = await submit_prompt(
            template_name="site_router",
            context_vars=context,
            task_name="router_agent",
            json_mode=True,
        )
        clean_text = response_text.replace("```json", "").replace("```", "").strip()
        decision_data = json.loads(clean_text)

        print(f"[LLM Router] ВЕРДИКТ: {decision_data.get('decision')}")
        print(f"[LLM Router] URL: {decision_data.get('target_url')}")
        print(f"[LLM Router] Логика: {decision_data.get('reasoning')}")

        return decision_data
    except Exception as e:
        print(f"[LLM Router] Ошибка маршрутизации: {e}")
        return {
            "decision": "NO_HR_FORMS",
            "target_url": None,
            "modal_trigger": None,
            "reasoning": f"Ошибка LLM: {e}",
        }

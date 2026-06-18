from services.llm_client import submit_prompt


async def generate_cover_letter(
    resume_text: str,
    target_job: str,
    vacancy_text: str,
    company_name: str = "",
) -> str:
    """
    генерирует сопроводительное письмо под конкретную вакансию через ллм, возвращает текст письма
    """
    print(
        f"[CoverLetter] Генерирую письмо под '{target_job}'"
        f"{f' для {company_name}' if company_name else ''}..."
    )

    context = {
        "resume_text": resume_text,
        "target_job": target_job,
        "vacancy_text": (vacancy_text or "")[:4000],
        "company_name": company_name,
    }

    letter = await submit_prompt(
        template_name="cover_letter",
        context_vars=context,
        task_name="cover_letter",
        json_mode=False,
    )

    print(f"[CoverLetter] Письмо сгенерировано ({len(letter)} символов)")
    return letter

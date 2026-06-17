import json

from services.llm_client import submit_prompt


def _clean_json(text: str) -> str:
    if not text:
        return "{}"
    return text.replace("```json", "").replace("```", "").strip()


async def generate_hh_queries(target_job: str, verbose: bool = True) -> list[str]:
    """возвращает список вариантов названия должности; target_job всегда первым"""

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    log(f"[HHQuery] генерирую варианты названия для HH из '{target_job}'...")
    queries: list[str] = []
    try:
        response = await submit_prompt(
            template_name="hh_query_generator",
            context_vars={"target_job": target_job},
            task_name="hh_query",
            json_mode=True,
        )
        data = json.loads(_clean_json(response))
        queries = data.get("queries") or []
    except Exception as e:
        log(f"[HHQuery] ошибка генерации: {e}")

    # канонический target_job всегда первым; дедуп без учёта регистра
    out: list[str] = []
    seen: set[str] = set()
    for q in [target_job, *queries]:
        q = (q or "").strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)

    log(f"[HHQuery] варианты ({len(out)}): {out}")
    return out

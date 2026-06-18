import asyncio


def run_candidate_process(
    candidate_id: str, hh_limit: int, dry_run: bool, queue
) -> None:
    """запускается в дочернем процессе, свой ивент луп, своя бд/браузер"""
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(_amain(candidate_id, hh_limit, dry_run, queue))


async def _amain(candidate_id: str, hh_limit: int, dry_run: bool, queue) -> None:
    from core.config import settings
    from services.db_service import DBService
    from services.pipeline import run_candidate_pipeline

    candidate = await DBService.get_candidate_by_id(candidate_id)
    if not candidate:
        queue.put({"event": "error", "msg": "кандидат не найден"})
        queue.put({"event": "__end__"})
        return

    async def on_progress(event: dict) -> None:
        try:
            queue.put(event)
        except Exception:
            pass

    try:
        await run_candidate_pipeline(
            candidate,
            hh_limit=hh_limit,
            workers=settings.PIPELINE_POOL_SIZE,
            dry_run=dry_run,
            verbose=False,
            on_progress=on_progress,
        )
    except Exception as e:
        queue.put({"event": "error", "msg": str(e).splitlines()[0][:200]})
    finally:
        queue.put({"event": "__end__"})

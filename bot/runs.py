import asyncio
import multiprocessing as mp
import queue as pyqueue
import time
from dataclasses import dataclass, field

from core.config import settings
from services.db_service import DBService
from services.pipeline_worker import run_candidate_process
from bot import keyboards as kb
from bot.formatters import progress_panel, outcome_summary, esc
from bot.utils import safe_edit


@dataclass
class RunHandle:
    candidate_id: str
    process: mp.Process
    queue: object
    chat_id: int
    msg_id: int
    tg_id: int
    dry_run: bool
    stop_requested: bool = False
    drain_task: asyncio.Task | None = field(default=None)


class RunManager:
    def __init__(self) -> None:
        self._running: dict[str, RunHandle] = {}
        self._ctx = mp.get_context("spawn")

    def is_running(self, candidate_id: str) -> bool:
        h = self._running.get(candidate_id)
        return h is not None and h.process.is_alive()

    def active_count(self) -> int:
        return sum(1 for h in self._running.values() if h.process.is_alive())

    def can_start(self) -> bool:
        return self.active_count() < settings.MAX_CONCURRENT_RUNS

    async def start(
        self,
        bot,
        chat_id: int,
        tg_id: int,
        candidate,
        hh_limit: int,
        dry_run: bool,
        target=run_candidate_process,
    ) -> tuple[bool, str]:
        if self.is_running(candidate.id):
            return False, "Этот кандидат уже запущен."
        if not self.can_start():
            return (
                False,
                f"Лимит одновременных прогонов ({settings.MAX_CONCURRENT_RUNS}). Дождись завершения.",
            )

        mode = "тест" if dry_run else "боевой"
        msg = await bot.send_message(
            chat_id,
            f"Запускаю «{esc(candidate.name or candidate.target_job)}» · {mode}…",
            reply_markup=kb.stop_kb(candidate.id),
        )

        q = self._ctx.Queue()
        proc = self._ctx.Process(
            target=target, args=(candidate.id, hh_limit, dry_run, q), daemon=True
        )
        proc.start()

        handle = RunHandle(
            candidate_id=candidate.id,
            process=proc,
            queue=q,
            chat_id=chat_id,
            msg_id=msg.message_id,
            tg_id=tg_id,
            dry_run=dry_run,
        )
        self._running[candidate.id] = handle
        handle.drain_task = asyncio.create_task(self._drain(bot, handle))
        return True, ""

    def stop(self, candidate_id: str) -> bool:
        h = self._running.get(candidate_id)
        if not h or not h.process.is_alive():
            return False
        h.stop_requested = True
        try:
            h.process.terminate()
        except Exception:
            pass
        return True

    def shutdown_all(self) -> None:
        for h in list(self._running.values()):
            try:
                h.process.terminate()
            except Exception:
                pass

    async def _drain(self, bot, handle: RunHandle) -> None:
        cid = handle.candidate_id
        inflight: dict[int, str] = {}
        counts = {"done": 0, "ok": 0, "failed": 0, "total": 0}
        last = {"t": 0.0}
        error_msg = None

        async def render(force: bool = False) -> None:
            now = time.monotonic()
            if not force and now - last["t"] < 2.0:
                return
            last["t"] = now
            await safe_edit(
                bot,
                handle.chat_id,
                handle.msg_id,
                progress_panel(
                    counts["done"],
                    counts["total"],
                    counts["ok"],
                    counts["failed"],
                    list(inflight.values()),
                    handle.dry_run,
                ),
                kb.stop_kb(cid),
            )

        try:
            while True:
                try:
                    event = await asyncio.to_thread(handle.queue.get, True, 1.0)
                except pyqueue.Empty:
                    if not handle.process.is_alive():
                        break
                    continue

                et = event.get("event")
                if et == "__end__":
                    break
                if et == "error":
                    error_msg = event.get("msg", "")
                elif et == "start":
                    counts["total"] = event["total"]
                    await render(force=True)
                elif et == "company_start":
                    inflight[event["task_id"]] = event["company"]
                    await render()
                elif et == "company":
                    inflight.pop(event["task_id"], None)
                    counts.update(
                        done=event["done"],
                        ok=event["ok"],
                        failed=event["failed"],
                        total=event["total"],
                    )
                    await render()
        finally:
            await asyncio.to_thread(handle.process.join, 5)
            if handle.process.is_alive():
                try:
                    handle.process.terminate()
                except Exception:
                    pass
            await self._finalize(bot, handle, error_msg)
            self._running.pop(cid, None)

    async def _finalize(self, bot, handle: RunHandle, error_msg) -> None:
        cid = handle.candidate_id
        try:
            ost = await DBService.get_outcome_stats(cid)
            body = outcome_summary(ost)
        except Exception:
            body = "(не удалось получить статистику)"

        if handle.stop_requested:
            head = "<b>Остановлено</b>\n\n"
        elif error_msg:
            head = f"<b>Ошибка прогона</b>: {esc(error_msg)}\n\n"
        else:
            head = f"<b>Готово</b> · {'тест' if handle.dry_run else 'боевой'}\n\n"

        await safe_edit(
            bot, handle.chat_id, handle.msg_id, head + body, kb.results_open_kb(cid)
        )


run_manager = RunManager()

import time
import langchain
import asyncio
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential
import json

from langchain.agents import create_agent

from langchain_core.messages import (
    SystemMessage,
    HumanMessage,
    BaseMessage,
    trim_messages,
)
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks import BaseCallbackHandler

# from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.memory import InMemorySaver

from langchain.agents.middleware import HumanInTheLoopMiddleware, AgentMiddleware
from langgraph.types import Command
from langgraph.graph.message import add_messages

from typing import Any, TypedDict
from typing_extensions import Annotated

from llm.llm_factory import get_llm
from services.prompt_manager import render_prompt
from core.llm_logger import log_llm_interaction
from core.config import settings
from core.model_config import model_router


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    pipeline_state: dict[str, Any]


session_stats = {}


class AgentLogCallback(BaseCallbackHandler):
    """Слушает события агента и пишет их в консоль и файл логов"""

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.session_stats = {"calls": 0, "tokens": 0}
        self._last_tool = None
        self.tool_call_success = True

    def on_chat_model_start(self, serialized: dict, messages: list, **kwargs):
        total_chars = 0
        for seq in messages:
            for msg in seq:
                content = msg.content
                if isinstance(content, str):
                    total_chars += len(content)
                else:
                    total_chars += len(str(content))

        estimated_tokens = total_chars // 4

        session_stats[self.thread_id]["calls"] += 1
        session_stats[self.thread_id]["tokens"] += estimated_tokens

        if settings.LLM_CALL_LOG:
            print(f"\n[МОНИТОРИНГ КОНТЕКСТА] Отправляем данные в LLM...")
            print(f"   -> Размер: ~{estimated_tokens} токенов ({total_chars} символов)")

            if estimated_tokens > 6000:
                print(
                    "   ВНИМАНИЕ (КРАСНАЯ ЗОНА): Контекст огромный! Возможны сильные тормоза, забывание инструкций и галлюцинации."
                )
            elif estimated_tokens > 4000:
                print(
                    "   ЖЕЛТАЯ ЗОНА: Контекст начал раздуваться. Держим под наблюдением."
                )

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs):
        tool_name = serialized.get("name", "unknown_tool")
        self._last_tool = tool_name
        self.tool_call_success = True
        if settings.LLM_CALL_LOG:
            msg = f"\n[АГЕНТ ВЫЗЫВАЕТ ИНСТРУМЕНТ]: {tool_name}\n[ПЕРЕДАННЫЕ АРГУМЕНТЫ]: {input_str}\n"
            print(msg)

    def on_tool_end(self, output: str, **kwargs):
        short_out = str(output)[:1000]
        if settings.LLM_CALL_LOG:
            msg = f"\n[РЕЗУЛЬТАТ ИНСТРУМЕНТА]:\n{short_out}...\n"
            print(msg)

    def on_tool_error(self, error: Exception, **kwargs):
        self.tool_call_success = False
        if settings.LLM_CALL_LOG:
            msg = f"\n[ОШИБКА ИНСТРУМЕНТА]: {str(error)}\n"
            print(msg)

    def get_stats(self):
        return self.session_stats


class TrimMessagesMiddleware(AgentMiddleware):
    """Обрезает историю сообщений перед каждым вызовом LLM"""

    def __init__(self, max_messages: int = 20):
        super().__init__()
        self.max_messages = max_messages

    def before_model(self, state, runtime):
        trimmed = trim_messages(
            state["messages"],
            max_tokens=self.max_messages,
            strategy="last",
            token_counter=len,
            include_system=True,
            start_on="human",
            allow_partial=False,
        )
        return {"messages": trimmed}

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)


def _extract_text_from_content(content) -> str:
    """Извлекает чистый текст из ответа LLM"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                text_parts.append(item["text"])
        return "\n".join(text_parts)
    return str(content)


async def submit_prompt(
    template_name: str,
    context_vars: dict,
    task_name: str,
    user_message: str = None,
    json_mode: bool = False,
    tools: list = None,  # нужно для агентов
    thread_id: str = "default_thread",  # id сессии
    interrupt_policy: dict = None,
) -> str | dict[str, Any]:
    """
    Выполняет запрос к LLM используя шаблон Jinja2.
    """
    start_time = time.time()

    # Юзаем шаблоны жинжа
    full_prompt_text = render_prompt(template_name, **context_vars)

    # print("full_prompt_text:", full_prompt_text)

    # Получаем модель
    llm = get_llm(task_name=task_name, json_mode=json_mode)

    callback = AgentLogCallback(thread_id=thread_id)

    content = ""
    try:
        if tools:
            task_config = model_router.get_task_config(task_name)
            max_iterations = task_config.get("max_iterations", 12)

            middleware = [TrimMessagesMiddleware(max_messages=20)]
            if interrupt_policy:
                hitl_middleware = HumanInTheLoopMiddleware(
                    interrupt_on=interrupt_policy,
                    description_prefix="Требуется подтверждение:",
                )
                middleware.append(hitl_middleware)

            # checkpointer = InMemorySaver()
            async with AsyncSqliteSaver.from_conn_string(
                "checkpoints.sqlite"
            ) as checkpointer:

                agent = create_agent(
                    model=llm,
                    tools=tools,
                    system_prompt=full_prompt_text,
                    middleware=middleware,
                    checkpointer=checkpointer,
                    state_schema=AgentState,
                )

                config = {
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": max_iterations * 5,
                    "callbacks": [callback],
                }

                initial_msg = user_message or "Начинай выполнение задачи по инструкции."
                invoke_payload = {"messages": [("user", initial_msg)]}

                # убираем глобальный ретрай и делаем его строго к запуску
                # async for attempt in AsyncRetrying(
                #    stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10)
                # ):
                #    with attempt:
                #        result = await agent.ainvoke(invoke_payload, config=config)

                result = await agent.ainvoke(
                    {"messages": [("user", initial_msg)]}, config=config
                )

                pipeline_state = (
                    result.get("pipeline_state", {}) if isinstance(result, dict) else {}
                )

                stats = callback.get_stats()

                if pipeline_state.get("done"):
                    return {
                        "status": "done",
                        "phase": pipeline_state.get("phase"),
                        "payload": pipeline_state,
                        "messages": result.get("messages", []),
                        "stats": stats,
                    }

                # HITL
                while (
                    result and isinstance(result, dict) and result.get("__interrupt__")
                ):
                    interrupt_info = result["__interrupt__"][0].value
                    action_requests = interrupt_info.get("action_requests", [])

                    if not action_requests:
                        break

                    decisions = []
                    for action in action_requests:
                        if isinstance(action, dict):
                            tool_name = action.get("name", "Unknown")
                            tool_args = action.get("args", {})
                            action_id = action.get("id")
                        else:
                            tool_name = getattr(action, "name", "Unknown")
                            tool_args = getattr(action, "args", {})
                            action_id = getattr(action, "id", None)

                        if settings.DRY_RUN:
                            print(
                                f"   [DRY RUN] Агент хочет выполнить '{tool_name}'. Автоматически ОТКЛОНЯЕМ для безопасности."
                            )
                            decisions.append(
                                {
                                    "type": "reject",
                                    "action_request_id": action_id,
                                }
                            )
                        else:
                            print(f"\nАГЕНТ ЖДЕТ РАЗРЕШЕНИЯ НА: {tool_name}")
                            print(f"Аргументы: {tool_args}")

                            # Временно используем input() консоли. Позже переделаем под Telegram
                            user_choice = (
                                input("Действие (approve / reject / edit): ")
                                .strip()
                                .lower()
                            )

                            if user_choice == "approve":
                                decisions.append(
                                    {
                                        "type": "approve",
                                        "action_request_id": action_id,
                                    }
                                )
                            elif user_choice == "edit":
                                new_args_str = input("Введите новые аргументы (JSON): ")
                                try:
                                    decisions.append(
                                        {
                                            "type": "edit",
                                            "action_request_id": action_id,
                                            "updated_args": json.loads(new_args_str),
                                        }
                                    )
                                except:
                                    print("Неверный JSON, отклоняем.")
                                    decisions.append(
                                        {
                                            "type": "reject",
                                            "action_request_id": action_id,
                                        }
                                    )
                            else:
                                decisions.append(
                                    {
                                        "type": "reject",
                                        "action_request_id": action_id,
                                    }
                                )

                    # Возобновляем выполнение агента с нашими решениями
                    command = Command(resume={"decisions": decisions})

                    # async for attempt in AsyncRetrying(
                    #    stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10)
                    # ):
                    #    with attempt:
                    #        result = await agent.ainvoke(command, config=config)
                    # тоже ретраи
                    result = await agent.ainvoke(command, config=config)

                pipeline_state = (
                    result.get("pipeline_state", {}) if isinstance(result, dict) else {}
                )

                stats = callback.get_stats()

                if pipeline_state.get("done"):
                    return {
                        "status": "done",
                        "phase": pipeline_state.get("phase"),
                        "payload": pipeline_state,
                        "messages": result.get("messages", []),
                        "stats": stats,
                    }

                # Вытаскиваем финальный ответ
                content = _extract_text_from_content(result["messages"][-1].content)
                return content

        else:

            # билдим сообщения
            messages = [HumanMessage(content=full_prompt_text)]
            if user_message:
                messages.append(HumanMessage(content=user_message))

            model_name = getattr(
                llm, "model_name", getattr(llm, "model", "Unknown Model")
            )
            print(f"--- [DEBUG] Task: {task_name} | Model: {model_name} ---")
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10)
            ):
                with attempt:
                    response = await llm.ainvoke(messages)
                    content = _extract_text_from_content(response.content)

            # response = await llm.ainvoke(messages)
            print("--- [DEBUG] LLM Response received ---")
            # content = response.content
            # content = _extract_text_from_content(content)
            return content

    except Exception as e:
        print(f"[ERROR] вызов llm не удался: {e} ---")
        content = f"ERROR: {str(e)}"
        raise e

    finally:
        await asyncio.to_thread(
            log_llm_interaction,
            full_prompt_text,
            user_message or "",
            content,
            start_time,
        )

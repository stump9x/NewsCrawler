from ai_prompter import Prompter
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.domain.notebook import Source
from open_notebook.domain.transformation import DefaultPrompts, Transformation
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content

# Summary-style presets: shorter budget → faster TTFT / complete.
_SHORT_TRANSFORM_NAMES = frozenset(
    {
        "simple summary",
        "key insights",
        "table of contents",
        "reflections",
    }
)


def transformation_max_tokens(
    transformation: Transformation,
    override: int | None = None,
) -> int:
    """Pick an output budget: request override > preset heuristic > default."""
    if override is not None:
        try:
            return max(256, min(int(override), 8192))
        except (TypeError, ValueError):
            pass
    name = str(getattr(transformation, "name", "") or "").strip().lower()
    title = str(getattr(transformation, "title", "") or "").strip().lower()
    if name in _SHORT_TRANSFORM_NAMES or "tóm tắt tình hình" in title:
        return 1536
    if name == "dense summary":
        return 2560
    if name == "analyze paper":
        return 4096
    if name == "translate formal vn":
        return 6144
    return 4096


class TransformationState(TypedDict):
    input_text: str
    source: Source
    transformation: Transformation
    output: str


async def run_transformation(state: dict, config: RunnableConfig) -> dict:
    source_obj = state.get("source")
    source: Source = source_obj if isinstance(source_obj, Source) else None  # type: ignore[assignment]
    content = state.get("input_text")
    assert source or content, "No content to transform"
    transformation: Transformation = state["transformation"]

    try:
        if not content:
            content = source.full_text
        # transformation.prompt is user-controlled free text. Never compile it as
        # Jinja template *source* (Prompter(template_text=...)) - pass it as a
        # plain render variable into a fixed, developer-authored template instead.
        # See docs/7-DEVELOPMENT/security.md (GHSA-f35w-wx37-26q7).
        instructions = transformation.prompt
        # Load shared default instructions from DB (do NOT construct with
        # transformation_instructions=None — RecordModel singleton would wipe them).
        default_prompts: DefaultPrompts = await DefaultPrompts.get_instance()  # type: ignore[assignment]
        if default_prompts.transformation_instructions:
            instructions = f"{default_prompts.transformation_instructions}\n\n{instructions}"

        system_prompt = Prompter(prompt_template="transformation/execute").render(
            data={**state, "instructions": instructions}
        )
        content_str = str(content) if content else ""
        payload = [SystemMessage(content=system_prompt), HumanMessage(content=content_str)]
        cfg = config.get("configurable", {}) or {}
        override_tokens = cfg.get("max_tokens")
        max_tokens = transformation_max_tokens(
            transformation,
            int(override_tokens) if override_tokens is not None else None,
        )
        chain = await provision_langchain_model(
            str(payload),
            cfg.get("model_id"),
            "transformation",
            max_tokens=max_tokens,
        )

        response = await chain.ainvoke(payload)

        # Clean thinking content from the response
        response_content = extract_text_content(response.content)
        cleaned_content = clean_thinking_content(response_content)

        if source:
            await source.add_insight(transformation.title, cleaned_content)

        return {
            "output": cleaned_content,
        }
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


agent_state = StateGraph(TransformationState)
agent_state.add_node("agent", run_transformation)  # type: ignore[type-var]
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile()

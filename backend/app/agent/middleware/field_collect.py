"""Generic HITL field-collection middleware — no per-business code needed.

Business tools declare their required/validated fields ONCE via
:func:`registry.register`. When the model emits a tool call whose args are
missing or invalid against that declaration, this middleware interrupts the
graph (in the model-node hook — an in-tool ``interrupt()`` would be turned
into a tool ERROR by deepagents' ToolNode), collects the human's answers and
patches the tool-call args — repeating until every declared field passes.
Then the patched tool call executes normally.

Adding a new business scenario is just:

    registry.register("book_ticket", [
        FieldSpec("passenger_name", "乘车人姓名"),
        FieldSpec("id_number", "身份证号", pattern=r"\\d{17}[\\dXx]", hint="18位"),
        FieldSpec("departure", "出发站"),
    ])

The frontend renders a generic form from the ``missing``/``invalid`` lists
(labels + hints) and answers via ``POST /resume`` — no per-business UI.

The interrupt payload is uniform:

    {"request": "fill_fields", "tool": <tool_name>,
     "missing": [{"name", "label", "hint"}],
     "invalid": [{"name", "label", "hint"}],
     "known": {<field>: <value already present>}}
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldSpec:
    """One declared field of a business tool."""

    name: str
    label: str = ""
    required: bool = True
    pattern: str | None = None       # fullmatch regex; None = no format check
    hint: str | None = None          # format hint, e.g. "11位手机号"
    placeholder: str | None = None   # example shown in the form input, e.g. "13800138000"


class FieldCollectRegistry:
    """tool_name → declared fields. Business modules call :meth:`register`."""

    def __init__(self) -> None:
        self._specs: dict[str, tuple[FieldSpec, ...]] = {}

    def register(self, tool_name: str, specs: list[FieldSpec]) -> None:
        if not tool_name:
            raise ValueError("tool_name must not be blank")
        self._specs[tool_name] = tuple(specs)
        logger.debug("field collect registered: tool=%s fields=%d", tool_name, len(specs))

    def for_tool(self, tool_name: str) -> tuple[FieldSpec, ...]:
        return self._specs.get(tool_name, ())


registry = FieldCollectRegistry()


def _prompt_of(spec: FieldSpec, *, invalid: bool) -> str:
    """Ready-to-show prompt sentence for the form input."""
    label = spec.label or spec.name
    if invalid:
        text = f"请重新输入{label}"
        if spec.hint:
            text += f"({spec.hint})"
        return text
    text = f"请输入{label}"
    if spec.placeholder:
        text += f",例如:{spec.placeholder}"
    elif spec.hint:
        text += f"({spec.hint})"
    return text


def _problems(
    specs: tuple[FieldSpec, ...], args: dict
) -> tuple[list[dict], list[dict]]:
    """Split the tool-call args into missing and invalid declared fields.

    Each entry is a complete, frontend-ready form field: ``prompt`` is the
    sentence to show above the input and ``placeholder`` the in-input
    example — the client renders them verbatim, no client-side copy needed.
    """
    missing: list[dict] = []
    invalid: list[dict] = []
    for spec in specs:
        value = (args.get(spec.name) or "").strip()
        if spec.required and not value:
            missing.append({
                "name": spec.name,
                "label": spec.label or spec.name,
                "hint": spec.hint,
                "placeholder": spec.placeholder,
                "prompt": _prompt_of(spec, invalid=False),
            })
        elif value and spec.pattern and not re.fullmatch(spec.pattern, value):
            invalid.append({
                "name": spec.name,
                "label": spec.label or spec.name,
                "hint": spec.hint,
                "placeholder": spec.placeholder,
                "prompt": _prompt_of(spec, invalid=True),
            })
    return missing, invalid


class FieldCollectMiddleware(AgentMiddleware):
    """Pause before declared business tools until their args are complete.

    Mirrors deepagents' HumanInTheLoopMiddleware pattern: inspect the last
    AIMessage after the model, interrupt with the missing/invalid fields,
    then patch the tool-call args with the human's answers. The hook re-runs
    on every resume (``interrupt()`` replays the recorded answers), so
    incomplete answers interrupt again automatically — no extra code.
    """

    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        del runtime  # hook signature requires it; unused here (silences Pylance)
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        ai: AIMessage = messages[-1]
        if not ai.tool_calls:
            return None

        patched = False
        for tool_call in ai.tool_calls:
            tool_name = tool_call.get("name")
            specs = registry.for_tool(tool_name) if tool_name else ()
            if not specs:
                continue
            args = dict(tool_call.get("args") or {})
            while True:
                missing, invalid = _problems(specs, args)
                if not missing and not invalid:
                    break
                answers = interrupt({
                    "request": "fill_fields",
                    "tool": tool_name,
                    "missing": missing,
                    "invalid": invalid,
                    "known": {
                        spec.name: v for spec in specs
                        if (v := (args.get(spec.name) or "").strip())
                        and spec.name not in {m["name"] for m in missing}
                        and spec.name not in {i["name"] for i in invalid}
                    },
                })
                if isinstance(answers, dict):
                    for entry in missing + invalid:
                        value = str(answers.get(entry["name"]) or "").strip()
                        if value:
                            args[entry["name"]] = value
            tool_call["args"] = args
            patched = True

        if not patched:
            return None
        return {"messages": [ai]}

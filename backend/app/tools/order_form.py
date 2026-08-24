"""Demo business tool: submit an order form — the ZERO-declaration natural
flow. The tool validates its own required fields and returns a missing-field
error; the model then asks the user in natural language and re-calls the
tool once the user answers. No registry, no middleware, no frontend form —
this is the default pattern for business tools.

Businesses that want the STRUCTURED form UX (labels, format validation,
pause-and-collect) opt in via ``app/agent/middleware/field_collect.py``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from langchain_core.tools import tool

from app.db import session as db_session
from app.db.models import FormSubmission

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 可选:结构化表单模式(与下面的自然语言流二选一)。
# 取消注释后,submit_order 缺字段时不再走"模型文字反问",而是系统暂停、
# 前端弹出表单 —— 每个字段自带完整提示(prompt)和示例(placeholder):
#
# from app.agent.middleware.field_collect import FieldSpec, registry
# registry.register(TOOL_NAME, [
#     FieldSpec("receiver_name", "收货人姓名", placeholder="张三"),
#     FieldSpec("receiver_phone", "收货人手机号",
#               pattern=r"1\d{10}", hint="11位手机号", placeholder="13800138000"),
#     FieldSpec("address", "收货地址", placeholder="北京市海淀区中关村大街1号"),
# ])
# --------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "receiver_name": "收货人姓名",
    "receiver_phone": "收货人手机号(11位)",
    "address": "收货地址",
}
PHONE_RE = re.compile(r"1\d{10}")


def build_submit_order_tool(conversation_id: uuid.UUID):
    """Factory: binds the conversation id so the demo row lands in it."""

    @tool
    def submit_order(
        receiver_name: str = "",
        receiver_phone: str = "",
        address: str = "",
        items: str = "",
    ) -> str:
        """Submit a delivery order (下单/提交订单). Use when the user asks to
        place or record an order. Required fields: receiver_name (收货人姓名),
        receiver_phone (收货人手机号, 11位), address (收货地址).

        Call this tool with the fields you know. If a required field is
        unknown, leave it empty and call anyway — the tool tells you exactly
        what is missing, then you ask the user for ONLY that, in natural
        language, and call the tool again once they answer. NEVER invent or
        guess values.
        """
        provided = {
            "receiver_name": receiver_name,
            "receiver_phone": receiver_phone,
            "address": address,
        }
        missing = [label for field, label in REQUIRED_FIELDS.items()
                   if not (provided.get(field) or "").strip()]
        if missing:
            return (
                f"无法提交订单:缺少必填字段 {missing}。"
                f"请用自然语言向用户询问这些信息,得到答案后再次调用本工具;不要编造。"
            )
        if not PHONE_RE.fullmatch(receiver_phone.strip()):
            return "无法提交订单:手机号格式不正确(需11位数字)。请向用户确认后再次调用。"

        # 入库 — 模拟真实业务写入。
        with db_session.SessionLocal() as db:
            row = FormSubmission(
                conversation_id=conversation_id,
                receiver_name=receiver_name.strip(),
                receiver_phone=receiver_phone.strip(),
                address=address.strip(),
                items=(items or "").strip() or None,
            )
            db.add(row)
            db.commit()
        logger.info(
            "order form saved: conversation=%s receiver=%s",
            conversation_id, receiver_name,
        )
        return "订单已提交入库: " + json.dumps(
            {
                "receiver_name": receiver_name.strip(),
                "receiver_phone": receiver_phone.strip(),
                "address": address.strip(),
            },
            ensure_ascii=False,
        )

    return submit_order

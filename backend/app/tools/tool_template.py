"""业务工具标准模板 —— 零声明自然语言流(默认模式)。

照这个模式写新业务工具即可,不需要任何中间件/注册/前端配合:

1. 用户说自然语言,模型自动提取成工具参数(这一步是 LLM 的默认行为)
2. 工具自己校验:缺哪些必填、格式对不对 —— **校验写在工具里,不靠模型自觉**
3. 校验失败时返回明确的错误文本:缺什么、让模型问什么、禁止编造
4. 模型用自然语言反问用户 → 用户自然语言回答 → 模型补全参数重调工具
5. 校验通过 → 执行业务动作(入库/调接口),返回结果

想要"表单弹窗"体验的业务,改走结构化模式:在
``app/agent/middleware/field_collect.py`` 里 registry.register 声明字段即可。

参考实现:``app/tools/order_form.py``(已接入的下单示例)。
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.tools import tool

from app.db import session as db_session

logger = logging.getLogger(__name__)

# 1) 必填字段:参数名 → 给用户看的中文名(错误提示里直接引用)
REQUIRED_FIELDS = {
    "passenger_name": "乘车人姓名",
    "id_number": "身份证号",
    "departure_station": "出发站",
}

# 2) 格式校验:参数名 → (正则, 失败时给用户的提示)
FORMAT_CHECKS = {
    "id_number": (re.compile(r"\d{17}[\dXx]"), "18位身份证号"),
}


@tool
def book_ticket(
    passenger_name: str = "",
    id_number: str = "",
    departure_station: str = "",
    arrival_station: str = "",
) -> str:
    """预订火车票。必填字段:passenger_name(乘车人姓名)、id_number(身份证号)、
    departure_station(出发站)、arrival_station(到达站)。

    从用户的自然语言中提取已知字段并调用本工具,未知字段留空 —— 工具会
    返回缺什么,你再用自然语言问用户**只缺的那部分**,得到答案后再次调用。
    禁止编造或猜测任何值。
    """
    # 3) 统一校验入口 —— 所有校验都在这里,不靠模型"嘴上提醒"
    provided = {
        "passenger_name": passenger_name,
        "id_number": id_number,
        "departure_station": departure_station,
        "arrival_station": arrival_station,
    }
    missing = [label for field, label in REQUIRED_FIELDS.items()
               if not (provided.get(field) or "").strip()]
    if missing:
        return (
            f"无法预订:缺少必填字段 {missing}。"
            f"请用自然语言向用户询问这些信息,得到答案后再次调用本工具;不要编造。"
        )
    for field, (pattern, hint) in FORMAT_CHECKS.items():
        value = (provided.get(field) or "").strip()
        if value and not pattern.fullmatch(value):
            return (
                f"无法预订:{REQUIRED_FIELDS[field]}格式不正确(应为{hint})。"
                f"请向用户确认后再次调用;不要替用户改。"
            )

    # 4) 校验通过 → 业务动作(这里以写库为例;换成调用第三方 API 同理)
    with db_session.SessionLocal() as db:
        # db.add(YourModel(...)); db.commit()
        pass
    logger.info(
        "ticket booked: passenger=%s from=%s to=%s",
        passenger_name, departure_station, arrival_station,
    )
    return "已成功预订: " + json.dumps(
        {k: v.strip() for k, v in provided.items() if (v or "").strip()},
        ensure_ascii=False,
    )


# 5) 接入:app/tools/registry.py 的 build_tools() 里加一行
#        tools.append(book_ticket)
#    (需要按用户/会话隔离数据时,照 order_form 的工厂模式包一层闭包)

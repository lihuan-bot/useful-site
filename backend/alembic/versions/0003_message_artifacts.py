"""Add artifacts column to messages (persist agent file deliverables).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25

SSE ``artifact`` 事件（write_file 产物）此前只存在于流式会话中，刷新页面后
从 DB 拉取的消息里没有交付物信息，下载卡片丢失。该列在流结束时由
``finalize_stream_message`` 写入。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("artifacts", sa.dialects.postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "artifacts")

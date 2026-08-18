"""product events and feedback

Revision ID: 6c9b78f0c1ad
Revises: 58b9d5631e45
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c9b78f0c1ad"
down_revision: str | None = "58b9d5631e45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("recording_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("safe_properties", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_event_actor_created", "product_events", ["actor_id", "created_at"])
    op.create_index("ix_product_event_name_created", "product_events", ["name", "created_at"])
    op.create_index(
        "ix_product_event_recording_created", "product_events", ["recording_id", "created_at"]
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("recording_id", sa.String(length=36), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("route", sa.String(length=300), nullable=False),
        sa.Column("app_version", sa.String(length=80), nullable=False),
        sa.Column("may_contact", sa.Boolean(), nullable=False),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_status_created", "feedback", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_status_created", table_name="feedback")
    op.drop_table("feedback")
    op.drop_index("ix_product_event_recording_created", table_name="product_events")
    op.drop_index("ix_product_event_name_created", table_name="product_events")
    op.drop_index("ix_product_event_actor_created", table_name="product_events")
    op.drop_table("product_events")

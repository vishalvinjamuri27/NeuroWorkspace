"""job dispatch outbox tracking

Adds Job.dispatched_at so the API and a periodic sweep can tell the
difference between "committed to the database" and "confirmed delivered to
the broker" -- closing the gap where a process could crash or the broker
could be unreachable between the two, stranding a QUEUED job forever.

Revision ID: 7a1c4e9f2b3d
Revises: 6c9b78f0c1ad
Create Date: 2026-09-03 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7a1c4e9f2b3d'
down_revision: Union[str, Sequence[str], None] = '6c9b78f0c1ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True))
    # Existing rows predate outbox tracking. Every job that already reached a
    # terminal or running state was, by definition, dispatched by the old
    # fire-and-forget path; only leave rows the sweep should treat as pending
    # dispatch (there are none yet, since QUEUED-forever was exactly the bug
    # this migration fixes) with a NULL value.
    op.execute(
        "UPDATE jobs SET dispatched_at = created_at "
        "WHERE status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'CANCEL_REQUESTED')"
    )
    op.create_index('ix_jobs_status_lease', 'jobs', ['status', 'lease_expires_at'], unique=False)
    op.create_index(
        'ix_jobs_dispatch', 'jobs', ['status', 'dispatched_at', 'created_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_jobs_dispatch', table_name='jobs')
    op.drop_index('ix_jobs_status_lease', table_name='jobs')
    op.drop_column('jobs', 'dispatched_at')

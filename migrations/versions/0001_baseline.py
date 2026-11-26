"""Baseline: the schema as it exists in production.

This describes reality, not the ORM model. The live `statistics` table has a
`tg_id` column, `UNIQUE (tg_id, week)`, and *no* foreign key -- the FK was
added to the model later, and `Base.metadata.create_all()` never alters an
existing table, so it was never applied. Verified against production with
`\\d statistics` before writing this.

Existing databases are stamped at this revision (`alembic stamp 0001`); fresh
ones run it for real and then migrate forward like any other.

Revision ID: 0001
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "volunteers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="volunteers_pkey"),
        sa.UniqueConstraint("full_name", name="volunteers_full_name_key"),
        sa.UniqueConstraint("tg_id", name="volunteers_tg_id_key"),
    )
    op.create_table(
        "statistics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "last_modified",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="statistics_pkey"),
        sa.UniqueConstraint("tg_id", "week", name="_user_week_uc"),
    )


def downgrade() -> None:
    op.drop_table("statistics")
    op.drop_table("volunteers")

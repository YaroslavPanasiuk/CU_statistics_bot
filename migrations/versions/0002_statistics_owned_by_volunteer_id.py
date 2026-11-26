"""Own statistics by volunteers.id instead of the nullable volunteers.tg_id.

`statistics.tg_id` referenced a *nullable, non-primary* column. The model
declared a foreign key with ON DELETE CASCADE but no ON UPDATE, which was
never actually applied to the live database -- so in production the constraint
simply did not exist, and unregistering a volunteer (setting tg_id to NULL)
silently orphaned their rows instead of failing. On any fresh deployment the
constraint *would* exist and `/unregister_user` would raise.

Owning rows by volunteers.id fixes both: unregistering no longer touches the
link, and a volunteer who re-registers keeps their history rather than
inheriting whatever the previous holder of that tg_id recorded.

Pre-migration state, measured in production on 2026-11-26:
    701 statistics rows, 36 distinct tg_ids, 30 volunteers (28 registered)
    104 rows belong to 8 departed volunteers (weeks 7-19, last touched
        2026-05-09). Their names survive nowhere -- not in the database and
        not in any worksheet -- so they are archived rather than deleted.
      1 row is junk: {"answer_1": "/remind_admin"}, a command captured as an
        answer by the questionnaire's catch-all handler.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORPHANS = "NOT EXISTS (SELECT 1 FROM volunteers v WHERE v.tg_id = s.tg_id)"
COMMAND_ANSWERS = "EXISTS (SELECT 1 FROM jsonb_each_text(s.stats) kv WHERE kv.value LIKE '/%')"


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Somewhere to keep rows whose owner is unknowable.
    op.create_table(
        "statistics_archive",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="statistics_archive_pkey"),
        comment="Statistics whose volunteer was deleted before this migration; retained for historical totals.",
    )

    archived = conn.execute(
        sa.text(
            f"""
            INSERT INTO statistics_archive (id, tg_id, week, stats, last_modified)
            SELECT s.id, s.tg_id, s.week, s.stats, s.last_modified
            FROM statistics s WHERE {ORPHANS}
            """
        )
    ).rowcount

    # 2. Drop answers that are really commands the questionnaire swallowed.
    junk = conn.execute(sa.text(f"DELETE FROM statistics s WHERE {COMMAND_ANSWERS}")).rowcount

    # 3. Remove the now-archived orphans from the live table.
    orphans = conn.execute(sa.text(f"DELETE FROM statistics s WHERE {ORPHANS}")).rowcount

    # 4. Repoint ownership.
    op.add_column("statistics", sa.Column("volunteer_id", sa.Integer(), nullable=True))
    conn.execute(
        sa.text(
            "UPDATE statistics s SET volunteer_id = v.id FROM volunteers v WHERE v.tg_id = s.tg_id"
        )
    )

    # 5. Refuse to continue on a partial backfill rather than guessing.
    unowned = conn.execute(
        sa.text("SELECT count(*) FROM statistics WHERE volunteer_id IS NULL")
    ).scalar_one()
    if unowned:
        raise RuntimeError(
            f"{unowned} statistics row(s) could not be matched to a volunteer; "
            "aborting so no ownership is invented."
        )

    print(
        f"0002: archived {archived} orphaned row(s), deleted {junk} command-answer row(s), "
        f"removed {orphans} orphan(s) from statistics"
    )

    op.alter_column("statistics", "volunteer_id", nullable=False)

    # 6. Swap the identity column and its constraints.
    op.drop_constraint("_user_week_uc", "statistics", type_="unique")
    op.drop_column("statistics", "tg_id")
    op.create_unique_constraint("_volunteer_week_uc", "statistics", ["volunteer_id", "week"])
    op.create_foreign_key(
        "statistics_volunteer_id_fkey",
        "statistics",
        "volunteers",
        ["volunteer_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Restore tg_id ownership.

    Lossy by nature: a row whose volunteer has since unregistered has no tg_id
    to go back to and cannot be represented in the old schema, so such rows are
    dropped. Archived rows are returned to the live table.
    """
    conn = op.get_bind()

    op.drop_constraint("statistics_volunteer_id_fkey", "statistics", type_="foreignkey")
    op.drop_constraint("_volunteer_week_uc", "statistics", type_="unique")
    op.add_column("statistics", sa.Column("tg_id", sa.BigInteger(), nullable=True))
    conn.execute(
        sa.text(
            "UPDATE statistics s SET tg_id = v.tg_id FROM volunteers v WHERE v.id = s.volunteer_id"
        )
    )
    dropped = conn.execute(sa.text("DELETE FROM statistics WHERE tg_id IS NULL")).rowcount
    if dropped:
        print(f"0002 downgrade: dropped {dropped} row(s) whose volunteer has no tg_id")

    # volunteer_id must go before the archived rows come back: they have no
    # volunteer_id, and the column is still NOT NULL at this point.
    op.drop_column("statistics", "volunteer_id")

    restored = conn.execute(
        sa.text(
            """
            INSERT INTO statistics (id, tg_id, week, stats, last_modified)
            SELECT id, tg_id, week, stats, last_modified FROM statistics_archive
            ON CONFLICT DO NOTHING
            """
        )
    ).rowcount
    print(f"0002 downgrade: restored {restored} archived row(s)")

    op.alter_column("statistics", "tg_id", nullable=False)
    op.create_unique_constraint("_user_week_uc", "statistics", ["tg_id", "week"])
    op.drop_table("statistics_archive")

"""Configuration store: settings, variants, version counter, audit, admins.

Moves the bot's configuration out of the Google spreadsheet and into tables the
config web UI can write and the bot can poll.

Variants are rows rather than a ';;'-joined cell. Twenty-one keys hold several
interchangeable phrasings; packed into one cell, adding a variant silently
broke LexiconFilter, which compared an incoming message against the whole
packed string -- so a menu button stopped matching its own handler.

Nothing is populated here. migrate_config.py imports the spreadsheet's values,
so the import can be re-run and reviewed independently of the schema change,
and this migration stays reversible without data loss.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("key", name="settings_pkey"),
        comment="One row per configuration key; its values hang off setting_variants.",
    )

    op.create_table(
        "setting_variants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["key"], ["settings.key"],
            name="setting_variants_key_fkey", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="setting_variants_pkey"),
        sa.UniqueConstraint("key", "position", name="_setting_variant_uc"),
        comment="Interchangeable phrasings for a key, in display order.",
    )
    op.create_index("ix_setting_variants_key", "setting_variants", ["key"])

    op.create_table(
        "config_meta",
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name="config_meta_pkey"),
        comment="Holds config_version, which the bot polls to pick up edits.",
    )

    op.create_table(
        "config_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="config_audit_pkey"),
        comment="Who changed which key, when, and to what. The sheet could not say.",
    )
    op.create_index("ix_config_audit_changed_at", "config_audit", ["changed_at"])
    op.create_index("ix_config_audit_key", "config_audit", ["key"])

    op.create_table(
        "admins",
        # autoincrement=False, or Postgres attaches a sequence to the primary
        # key: an insert that omitted the id would grant admin rights to 1, 2, 3.
        sa.Column("tg_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("added_by", sa.String(length=100), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tg_id", name="admins_pkey"),
        comment=(
            "Admins granted from the web UI. ADMIN_IDS from .env is deliberately "
            "not copied here: that stays the way back in if this table is emptied."
        ),
    )

    op.create_table(
        "admin_invites",
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("invited_by", sa.String(length=100), nullable=False),
        sa.Column(
            "invited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username", name="admin_invites_pkey"),
        comment=(
            "A username invited before anyone knows their numeric id. The Bot API "
            "cannot resolve @someone to an id, so it is captured from the Telegram "
            "login payload on first sign-in and the invite consumed there."
        ),
    )


def downgrade() -> None:
    op.drop_table("admin_invites")
    op.drop_table("admins")
    op.drop_index("ix_config_audit_key", table_name="config_audit")
    op.drop_index("ix_config_audit_changed_at", table_name="config_audit")
    op.drop_table("config_audit")
    op.drop_table("config_meta")
    op.drop_index("ix_setting_variants_key", table_name="setting_variants")
    op.drop_table("setting_variants")
    op.drop_table("settings")

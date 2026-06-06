from alembic import op
import sqlalchemy as sa

# replace these with the values Alembic generated in your file
revision = "YOUR_REVISION_ID"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("appearance", sa.Text(), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "appearance IN ('light', 'dark', 'system')",
            name="user_settings_appearance_check",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade():
    op.drop_table("user_settings")
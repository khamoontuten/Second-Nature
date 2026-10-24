"""Strategy impact studies (individual, combination and pairwise evaluations against the baseline).

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "impact_studies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_version_no", sa.Integer(), nullable=False),
        sa.Column("dataset_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_impact_studies_project_id", "impact_studies", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_impact_studies_project_id", table_name="impact_studies")
    op.drop_table("impact_studies")

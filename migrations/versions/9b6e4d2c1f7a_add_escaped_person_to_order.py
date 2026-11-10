"""add escaped person to order

Revision ID: 9b6e4d2c1f7a
Revises: d089aab039ea
Create Date: 2026-11-10

"""
from alembic import op
import sqlalchemy as sa


revision = "9b6e4d2c1f7a"
down_revision = "d089aab039ea"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("order") as batch_op:
        batch_op.add_column(
            sa.Column("escaped_person_id", sa.Integer(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("order") as batch_op:
        batch_op.drop_column("escaped_person_id")

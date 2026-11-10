"""add order escaped person

Revision ID: c3d8f5a7b2e1
Revises: 9b6e4d2c1f7a
Create Date: 2026-11-10

"""
from alembic import op
import sqlalchemy as sa


revision = "c3d8f5a7b2e1"
down_revision = "9b6e4d2c1f7a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_escaped_person",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id",
            "person_id",
            name="uq_order_escaped_person",
        ),
    )
    op.execute(
        'INSERT INTO order_escaped_person (order_id, person_id) '
        'SELECT o.id, o.escaped_person_id FROM "order" o '
        "WHERE o.escaped_person_id IS NOT NULL"
    )


def downgrade():
    op.drop_table("order_escaped_person")

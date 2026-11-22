"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None

# SQL explícito. Evitar '%' y ':palabra' en el texto: SQLAlchemy los interpreta como parámetros.


def upgrade() -> None:
    op.execute("""
    """)


def downgrade() -> None:
    op.execute("""
    """)

"""client intake: client ids, saved upload reviews and per-row import results

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_id', sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f('ix_clients_external_id'), ['external_id'], unique=False)
    with op.batch_alter_table('csv_uploads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan', sa.JSON(), server_default='{}', nullable=False))
        batch_op.add_column(sa.Column('results', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('csv_uploads', schema=None) as batch_op:
        batch_op.drop_column('results')
        batch_op.drop_column('plan')
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clients_external_id'))
        batch_op.drop_column('external_id')

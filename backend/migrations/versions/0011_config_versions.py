"""versioned organization configuration

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-08 16:20:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('config_versions',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('org_id', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_by_email', sa.String(length=320), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'kind', 'key', 'version')
    )
    with op.batch_alter_table('config_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_config_versions_org_id'), ['org_id'], unique=False)
    with op.batch_alter_table('analysis_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('config', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('analysis_runs', schema=None) as batch_op:
        batch_op.drop_column('config')
    with op.batch_alter_table('config_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_config_versions_org_id'))
    op.drop_table('config_versions')

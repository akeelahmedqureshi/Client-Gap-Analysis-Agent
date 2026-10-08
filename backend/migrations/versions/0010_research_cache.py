"""research cache and upload domain checks

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08 15:10:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('research_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.String(length=40), nullable=False),
    sa.Column('url_hash', sa.String(length=64), nullable=False),
    sa.Column('url', sa.String(length=2000), nullable=False),
    sa.Column('final_url', sa.String(length=2000), nullable=False),
    sa.Column('status', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('rendered', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'url_hash')
    )
    with op.batch_alter_table('research_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_cache_fetched_at'), ['fetched_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_research_cache_org_id'), ['org_id'], unique=False)
    with op.batch_alter_table('csv_uploads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('domain_checks', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    with op.batch_alter_table('csv_uploads', schema=None) as batch_op:
        batch_op.drop_column('domain_checks')
    with op.batch_alter_table('research_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_cache_org_id'))
        batch_op.drop_index(batch_op.f('ix_research_cache_fetched_at'))
    op.drop_table('research_cache')

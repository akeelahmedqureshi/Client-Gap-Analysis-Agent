"""crm and marketing integrations

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09 09:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('integrations',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('org_id', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('encrypted_secret', sa.Text(), nullable=True),
    sa.Column('encrypted_config', sa.Text(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('auto_create', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'kind')
    )
    with op.batch_alter_table('integrations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_integrations_org_id'), ['org_id'], unique=False)
    op.create_table('integration_syncs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('org_id', sa.String(length=40), nullable=False),
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=False),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('external_ids', sa.JSON(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('document_version', sa.Integer(), nullable=True),
    sa.Column('automatic', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_by', sa.String(length=40), nullable=True),
    sa.Column('created_by_email', sa.String(length=320), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('integration_syncs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_integration_syncs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_integration_syncs_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_integration_syncs_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_integration_syncs_run_id'), ['run_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('integration_syncs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_integration_syncs_run_id'))
        batch_op.drop_index(batch_op.f('ix_integration_syncs_project_id'))
        batch_op.drop_index(batch_op.f('ix_integration_syncs_org_id'))
        batch_op.drop_index(batch_op.f('ix_integration_syncs_created_at'))
    op.drop_table('integration_syncs')
    with op.batch_alter_table('integrations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_integrations_org_id'))
    op.drop_table('integrations')

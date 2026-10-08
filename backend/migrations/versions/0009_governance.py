"""governance: org settings, job functions, notification preferences and notifications

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08 13:05:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('settings', sa.JSON(), nullable=False, server_default='{}'))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('job_function', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('notification_prefs', sa.JSON(), nullable=False, server_default='{}'))
    op.create_table('notifications',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('org_id', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('project_id', sa.String(length=40), nullable=True),
    sa.Column('run_id', sa.String(length=40), nullable=True),
    sa.Column('event', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('in_app', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('delivery', sa.JSON(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['analysis_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'run_id', 'event')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_run_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_org_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))
    op.drop_table('notifications')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('notification_prefs')
        batch_op.drop_column('job_function')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('settings')

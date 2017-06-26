"""empty message

Revision ID: 83c69e0d88dc
Revises: 5721833d4354
Create Date: 2017-06-23 10:20:02.476904

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '83c69e0d88dc'
down_revision = '5721833d4354'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('avatar_hash', sa.String(length=32), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'avatar_hash')
    # ### end Alembic commands ###
"""Alter risk and grounding scores to fixed precision NUMERIC(5, 4)

Revision ID: 0002_numeric_precision
Revises: 0001_initial_schema
Create Date: 2026-09-27 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_numeric_precision'
down_revision: Union[str, None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Round existing values and alter column types to NUMERIC(5, 4)
    op.execute("UPDATE decisions SET grounding_score = ROUND(grounding_score::numeric, 4) WHERE grounding_score IS NOT NULL")
    op.execute("UPDATE decisions SET policy_risk_score = ROUND(policy_risk_score::numeric, 4) WHERE policy_risk_score IS NOT NULL")
    op.execute("UPDATE decisions SET final_risk = ROUND(final_risk::numeric, 4) WHERE final_risk IS NOT NULL")
    op.execute("UPDATE policy_checks SET policy_risk_score = ROUND(policy_risk_score::numeric, 4) WHERE policy_risk_score IS NOT NULL")
    op.execute("UPDATE grounding_results SET grounding_score = ROUND(grounding_score::numeric, 4) WHERE grounding_score IS NOT NULL")

    op.alter_column('decisions', 'grounding_score', type_=sa.Numeric(5, 4), existing_type=sa.Numeric())
    op.alter_column('decisions', 'policy_risk_score', type_=sa.Numeric(5, 4), existing_type=sa.Numeric())
    op.alter_column('decisions', 'final_risk', type_=sa.Numeric(5, 4), existing_type=sa.Numeric())
    op.alter_column('policy_checks', 'policy_risk_score', type_=sa.Numeric(5, 4), existing_type=sa.Numeric())
    op.alter_column('grounding_results', 'grounding_score', type_=sa.Numeric(5, 4), existing_type=sa.Numeric())


def downgrade() -> None:
    op.alter_column('decisions', 'grounding_score', type_=sa.Numeric(), existing_type=sa.Numeric(5, 4))
    op.alter_column('decisions', 'policy_risk_score', type_=sa.Numeric(), existing_type=sa.Numeric(5, 4))
    op.alter_column('decisions', 'final_risk', type_=sa.Numeric(), existing_type=sa.Numeric(5, 4))
    op.alter_column('policy_checks', 'policy_risk_score', type_=sa.Numeric(), existing_type=sa.Numeric(5, 4))
    op.alter_column('grounding_results', 'grounding_score', type_=sa.Numeric(), existing_type=sa.Numeric(5, 4))

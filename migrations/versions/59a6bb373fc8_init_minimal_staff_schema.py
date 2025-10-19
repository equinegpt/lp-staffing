from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as psql

# revision identifiers
revision = "0001_minimal_staff"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Ensure gen_random_uuid() exists for server defaults
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # role
    op.create_table(
        "role",
        sa.Column("id", psql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(64), nullable=False),
    )

    # location
    op.create_table(
        "location",
        sa.Column("id", psql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Australia/Melbourne"),
    )

    # staff
    op.create_table(
        "staff",
        sa.Column("id", psql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("given_name", sa.String(80), nullable=False),
        sa.Column("family_name", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160)),
        sa.Column("mobile", sa.String(32), nullable=False, unique=True),
        sa.Column("email", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("home_location_id", psql.UUID(as_uuid=True), sa.ForeignKey("location.id")),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_staff_name", "staff", ["family_name", "given_name"])

    # staff_role_assignment
    op.create_table(
        "staff_role_assignment",
        sa.Column("id", psql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("staff_id", psql.UUID(as_uuid=True), sa.ForeignKey("staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", psql.UUID(as_uuid=True), sa.ForeignKey("role.id"), nullable=False),
        sa.Column("location_id", psql.UUID(as_uuid=True), sa.ForeignKey("location.id")),
        sa.Column("effective_start", sa.Date(), nullable=False),
        sa.Column("effective_end", sa.Date()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_sra_staff_dates", "staff_role_assignment", ["staff_id", "effective_start", "effective_end"])
    op.create_index("ix_sra_role", "staff_role_assignment", ["role_id"])
    op.create_index("ix_sra_location", "staff_role_assignment", ["location_id"])

    # Seed roles (server_default fills id)
    roles = [
        ("RIDER","Rider"),
        ("STRAPPER","Strapper"),
        ("MEDIA","Media"),
        ("TREADMILL","Treadmill"),
        ("WATERWALKER","Water Walkers"),
        ("FARRIER","Farrier"),
        ("VET","Vet"),
    ]
    for code,label in roles:
        op.execute(sa.text("INSERT INTO role (code, label) VALUES (:code, :label)").bindparams(code=code, label=label))

    # Seed a couple of locations
    locs = [
        ("BALLARAT","Ballarat","Australia/Melbourne"),
        ("CRANBOURNE","Cranbourne","Australia/Melbourne"),
    ]
    for code,name,tz in locs:
        op.execute(sa.text(
            "INSERT INTO location (code, name, timezone) VALUES (:code, :name, :tz)"
        ).bindparams(code=code, name=name, tz=tz))

def downgrade():
    op.drop_index("ix_sra_location", table_name="staff_role_assignment")
    op.drop_index("ix_sra_role", table_name="staff_role_assignment")
    op.drop_index("ix_sra_staff_dates", table_name="staff_role_assignment")
    op.drop_table("staff_role_assignment")
    op.drop_index("ix_staff_name", table_name="staff")
    op.drop_table("staff")
    op.drop_table("location")
    op.drop_table("role")

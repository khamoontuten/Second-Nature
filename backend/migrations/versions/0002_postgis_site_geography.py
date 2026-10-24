"""PostGIS geography column and spatial index for sites (PostgreSQL with PostGIS only).

On PostgreSQL the migration tries to enable the PostGIS extension. If PostGIS is not installed on the
server, the geography column is skipped (latitude/longitude columns remain authoritative) and a notice
is logged. On SQLite this migration is a no-op.

Revision ID: 0002
Revises: 0001
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def _postgis_available(bind) -> bool:
    avail = bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")).scalar()
    return bool(avail)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _postgis_available(bind):
        log.warning("PostGIS extension not available on this server: sites.geog not created (lat/lon columns used)")
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("ALTER TABLE sites ADD COLUMN IF NOT EXISTS geog geography(Point, 4326)")
    op.execute("UPDATE sites SET geog = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sites_geog ON sites USING GIST (geog)")
    op.execute("""
        CREATE OR REPLACE FUNCTION sites_set_geog() RETURNS trigger AS $$
        BEGIN NEW.geog := ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography; RETURN NEW; END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_sites_geog ON sites")
    op.execute("CREATE TRIGGER trg_sites_geog BEFORE INSERT OR UPDATE OF latitude, longitude ON sites "
               "FOR EACH ROW EXECUTE FUNCTION sites_set_geog()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_sites_geog ON sites")
    op.execute("DROP FUNCTION IF EXISTS sites_set_geog()")
    op.execute("DROP INDEX IF EXISTS ix_sites_geog")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS geog")

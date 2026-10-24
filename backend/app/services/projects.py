"""Project CRUD with immutable configuration versions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.database.models import BuildingGeometry, Project, ProjectConfigVersion, Site
from app.schemas.config import ProjectConfig, assumption_register
from app.simulation.scenarios import _merge


def _version_record(project: Project, cfg: ProjectConfig, version_no: int, note: str = "") -> ProjectConfigVersion:
    v = ProjectConfigVersion(project_id=project.id, version_no=version_no, config=cfg.model_dump(mode="json"),
                             config_hash=cfg.config_hash(), note=note)
    return v


def _geometry(v: ProjectConfigVersion, cfg: ProjectConfig) -> BuildingGeometry:
    b = cfg.building
    return BuildingGeometry(config_version_id=v.id, length_m=b.length_m, width_m=b.width_m, height_m=b.height_m,
                            floor_area_m2=b.floor_area_m2, details=b.model_dump(mode="json"))


def with_provenance_tags(cfg: ProjectConfig) -> ProjectConfig:
    """Persist default-assumption tags explicitly so they survive storage as a full JSON document."""
    tags = dict(cfg.sources)
    for row in assumption_register(cfg):
        tags.setdefault(row["path"], row["source"])
    return ProjectConfig(**{**cfg.model_dump(mode="python"), "sources": tags})


def _leaf_paths(patch: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in patch.items():
        if k in ("sources", "notes"):
            continue
        if isinstance(v, dict) and v and not k.endswith("_per_facade") and k not in ("capex", "maintenance_per_year",
                                                                                  "embodied_factors_kgco2e_per_kg"):
            out += _leaf_paths(v, f"{prefix}{k}.")
        else:
            out.append(f"{prefix}{k}")
    return out


def create_project(s: Session, cfg: ProjectConfig, *, is_demo: bool = False, geocode_source: str | None = None) -> Project:
    cfg = with_provenance_tags(cfg)
    p = Project(name=cfg.name, description=cfg.description, is_demo=is_demo, current_version_no=1)
    s.add(p)
    s.flush()
    site = cfg.site
    s.add(Site(project_id=p.id, name=site.name, country=site.country, address=site.address, latitude=site.latitude,
               longitude=site.longitude, elevation_m=site.elevation_m, timezone=site.timezone, site_area_m2=site.site_area_m2,
               geocode_source=geocode_source))
    v = _version_record(p, cfg, 1, "created")
    s.add(v)
    s.flush()
    s.add(_geometry(v, cfg))
    s.flush()
    return p


def get_project(s: Session, project_id: str) -> Project:
    p = s.get(Project, project_id)
    if p is None:
        raise NotFound(f"project {project_id} not found")
    return p


def current_version(s: Session, project: Project) -> ProjectConfigVersion:
    v = s.scalar(select(ProjectConfigVersion).where(ProjectConfigVersion.project_id == project.id,
                                                    ProjectConfigVersion.version_no == project.current_version_no))
    if v is None:
        raise NotFound(f"configuration version {project.current_version_no} of project {project.id} not found")
    return v


def current_config(s: Session, project: Project) -> ProjectConfig:
    return ProjectConfig(**current_version(s, project).config)


def update_project(s: Session, project: Project, patch: dict, note: str = "") -> ProjectConfigVersion:
    """Apply a partial update; a new immutable configuration version is created (old ones remain reproducible)."""
    cfg = current_config(s, project)
    merged = _merge(cfg.model_dump(mode="python"), patch)
    tags = dict(merged.get("sources") or {})
    for path in _leaf_paths(patch):
        if path not in (patch.get("sources") or {}):
            tags[path] = "user"   # fields the engineer edited are no longer default assumptions
    merged["sources"] = tags
    new_cfg = ProjectConfig(**merged)
    if new_cfg.config_hash() == cfg.config_hash():
        return current_version(s, project)
    project.current_version_no += 1
    project.name = new_cfg.name
    project.description = new_cfg.description
    v = _version_record(project, new_cfg, project.current_version_no, note or "updated")
    s.add(v)
    s.flush()
    s.add(_geometry(v, new_cfg))
    site = project.site
    if site is not None:
        for k in ("name", "country", "address", "latitude", "longitude", "elevation_m", "timezone", "site_area_m2"):
            setattr(site, k, getattr(new_cfg.site, k))
    s.flush()
    return v


def project_dict(s: Session, p: Project, include_config: bool = True) -> dict:
    d = {"id": p.id, "name": p.name, "description": p.description, "created_at": p.created_at.isoformat(),
         "updated_at": p.updated_at.isoformat() if p.updated_at else None, "current_version_no": p.current_version_no,
         "default_dataset_id": p.default_dataset_id, "is_demo": p.is_demo,
         "site": None if p.site is None else {"name": p.site.name, "latitude": p.site.latitude, "longitude": p.site.longitude,
                                              "elevation_m": p.site.elevation_m, "timezone": p.site.timezone, "country": p.site.country}}
    if include_config:
        v = current_version(s, p)
        cfg = ProjectConfig(**v.config)
        d["config"] = v.config
        d["config_hash"] = v.config_hash
        d["assumptions"] = assumption_register(cfg)
    return d

"""Siembra las configs estáticas de creators en la BD (idempotente)."""
import logging
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Creator
from .creators import ALL_CREATOR_CONFIGS

logger = logging.getLogger(__name__)


def seed_creators():
    db: Session = SessionLocal()
    try:
        for cfg in ALL_CREATOR_CONFIGS:
            existing = db.query(Creator).filter(Creator.slug == cfg["slug"]).first()
            if existing:
                # Actualizamos campos no destructivos
                existing.name = cfg["name"]
                existing.subtitle = cfg.get("subtitle", "")
                existing.avatar_initials = cfg.get("avatar_initials", "")
                existing.color_primary = cfg.get("color_primary", "#C72027")
                existing.color_secondary = cfg.get("color_secondary", "#D4B97D")
                existing.code_prefix = cfg.get("code_prefix", "V")
                existing.system_prompt = cfg["system_prompt"]
                existing.user_template = cfg["user_template"]
                existing.checklist_template = cfg.get("checklist_template", [])
                existing.config = cfg.get("config", {})
                logger.info(f"Creator actualizado: {cfg['slug']}")
            else:
                db.add(Creator(
                    slug=cfg["slug"],
                    name=cfg["name"],
                    subtitle=cfg.get("subtitle", ""),
                    avatar_initials=cfg.get("avatar_initials", ""),
                    color_primary=cfg.get("color_primary", "#C72027"),
                    color_secondary=cfg.get("color_secondary", "#D4B97D"),
                    code_prefix=cfg.get("code_prefix", "V"),
                    system_prompt=cfg["system_prompt"],
                    user_template=cfg["user_template"],
                    checklist_template=cfg.get("checklist_template", []),
                    config=cfg.get("config", {}),
                ))
                logger.info(f"Creator creado: {cfg['slug']}")
        db.commit()
    finally:
        db.close()

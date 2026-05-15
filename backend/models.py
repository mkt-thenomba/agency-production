"""Modelos multi-tenant: Creator → Video → ChecklistItem."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, JSON, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from .database import Base


class Creator(Base):
    """Cada creator (Marcelo Gullo, Raíces de Europa, etc.) es un tenant."""
    __tablename__ = "creators"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    subtitle = Column(String, default="")
    avatar_initials = Column(String, default="")     # ej. "MG", "RE"
    color_primary = Column(String, default="#C72027")
    color_secondary = Column(String, default="#D4B97D")

    system_prompt = Column(Text, nullable=False)
    user_template = Column(Text, nullable=False)
    code_prefix = Column(String, default="V")          # V##, RE##, etc.

    # Config flexible (libros, thumb templates, type_keywords, publishing schedule…)
    config = Column(JSON, default=dict)
    checklist_template = Column(JSON, default=list)    # lista de {key, phase, label}

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    videos = relationship("Video", back_populates="creator", cascade="all, delete-orphan")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("creator_id", "code", name="uq_creator_code"),
        Index("ix_videos_creator_created", "creator_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    creator_id = Column(Integer, ForeignKey("creators.id"), nullable=False)
    code = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    type = Column(String, default="historia")
    duration = Column(String, default="—")             # MM:SS si lo derivamos de los timestamps
    status = Column(String, default="processing")     # processing / done / error

    # Input
    transcript = Column(Text, default="")
    transcript_source = Column(String, default="text")  # text | assemblyai-json | srt

    # Output crudo y artefactos
    paquete_json = Column(JSON)
    paquete_md = Column(Text)
    descripcion_txt = Column(Text)
    cortes_csv = Column(Text)
    miniatura_txt = Column(Text)

    error_message = Column(Text)
    suggested_publish_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("Creator", back_populates="videos")
    checklist = relationship(
        "ChecklistItem", back_populates="video", cascade="all, delete-orphan",
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    __table_args__ = (UniqueConstraint("video_id", "item_key", name="uq_video_item"),)

    id = Column(Integer, primary_key=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False)
    item_key = Column(String, nullable=False)
    done = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    video = relationship("Video", back_populates="checklist")

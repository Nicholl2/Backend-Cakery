from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.sql import func

from app.core.database import Base


class StoreCustomization(Base):
    """Singleton public homepage content managed by the store owner."""

    __tablename__ = "store_customization"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_store_customization_singleton"),
    )

    id = Column(Integer, primary_key=True)
    hero_title_id = Column(Text, nullable=True)
    hero_title_en = Column(Text, nullable=True)
    hero_subtitle_id = Column(Text, nullable=True)
    hero_subtitle_en = Column(Text, nullable=True)
    hero_image_url = Column(Text, nullable=True)
    about_title_id = Column(Text, nullable=True)
    about_title_en = Column(Text, nullable=True)
    about_description_id = Column(Text, nullable=True)
    about_description_en = Column(Text, nullable=True)
    store_address = Column(Text, nullable=True)
    maps_embed_url = Column(Text, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

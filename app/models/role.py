from sqlalchemy import Column, Integer, String
from app.core.database import Base


class Role(Base):
    """User roles with hierarchical levels"""
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    nama_role = Column(String(50), nullable=False)
    level = Column(Integer, nullable=False, unique=True)  # 1: Owner, 2: Admin, 3: Staff

    def __repr__(self):
        return f"<Role(id={self.id}, nama_role={self.nama_role}, level={self.level})>"
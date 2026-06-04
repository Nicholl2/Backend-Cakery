from sqlalchemy import Column, Integer, String, Numeric
from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    nama_produk = Column(String(100))
    harga_jual = Column(Numeric(10,2))

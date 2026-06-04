from sqlalchemy import Column, Integer, ForeignKey, Numeric
from app.core.database import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    stock_item_id = Column(Integer, ForeignKey("stock_items.id"))
    jumlah_dibutuhkan = Column(Numeric(10,2))

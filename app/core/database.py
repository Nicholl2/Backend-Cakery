from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# Menggunakan create_async_engine untuk mendukung asyncpg & lifespan main.py
engine = create_async_engine(settings.database_url, pool_pre_ping=True)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

# Dependency injection generator untuk route FastAPI
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
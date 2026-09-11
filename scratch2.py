import asyncio
from sqlalchemy import Column, Integer, Boolean, String, select
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    is_active = Column(Boolean, default=True)
    phone_number = Column(String(20))

async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        u1 = User(is_active=True, phone_number="111")
        u2 = User(is_active=False, phone_number="222")
        db.add_all([u1, u2])
        await db.commit()
        
        stmt = select(User).where(User.is_active == True)
        res = await db.execute(stmt)
        users = res.scalars().all()
        print([u.phone_number for u in users])

asyncio.run(main())

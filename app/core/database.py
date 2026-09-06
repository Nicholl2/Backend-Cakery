import logging
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import select
from app.core.config import settings

logger = logging.getLogger(__name__)

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


# ── SEED INITIAL MASTER DATA ──────────────────────────────────────────────────

async def ensure_role(db: AsyncSession, nama_role: str, level: int):
    from app.models.role import Role
    result = await db.execute(select(Role).where(Role.level == level))
    role = result.scalars().first()
    if role:
        role.nama_role = nama_role
        return role

    role = Role(nama_role=nama_role, level=level)
    db.add(role)
    await db.flush()
    return role


async def ensure_user(
    db: AsyncSession,
    username: str,
    password_plain: str,
    role_id: int,
    nomor_wa_admin: str = None,
    handles_takeover: bool = False,
    email: str = None,
    phone_number: str = None,
):
    from app.core.security import hash_password
    from app.models.user import User
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    if user:
        user.password_hash = hash_password(password_plain)
        user.role_id = role_id
        user.nomor_wa_admin = nomor_wa_admin
        user.handles_takeover = handles_takeover
        user.is_active = True
        if email is not None:
            user.email = email
        if phone_number is not None:
            user.phone_number = phone_number
        return user

    user = User(
        username=username,
        password_hash=hash_password(password_plain),
        role_id=role_id,
        nomor_wa_admin=nomor_wa_admin,
        handles_takeover=handles_takeover,
        is_active=True,
        email=email,
        phone_number=phone_number,
    )
    db.add(user)
    await db.flush()
    return user


async def ensure_buyer(
    db: AsyncSession,
    name: str,
    email: str,
    phone: str,
    password_plain: str,
    role_id: int = None,
):
    from app.core.security import get_password_hash
    from app.models.buyer import Buyer
    from app.models.user import User

    hashed_pw = get_password_hash(password_plain)

    # 1. Ensure record in buyers table
    result = await db.execute(select(Buyer).where(Buyer.email == email))
    buyer = result.scalars().first()
    if buyer:
        buyer.name = name
        buyer.phone = phone
        buyer.password_hash = hashed_pw
        buyer.is_verified = True
        buyer.is_active = True
    else:
        buyer = Buyer(
            name=name,
            email=email,
            phone=phone,
            password_hash=hashed_pw,
            is_verified=True,
            is_active=True,
        )
        db.add(buyer)

    # 2. Also ensure record in users table linked with BUYER role if role_id provided
    if role_id is not None:
        user_res = await db.execute(select(User).where((User.username == email) | (User.email == email)))
        user = user_res.scalars().first()
        if user:
            user.password_hash = hashed_pw
            user.role_id = role_id
            user.email = email
            user.phone_number = phone
            user.is_active = True
        else:
            user = User(
                username=email,
                password_hash=hashed_pw,
                role_id=role_id,
                email=email,
                phone_number=phone,
                is_active=True,
            )
            db.add(user)

    await db.flush()
    return buyer


async def ensure_supplier(
    db: AsyncSession,
    nama_supplier: str,
    kontak_person: str,
    email: str,
    nomor_telepon: str,
    alamat: str,
    kota: str,
):
    from app.models.purchasing import Supplier
    result = await db.execute(select(Supplier).where(Supplier.nama_supplier == nama_supplier))
    supplier = result.scalars().first()
    if supplier:
        return supplier

    supplier = Supplier(
        nama_supplier=nama_supplier,
        kontak_person=kontak_person,
        email=email,
        nomor_telepon=nomor_telepon,
        alamat=alamat,
        kota=kota,
        is_active=True,
    )
    db.add(supplier)
    await db.flush()
    return supplier


async def ensure_stock_item(
    db: AsyncSession,
    nama_item: str,
    satuan,
    kategori,
    harga_per_satuan: Decimal,
    stok_tersedia: Decimal,
    alert_min_stok: Decimal,
    supplier_id: int,
    user_id: int,
):
    from app.models.stock_item import StockItem
    result = await db.execute(select(StockItem).where(StockItem.nama_item == nama_item))
    item = result.scalars().first()
    if item:
        return item

    item = StockItem(
        nama_item=nama_item,
        satuan=satuan,
        kategori=kategori,
        harga_per_satuan=harga_per_satuan,
        stok_tersedia=stok_tersedia,
        alert_min_stok=alert_min_stok,
        supplier_id=supplier_id,
        last_updated_by=user_id,
    )
    db.add(item)
    await db.flush()
    return item


async def ensure_product(
    db: AsyncSession,
    nama_produk: str,
    deskripsi: str,
    kategori: str,
    harga_jual: Decimal,
    slug: str,
    minimum_order: int = 1,
    is_featured: bool = False,
):
    from app.models.product import Product
    result = await db.execute(select(Product).where(Product.nama_produk == nama_produk))
    product = result.scalars().first()
    if product:
        return product

    product = Product(
        nama_produk=nama_produk,
        deskripsi=deskripsi,
        kategori=kategori,
        harga_jual=harga_jual,
        hpp_total=Decimal("0.00"),
        slug=slug,
        is_active=True,
        is_featured=is_featured,
        minimum_order=minimum_order,
    )
    db.add(product)
    await db.flush()
    return product


async def ensure_recipe(
    db: AsyncSession,
    product_id: int,
    stock_item_id: int,
    jumlah_dibutuhkan: Decimal,
):
    from app.models.recipe import Recipe
    result = await db.execute(
        select(Recipe).where(
            Recipe.product_id == product_id,
            Recipe.stock_item_id == stock_item_id,
        )
    )
    recipe = result.scalars().first()
    if recipe:
        return recipe

    recipe = Recipe(
        product_id=product_id,
        stock_item_id=stock_item_id,
        jumlah_dibutuhkan=jumlah_dibutuhkan,
        quantity_required=jumlah_dibutuhkan,
    )
    db.add(recipe)
    await db.flush()
    return recipe


async def ensure_faq(
    db: AsyncSession,
    pertanyaan: str,
    jawaban: str,
    created_by: int,
):
    from app.models.faq_item import FaqItem
    result = await db.execute(select(FaqItem).where(FaqItem.pertanyaan == pertanyaan))
    faq = result.scalars().first()
    if faq:
        return faq

    faq = FaqItem(
        pertanyaan=pertanyaan,
        jawaban=jawaban,
        created_by=created_by,
        is_active=True,
    )
    db.add(faq)
    await db.flush()
    return faq


async def seed_initial_data(db: AsyncSession) -> None:
    """
    Seed initial master data to database if empty.
    Ensures roles, default admin/seller/buyer accounts, suppliers, stock items, products, recipes, and FAQs.
    """
    from app.models.stock_item import SatuanEnum, KategoriEnum

    logger.info("Checking & seeding initial database data...")

    # 1. Ensure Roles (Owner=1, Admin=2, Staff/Seller=3, Buyer=4)
    owner_role = await ensure_role(db, "Owner", 1)
    admin_role = await ensure_role(db, "Admin", 2)
    seller_role = await ensure_role(db, "Staff", 3)
    buyer_role = await ensure_role(db, "Buyer", 4)

    # 2. Default Internal Users (Admin / Owner / Seller)
    owner_user = await ensure_user(
        db,
        username="imeng",
        password_plain="Admin_123",
        role_id=owner_role.id,
        nomor_wa_admin="08111111111",
        handles_takeover=True,
        email="imeng@toticakery.com",
        phone_number="08111111111",
    )
    admin_user = await ensure_user(
        db,
        username="ameng",
        password_plain="Admin_123",
        role_id=admin_role.id,
        nomor_wa_admin="08222222222",
        handles_takeover=True,
        email="ameng@toticakery.com",
        phone_number="08222222222",
    )
    seller_user = await ensure_user(
        db,
        username="smeng",
        password_plain="Staff_123",
        role_id=seller_role.id,
        nomor_wa_admin="08333333333",
        handles_takeover=False,
        email="smeng@toticakery.com",
        phone_number="08333333333",
    )

    # 3. Default Buyer Account
    buyer_user = await ensure_buyer(
        db,
        name="Aceng",
        email="aceng@gmail.com",
        phone="08123456789",
        password_plain="Aceng_123",
        role_id=buyer_role.id,
    )

    # 4. Default Suppliers
    sup_bahan = await ensure_supplier(
        db,
        nama_supplier="PT Sukses Bahan Kue",
        kontak_person="Budi Santoso",
        email="budi@suksesbahan.com",
        nomor_telepon="081288889999",
        alamat="Jl. Industri Pangan No. 10",
        kota="Jakarta Barat",
    )
    sup_kemasan = await ensure_supplier(
        db,
        nama_supplier="CV Kemasan Cantik",
        kontak_person="Dewi Lestari",
        email="dewi@kemasancantik.com",
        nomor_telepon="081377776666",
        alamat="Jl. Percetakan No. 45",
        kota="Tangerang",
    )

    # 5. Default Stock Items
    tepung = await ensure_stock_item(
        db,
        nama_item="Tepung Terigu Protein Sedang",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("15.0000"),
        stok_tersedia=Decimal("50000.00"),
        alert_min_stok=Decimal("5000.00"),
        supplier_id=sup_bahan.id,
        user_id=admin_user.id,
    )
    gula = await ensure_stock_item(
        db,
        nama_item="Gula Pasir",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("18.0000"),
        stok_tersedia=Decimal("30000.00"),
        alert_min_stok=Decimal("3000.00"),
        supplier_id=sup_bahan.id,
        user_id=admin_user.id,
    )
    butter = await ensure_stock_item(
        db,
        nama_item="Mentega Wisman",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("120.0000"),
        stok_tersedia=Decimal("10000.00"),
        alert_min_stok=Decimal("1000.00"),
        supplier_id=sup_bahan.id,
        user_id=admin_user.id,
    )
    telur = await ensure_stock_item(
        db,
        nama_item="Telur Ayam",
        satuan=SatuanEnum.pcs,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("2000.0000"),
        stok_tersedia=Decimal("500.00"),
        alert_min_stok=Decimal("50.00"),
        supplier_id=sup_bahan.id,
        user_id=admin_user.id,
    )
    box_kue = await ensure_stock_item(
        db,
        nama_item="Box Kue Eksklusif 20x20",
        satuan=SatuanEnum.pcs,
        kategori=KategoriEnum.kemasan,
        harga_per_satuan=Decimal("5000.0000"),
        stok_tersedia=Decimal("200.00"),
        alert_min_stok=Decimal("20.00"),
        supplier_id=sup_kemasan.id,
        user_id=admin_user.id,
    )

    # 6. Default Products & Recipes
    prod_lapis = await ensure_product(
        db,
        nama_produk="Lapis Legit Premium",
        deskripsi="Lapis legit lembut dibuat dengan butter wisman berkualitas premium",
        kategori="Kue Basah",
        harga_jual=Decimal("250000.00"),
        slug="lapis-legit-premium",
        minimum_order=1,
        is_featured=True,
    )
    await ensure_recipe(db, prod_lapis.id, butter.id, Decimal("500.0000"))
    await ensure_recipe(db, prod_lapis.id, telur.id, Decimal("20.0000"))
    await ensure_recipe(db, prod_lapis.id, gula.id, Decimal("250.0000"))
    await ensure_recipe(db, prod_lapis.id, tepung.id, Decimal("100.0000"))
    await ensure_recipe(db, prod_lapis.id, box_kue.id, Decimal("1.0000"))

    prod_lapis.hpp_total = (
        Decimal("500") * Decimal("120")
        + Decimal("20") * Decimal("2000")
        + Decimal("250") * Decimal("18")
        + Decimal("100") * Decimal("15")
        + Decimal("1") * Decimal("5000")
    )

    prod_chiffon = await ensure_product(
        db,
        nama_produk="Chiffon Cake Pandan",
        deskripsi="Chiffon cake pandan wangi alami, lembut dan empuk",
        kategori="Chiffon",
        harga_jual=Decimal("85000.00"),
        slug="chiffon-cake-pandan",
        minimum_order=1,
        is_featured=True,
    )
    await ensure_recipe(db, prod_chiffon.id, telur.id, Decimal("6.0000"))
    await ensure_recipe(db, prod_chiffon.id, tepung.id, Decimal("150.0000"))
    await ensure_recipe(db, prod_chiffon.id, gula.id, Decimal("120.0000"))
    prod_chiffon.hpp_total = (
        Decimal("6") * Decimal("2000")
        + Decimal("150") * Decimal("15")
        + Decimal("120") * Decimal("18")
    )

    prod_brownies = await ensure_product(
        db,
        nama_produk="Brownies Fudgy Almond",
        deskripsi="Brownies cokelat panggang fudgy dengan limpahan taburan almond",
        kategori="Brownies",
        harga_jual=Decimal("95000.00"),
        slug="brownies-fudgy-almond",
        minimum_order=1,
        is_featured=True,
    )
    await ensure_recipe(db, prod_brownies.id, tepung.id, Decimal("120.0000"))
    await ensure_recipe(db, prod_brownies.id, gula.id, Decimal("150.0000"))
    await ensure_recipe(db, prod_brownies.id, telur.id, Decimal("3.0000"))
    prod_brownies.hpp_total = (
        Decimal("120") * Decimal("15")
        + Decimal("150") * Decimal("18")
        + Decimal("3") * Decimal("2000")
    )

    # 7. Default FAQ Items
    await ensure_faq(
        db,
        pertanyaan="Berapa lama daya tahan kue Toti Cakery?",
        jawaban="Kue kami tahan 3-4 hari di suhu ruang dan hingga 10-14 hari jika disimpan di dalam lemari es / chiller tertutup rapat.",
        created_by=admin_user.id,
    )
    await ensure_faq(
        db,
        pertanyaan="Bagaimana cara melakukan pembayaran?",
        jawaban="Pembayaran dapat dilakukan melalui Transfer Bank BCA Virtual Account dan QRIS otomatis via Midtrans terintegrasi.",
        created_by=admin_user.id,
    )
    await ensure_faq(
        db,
        pertanyaan="Apakah melayani pengiriman ke luar kota?",
        jawaban="Untuk saat ini kami melayani pengiriman instan/sameday di area dalam kota serta opsi pickup di outlet kami.",
        created_by=admin_user.id,
    )

    await db.commit()
    logger.info("Database initial seeding completed successfully.")
"""Add owner-managed public homepage customization.

Revision ID: 20261030_01
Revises: 20261027_01
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = "20261030_01"
down_revision = "20261027_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_customization",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hero_title_id", sa.Text(), nullable=True),
        sa.Column("hero_title_en", sa.Text(), nullable=True),
        sa.Column("hero_subtitle_id", sa.Text(), nullable=True),
        sa.Column("hero_subtitle_en", sa.Text(), nullable=True),
        sa.Column("hero_image_url", sa.Text(), nullable=True),
        sa.Column("about_title_id", sa.Text(), nullable=True),
        sa.Column("about_title_en", sa.Text(), nullable=True),
        sa.Column("about_description_id", sa.Text(), nullable=True),
        sa.Column("about_description_en", sa.Text(), nullable=True),
        sa.Column("store_address", sa.Text(), nullable=True),
        sa.Column("maps_embed_url", sa.Text(), nullable=True),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_store_customization_singleton"),
    )

    store_customization = sa.table(
        "store_customization",
        sa.column("id", sa.Integer()),
        sa.column("hero_title_id", sa.Text()),
        sa.column("hero_title_en", sa.Text()),
        sa.column("hero_subtitle_id", sa.Text()),
        sa.column("hero_subtitle_en", sa.Text()),
        sa.column("hero_image_url", sa.Text()),
        sa.column("about_title_id", sa.Text()),
        sa.column("about_title_en", sa.Text()),
        sa.column("about_description_id", sa.Text()),
        sa.column("about_description_en", sa.Text()),
        sa.column("store_address", sa.Text()),
        sa.column("maps_embed_url", sa.Text()),
    )
    op.bulk_insert(
        store_customization,
        [
            {
                "id": 1,
                "hero_title_id": "Kue Lezat,\nMomen Berkesan",
                "hero_title_en": "Delicious Cakes,\nMemorable Moments",
                "hero_subtitle_id": "Toti Cakery hadir dengan kue berkualitas, dibuat dari bahan pilihan untuk setiap momen spesial Anda.",
                "hero_subtitle_en": "Toti Cakery is here with quality cakes, made from selected ingredients for your every special moment.",
                "hero_image_url": "https://res.cloudinary.com/mrje22up/image/upload/v1790318553/SaveClip.App_618505350_17958279306045799_2574558123171612005_n.jpg",
                "about_title_id": "Tentang Toti Cakery",
                "about_title_en": "About Toti Cakery",
                "about_description_id": "Toti Cakery adalah toko kue rumahan yang berdedikasi menghadirkan kue lezat dengan cita rasa istimewa. Setiap kue kami dibuat dengan penuh cinta dan perhatian pada detail,menggunakan bahan berkualitas terbaik.",
                "about_description_en": "Toti Cakery is a home bakery dedicated to presenting delicious cakes with a special taste. Every cake are made with love and attention to detail,using the best quality ingredients.",
                # The existing homepage embeds a map but does not store a textual address.
                "store_address": None,
                "maps_embed_url": "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3989.157534596183!2d103.95726197941448!3d1.0427912962325454!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x31d98da3b87ec2f7%3A0x280a84339839dd69!2sToti%20Cakery!5e0!3m2!1sen!2sid!4v1782278391706!5m2!1sen!2sid",
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("store_customization")

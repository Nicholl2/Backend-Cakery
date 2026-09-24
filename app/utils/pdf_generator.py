import io
from datetime import datetime
from decimal import Decimal
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether,
)

from app.models.order import Order


def format_rupiah(amount: Optional[Decimal | float | int | str]) -> str:
    """Format angka desimal/int menjadi representasi mata uang Rupiah."""
    if amount is None:
        return "Rp 0"
    try:
        val = Decimal(str(amount))
        formatted = f"{val:,.0f}".replace(",", ".")
        return f"Rp {formatted}"
    except Exception:
        return f"Rp {amount}"


def format_datetime(dt: Optional[datetime | str]) -> str:
    """Format datetime ke string tanggal Indonesia."""
    if not dt:
        return "-"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return dt
    return dt.strftime("%d %b %Y, %H:%M")


def get_payment_badge_color(status_str: str) -> tuple[colors.Color, colors.Color]:
    """Mengembalikan tuple (background_color, text_color) untuk badge status pembayaran."""
    st = (status_str or "").lower()
    if st in ("paid", "lunas", "success", "settlement"):
        return colors.HexColor("#DCFCE7"), colors.HexColor("#166534")  # Emerald/Green
    elif st in ("partial", "dp"):
        return colors.HexColor("#FEF3C7"), colors.HexColor("#92400E")  # Amber/Yellow
    elif st in ("refunded",):
        return colors.HexColor("#F3F4F6"), colors.HexColor("#374151")  # Gray
    else:  # unpaid, pending, etc.
        return colors.HexColor("#FEE2E2"), colors.HexColor("#991B1B")  # Rose/Red


def generate_order_invoice_pdf(order: Order, store_whatsapp: str = "") -> io.BytesIO:
    """
    Menghasilkan file PDF Invoice pesanan Toti Cakery dalam format A4.
    Mengembalikan objek io.BytesIO yang berisi byte PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Custom colors & styles
    primary_color = colors.HexColor("#5C2C16")    # Warm mocha / deep bakery brown
    secondary_color = colors.HexColor("#8C4A2F")  # Terracotta / caramel
    dark_text = colors.HexColor("#1F2937")        # Charcoal
    muted_text = colors.HexColor("#6B7280")       # Muted gray

    brand_title_style = ParagraphStyle(
        "BrandTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=primary_color,
    )

    brand_subtitle_style = ParagraphStyle(
        "BrandSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=secondary_color,
    )

    invoice_header_style = ParagraphStyle(
        "InvoiceHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        alignment=2,  # Right aligned
        textColor=primary_color,
    )

    invoice_sub_style = ParagraphStyle(
        "InvoiceSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        alignment=2,  # Right aligned
        textColor=dark_text,
    )

    section_heading_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=primary_color,
    )

    card_label_style = ParagraphStyle(
        "CardLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=muted_text,
    )

    card_val_style = ParagraphStyle(
        "CardVal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=dark_text,
    )

    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.white,
    )

    table_header_right_style = ParagraphStyle(
        "TableHeaderRight",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        alignment=2,
        textColor=colors.white,
    )

    table_cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=dark_text,
    )

    table_cell_bold_style = ParagraphStyle(
        "TableCellBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=dark_text,
    )

    table_cell_right_style = ParagraphStyle(
        "TableCellRight",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        alignment=2,
        textColor=dark_text,
    )

    table_cell_center_style = ParagraphStyle(
        "TableCellCenter",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        alignment=1,
        textColor=dark_text,
    )

    footer_style = ParagraphStyle(
        "FooterText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        alignment=1,
        textColor=muted_text,
    )

    story = []

    # ── 1. HEADER SECTION (Brand & Invoice Metadata) ─────────────────────────
    invoice_num = order.invoice.nomor_invoice if order.invoice else f"INV-{order.id:05d}"
    order_date = format_datetime(order.created_at)

    raw_status = (order.invoice.status.value if (order.invoice and hasattr(order.invoice.status, "value"))
                  else (order.invoice.status if order.invoice else "unpaid"))
    status_label_map = {
        "paid": "LUNAS / SETTLEMENT",
        "partial": "DP / SEBAGIAN",
        "unpaid": "BELUM LUNAS",
        "refunded": "REFUNDED",
    }
    status_display = status_label_map.get(str(raw_status).lower(), str(raw_status).upper())

    badge_bg, badge_fg = get_payment_badge_color(str(raw_status))

    header_left = [
        Paragraph("TOTI CAKERY", brand_title_style),
        Spacer(1, 2),
        Paragraph("Artisan Bakery, Cakes & Pastries", brand_subtitle_style),
        Paragraph("Freshly Baked Every Day &bull; Made with Quality Ingredients", brand_subtitle_style),
    ]

    badge_style = ParagraphStyle(
        "StatusBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        alignment=2,
        textColor=badge_fg,
    )

    header_right = [
        Paragraph("INVOICE", invoice_header_style),
        Spacer(1, 2),
        Paragraph(f"<b>No:</b> {invoice_num}", invoice_sub_style),
        Paragraph(f"<b>Tanggal:</b> {order_date}", invoice_sub_style),
        Spacer(1, 4),
        Paragraph(f"Status: <b>{status_display}</b>", badge_style),
    ]

    header_table = Table(
        [[header_left, header_right]],
        colWidths=[280, 243],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    # Divider line
    story.append(HRFlowable(
        width="100%",
        thickness=2,
        color=primary_color,
        spaceBefore=2,
        spaceAfter=12,
    ))

    # ── 2. INFO CARDS (Customer Details & Order Details) ─────────────────────
    cust_nama = order.customer.nama if order.customer else "Pelanggan Umum"
    cust_wa = order.customer.nomor_wa if order.customer else "-"
    cust_alamat = (order.customer.alamat or "-") if order.customer else "-"

    metode_kirim = (order.metode_pengiriman.value if hasattr(order.metode_pengiriman, "value")
                    else str(order.metode_pengiriman)).capitalize()
    order_status_str = (order.status.value if hasattr(order.status, "value") else str(order.status)).replace("_", " ").upper()
    due_date_str = format_datetime(order.due_date) if order.due_date else "-"
    pref_bayar = order.payment_method_preference or "-"

    cust_info = [
        Paragraph("INFORMASI PELANGGAN", section_heading_style),
        Spacer(1, 4),
        Paragraph("Nama Pelanggan", card_label_style),
        Paragraph(cust_nama, card_val_style),
        Spacer(1, 3),
        Paragraph("Nomor WhatsApp / Kontak", card_label_style),
        Paragraph(cust_wa, card_val_style),
        Spacer(1, 3),
        Paragraph("Alamat Pengiriman", card_label_style),
        Paragraph(cust_alamat, card_val_style),
    ]

    order_info = [
        Paragraph("DETAIL PESANAN", section_heading_style),
        Spacer(1, 4),
        Paragraph("ID Pesanan / Order ID", card_label_style),
        Paragraph(f"#{order.id}", card_val_style),
        Spacer(1, 3),
        Paragraph("Metode Pengiriman", card_label_style),
        Paragraph(metode_kirim, card_val_style),
        Spacer(1, 3),
        Paragraph("Estimasi Selesai / Kirim", card_label_style),
        Paragraph(due_date_str, card_val_style),
        Spacer(1, 3),
        Paragraph("Status Pesanan", card_label_style),
        Paragraph(order_status_str, card_val_style),
    ]

    card_bg = colors.HexColor("#FAF7F4")
    card_border = colors.HexColor("#E5DFD9")

    info_table = Table(
        [[cust_info, "", order_info]],
        colWidths=[251, 21, 251],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, 0), card_bg),
        ("BACKGROUND", (2, 0), (2, 0), card_bg),
        ("BOX", (0, 0), (0, 0), 1, card_border),
        ("BOX", (2, 0), (2, 0), 1, card_border),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    # ── 3. ITEM TABLE (Product Breakdown) ────────────────────────────────────
    story.append(Paragraph("RINCIAN PESANAN", section_heading_style))
    story.append(Spacer(1, 6))

    items_header = [
        Paragraph("No", table_header_style),
        Paragraph("Nama Produk / Item", table_header_style),
        Paragraph("Qty", table_header_style),
        Paragraph("Harga Satuan", table_header_right_style),
        Paragraph("Subtotal", table_header_right_style),
    ]

    items_data = [items_header]
    total_custom_charges = Decimal("0.00")

    order_items = getattr(order, "order_items", []) or []
    for idx, item in enumerate(order_items, start=1):
        item_name = (
            getattr(item, "product_name", None)
            or (item.product.nama_produk if getattr(item, "product", None) else None)
            or item.custom_product_name
            or f"Item #{idx}"
        )

        subtotal_val = Decimal(str(item.subtotal))
        qty = item.jumlah
        custom_charge = Decimal(str(getattr(item, "custom_decoration_charge", 0) or 0))
        total_custom_charges += custom_charge

        unit_price = (subtotal_val - custom_charge) / qty if qty > 0 else subtotal_val

        desc_cell = [Paragraph(f"<b>{item_name}</b>", table_cell_style)]
        if custom_charge > 0:
            desc_cell.append(
                Paragraph(f"<font color='#8C4A2F'><i>+ Biaya Custom/Dekorasi: {format_rupiah(custom_charge)}</i></font>", table_cell_style)
            )

        items_data.append([
            Paragraph(str(idx), table_cell_center_style),
            desc_cell,
            Paragraph(f"{qty}x", table_cell_center_style),
            Paragraph(format_rupiah(unit_price), table_cell_right_style),
            Paragraph(format_rupiah(subtotal_val), table_cell_right_style),
        ])

    if not order_items:
        items_data.append([
            Paragraph("1", table_cell_center_style),
            Paragraph("Pesanan Toti Cakery", table_cell_style),
            Paragraph("1x", table_cell_center_style),
            Paragraph(format_rupiah(order.total_harga_pesanan), table_cell_right_style),
            Paragraph(format_rupiah(order.total_harga_pesanan), table_cell_right_style),
        ])

    col_widths = [28, 255, 45, 95, 100]
    items_table = Table(items_data, colWidths=col_widths)

    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), primary_color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5DFD9")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E5DFD9")),
    ]

    for r in range(1, len(items_data)):
        if r % 2 == 0:
            t_style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#FAF8F5")))
        else:
            t_style.append(("BACKGROUND", (0, r), (-1, r), colors.white))

    items_table.setStyle(TableStyle(t_style))
    story.append(items_table)
    story.append(Spacer(1, 10))

    # ── 4. TOTALS & SUMMARY SECTION ──────────────────────────────────────────
    total_tagihan = (
        Decimal(str(order.invoice.total_tagihan)) if order.invoice
        else Decimal(str(order.total_harga_pesanan))
    )

    amount_paid = getattr(order, "amount_paid", None)
    if amount_paid is None:
        amount_paid = Decimal("0.00")
        if order.invoice and hasattr(order.invoice, "payments"):
            for p in order.invoice.payments:
                if str(getattr(p, "payment_status", "")).lower() == "success":
                    amount_paid += Decimal(str(p.jumlah_bayar))
    else:
        amount_paid = Decimal(str(amount_paid))

    amount_due = getattr(order, "amount_due", None)
    if amount_due is None:
        amount_due = max(Decimal("0.00"), total_tagihan - amount_paid)
    else:
        amount_due = Decimal(str(amount_due))

    notes_block = []
    if order.notes:
        notes_block = [
            Paragraph("<b>Catatan Khusus:</b>", card_label_style),
            Spacer(1, 2),
            Paragraph(order.notes, table_cell_style),
        ]
    elif pref_bayar != "-":
        notes_block = [
            Paragraph("<b>Preferensi Bayar:</b>", card_label_style),
            Spacer(1, 2),
            Paragraph(pref_bayar, table_cell_style),
        ]

    due_color = colors.HexColor("#991B1B") if amount_due > 0 else colors.HexColor("#166534")

    total_rows = [
        [
            notes_block,
            Paragraph("Total Tagihan", table_cell_bold_style),
            Paragraph(format_rupiah(total_tagihan), table_cell_right_style),
        ],
        [
            "",
            Paragraph("Sudah Dibayar", table_cell_bold_style),
            Paragraph(f"<font color='#166534'><b>{format_rupiah(amount_paid)}</b></font>", table_cell_right_style),
        ],
        [
            "",
            Paragraph("Sisa Tagihan", ParagraphStyle("DueStyle", parent=table_cell_bold_style, textColor=due_color, fontSize=9.5)),
            Paragraph(f"<font color='{due_color.hexval()}'><b>{format_rupiah(amount_due)}</b></font>", ParagraphStyle("DueVal", parent=table_cell_right_style, fontSize=9.5)),
        ],
    ]

    totals_table = Table(
        total_rows,
        colWidths=[273, 130, 120],
    )
    totals_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (1, 0), (2, -1), 0.5, colors.HexColor("#E5DFD9")),
        ("BACKGROUND", (1, 2), (2, 2), colors.HexColor("#FAF7F4")),
    ]))

    story.append(totals_table)
    story.append(Spacer(1, 14))

    # ── 5. RIWAYAT PEMBAYARAN (Jika Ada Transaksi Pembayaran) ─────────────────
    payments = []
    if order.invoice and hasattr(order.invoice, "payments") and order.invoice.payments:
        payments = list(order.invoice.payments)

    if payments:
        pay_elements = [
            Paragraph("RIWAYAT PEMBAYARAN", section_heading_style),
            Spacer(1, 5),
        ]

        pay_table_data = [[
            Paragraph("Waktu Transaksi", table_header_style),
            Paragraph("Tipe", table_header_style),
            Paragraph("Metode Pembayaran", table_header_style),
            Paragraph("Status", table_header_style),
            Paragraph("Jumlah", table_header_right_style),
        ]]

        for p in payments:
            ptype = getattr(p, "payment_type", "")
            ptype_str = ptype.value if hasattr(ptype, "value") else str(ptype)
            pstatus = getattr(p, "payment_status", "")
            pstatus_str = pstatus.value if hasattr(pstatus, "value") else str(pstatus)

            pay_table_data.append([
                Paragraph(format_datetime(getattr(p, "created_at", None)), table_cell_style),
                Paragraph(ptype_str, table_cell_style),
                Paragraph(str(getattr(p, "payment_method", "-")).upper(), table_cell_style),
                Paragraph(pstatus_str, table_cell_style),
                Paragraph(format_rupiah(getattr(p, "jumlah_bayar", 0)), table_cell_right_style),
            ])

        pay_table = Table(pay_table_data, colWidths=[130, 75, 125, 93, 100])
        p_style = [
            ("BACKGROUND", (0, 0), (-1, 0), secondary_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5DFD9")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#E5DFD9")),
        ]
        for r in range(1, len(pay_table_data)):
            p_style.append(("BACKGROUND", (0, r), (-1, r), colors.white if r % 2 == 1 else colors.HexColor("#FAF8F5")))

        pay_table.setStyle(TableStyle(p_style))
        pay_elements.append(pay_table)
        pay_elements.append(Spacer(1, 14))

        story.append(KeepTogether(pay_elements))

    # ── 6. FOOTER ────────────────────────────────────────────────────────────
    footer_elements = [
        HRFlowable(
            width="100%",
            thickness=1,
            color=colors.HexColor("#E5DFD9"),
            spaceBefore=8,
            spaceAfter=8,
        ),
        Paragraph(
            "Terima kasih telah berbelanja di <b>Toti Cakery</b>!<br/>"
            "Invoice ini merupakan bukti pemesanan yang sah dan dihasilkan secara otomatis oleh sistem.",
            footer_style
        ),
        Spacer(1, 2),
    ]
    if store_whatsapp:
        footer_elements.append(
            Paragraph(
                f"Hubungi kami via WhatsApp: <b>{store_whatsapp}</b>",
                footer_style
            )
        )
        footer_elements.append(Spacer(1, 2))
    footer_elements += [
        Paragraph(
            f"Dicetak pada: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')} &bull; ID Pesanan: #{order.id}",
            footer_style
        ),
    ]
    story.append(KeepTogether(footer_elements))

    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer

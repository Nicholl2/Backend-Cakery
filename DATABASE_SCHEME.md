# Database Schema (PostgreSQL)

Dokumentasi skema database PostgreSQL resmi untuk backend Toti Cakery.

---

## 1. DBML (Database Markup Language)

```dbml
// ==========================================
// TOTI CAKERY - OFFICIAL DATABASE SCHEMA
// ==========================================

// 1. AUTHENTICATION, USERS & BUYERS
Table roles {
  id int [pk, increment]
  nama_role varchar(50) [not null, note: 'Owner, Admin, Staff']
  level int [unique, not null, note: '1: Owner, 2: Admin, 3: Staff']
}

Table users {
  id int [pk, increment]
  username varchar(50) [unique, not null]
  email varchar(100) [unique, null]
  phone_number varchar(20) [null]
  password_hash varchar(255) [not null]
  avatar_url varchar(500) [null, note: 'Cloudinary secure HTTPS URL']
  role_id int [ref: > roles.id, not null]
  is_active boolean [default: true]
  nomor_wa_admin varchar(20) [null]
  handles_takeover boolean [default: false, not null, note: 'Siap menerima human takeover']
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table buyers {
  id int [pk, increment]
  name varchar(100) [not null]
  email varchar(100) [unique, not null]
  phone varchar(20) [unique, not null]
  password_hash varchar(255) [not null]
  avatar_url varchar(500) [null, note: 'Cloudinary secure HTTPS URL']
  is_verified boolean [default: false, not null]
  is_active boolean [default: true, not null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table customers {
  id int [pk, increment]
  nama varchar(100) [not null]
  nomor_wa varchar(20) [unique, not null]
  alamat text [null]
  is_verified boolean [default: false]
  human_takeover_active boolean [default: false, not null]
  takeover_expires_at timestamp [null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table otp_codes {
  id varchar(36) [pk, note: 'UUID string']
  target varchar(100) [not null]
  channel varchar(50) [not null, note: 'whatsapp, email']
  purpose varchar(50) [not null, note: 'register, login, reset_password']
  code_hash varchar(255) [not null]
  expires_at timestamp [not null]
  is_used boolean [default: false, not null]
  nonce varchar(50) [unique, null, note: 'WA Deep Link Nonce']
  phone_number varchar(20) [null]
  is_verified boolean [default: false, not null]
  verify_token varchar(36) [unique, null]
  attempt_count int [default: 0, not null]
  created_at timestamp [default: `now()`]
}

// 2. INVENTORY & PURCHASING
Table suppliers {
  id int [pk, increment]
  nama_supplier varchar(100) [unique, not null]
  kontak_person varchar(100) [null]
  email varchar(100) [null]
  nomor_telepon varchar(20) [null]
  alamat text [null]
  kota varchar(50) [null]
  is_active boolean [default: true]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table stock_items {
  id int [pk, increment]
  nama_item varchar(100) [not null]
  satuan enum('gram','ml','pcs','kg','liter') [not null]
  kategori enum('bahan_baku','kemasan') [not null, default: 'bahan_baku']
  harga_per_satuan decimal(10,4) [not null]
  stok_tersedia decimal(10,2) [not null]
  alert_min_stok decimal(10,2) [default: 0, not null]
  supplier_id int [ref: > suppliers.id, null]
  version int [default: 0, not null, note: 'Optimistic Locking Version']
  last_updated_at timestamp
  last_updated_by int [ref: > users.id, null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table purchases {
  id int [pk, increment]
  supplier_id int [ref: > suppliers.id, not null]
  created_by int [ref: > users.id, not null]
  nomor_po varchar(50) [unique, null]
  tanggal_pemesanan timestamp [default: `now()`, not null]
  tanggal_diterima timestamp [null]
  total_harga decimal(15,2) [default: 0, not null]
  catatan text [null]
  is_received boolean [default: false]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table purchase_items {
  id int [pk, increment]
  purchase_id int [ref: > purchases.id, not null]
  stock_item_id int [ref: > stock_items.id, not null]
  jumlah decimal(10,4) [not null]
  harga_satuan decimal(10,4) [not null]
  harga_total decimal(15,2) [not null]
  created_at timestamp [default: `now()`]
}

// 3. PRODUCTS, RECIPES & PRICING
Table products {
  id int [pk, increment]
  nama_produk varchar(100) [not null]
  deskripsi varchar(500) [null]
  kategori varchar(50) [null]
  harga_jual decimal(10,2) [null]
  hpp_total decimal(10,2) [default: 0, null]
  markup_percentage decimal(5,4) [null]
  is_active boolean [default: true]
  image_url varchar(500) [null]
  slug varchar(100) [unique, null]
  rating float [default: 0.0, not null]
  review_count int [default: 0, not null]
  sold_count int [default: 0, not null]
  is_featured boolean [default: false, not null]
  minimum_order int [default: 1, not null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table recipes {
  id int [pk, increment]
  product_id int [ref: > products.id, not null]
  stock_item_id int [ref: > stock_items.id, not null]
  jumlah_dibutuhkan decimal(10,4) [not null, note: 'Takaran bahan per kue']
  quantity_required decimal(10,4) [null]
  unit varchar(20) [null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table price_histories {
  id int [pk, increment]
  product_id int [ref: > products.id, not null]
  harga_jual_lama decimal(10,2) [null]
  harga_jual_baru decimal(10,2) [not null]
  hpp_saat_itu decimal(10,2) [not null]
  changed_by varchar(100) [null]
  created_at timestamp [default: `now()`]
}

// 4. ORDERS, INVOICES & PAYMENTS
Table orders {
  id int [pk, increment]
  customer_id int [ref: > customers.id, not null]
  status enum('pending','in_process','ready','delivered','picked_up','cancelled') [default: 'pending', not null]
  metode_pengiriman enum('pickup','delivery') [not null]
  total_harga_pesanan decimal(10,2) [default: 0, not null]
  created_via varchar(50) [default: 'chatbot', not null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table order_items {
  id int [pk, increment]
  order_id int [ref: > orders.id, not null]
  product_id int [ref: > products.id, not null]
  jumlah int [not null]
  custom_decoration_charge decimal(10,2) [default: 0, not null]
  subtotal decimal(10,2) [not null]
  hpp_snapshot decimal(10,2) [not null, note: 'Modal HPP kue saat transaksi terjadi']
}

Table invoices {
  id int [pk, increment]
  order_id int [ref: - orders.id, unique, not null]
  nomor_invoice varchar(30) [unique, not null]
  total_tagihan decimal(10,2) [not null]
  status enum('unpaid','partial','paid','refunded') [default: 'unpaid', not null]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table payments {
  id int [pk, increment]
  invoice_id int [ref: > invoices.id, not null]
  pg_transaction_id varchar(100) [null, note: 'ID Transaksi Payment Gateway Midtrans']
  jumlah_bayar decimal(10,2) [not null]
  payment_method varchar(50) [not null, note: 'bank_transfer, qris, manual']
  verified_by int [ref: > users.id, null]
  payment_status enum('Success','Pending','Failed','Refunded') [default: 'Pending', not null]
  payment_type enum('DP','Final') [default: 'Final', not null]
  va_number varchar(50) [null]
  qris_url text [null]
  created_at timestamp [default: `now()`]
}

// 5. EXPENSES, REVIEWS & FAQ
Table expenses {
  id int [pk, increment]
  kategori varchar(50) [not null, note: 'electricity, salary, rent, packaging, dll']
  jumlah decimal(15,2) [not null]
  recorded_by int [ref: > users.id, not null]
  tanggal timestamp [default: `now()`]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}

Table reviews {
  id int [pk, increment]
  product_id int [ref: > products.id, not null]
  customer_id int [ref: > customers.id, not null]
  rating int [not null, note: 'Nilai 1 s/d 5']
  komentar text [null]
  created_at timestamp [default: `now()`]
}

Table faq_items {
  id int [pk, increment]
  pertanyaan text [not null]
  jawaban text [not null]
  created_by int [ref: > users.id, not null]
  is_active boolean [default: true]
  created_at timestamp [default: `now()`]
  updated_at timestamp
}
```

---

## 2. Catatan Khusus Arsitektur Basis Data

1. **Optimistic Locking pada Stok Bahan Baku**:
   Kolom `version` pada tabel `stock_items` digunakan saat pengurangan stok pemesanan (`POST /orders`) maupun pemulihan stok saat pembatalan (`POST /orders/{order_id}/cancel`). Jika terjadi race condition / modifikasi bersamaan, update query mendeteksi versi yang tidak cocok dan membatalkan transaksi untuk mencegah inkonsistensi data stok.

2. **Partial Index & Constraints**:
   - `ix_products_slug`: Unique partial index pada `products(slug) WHERE slug IS NOT NULL`.
   - `ix_otp_codes_nonce`: Unique index pada `otp_codes(nonce) WHERE nonce IS NOT NULL`.
   - `ix_otp_codes_verify_token`: Unique index pada `otp_codes(verify_token) WHERE verify_token IS NOT NULL`.

3. **Integritas Relasi & Cascade**:
   - Menghapus `products` otomatis menghapus resep (`recipes`) dan histori harga (`price_histories`) via `cascade="all, delete-orphan"`.
   - Menghapus `stock_items` diblokir jika ID bahan masih tercatat dalam tabel `recipes`.
   - Menghapus `suppliers` diblokir jika masih memiliki relasi transaksi pada tabel `purchases`.
   - Menghapus `purchases` diblokir jika status PO sudah ditandai diterima (`is_received = true`).
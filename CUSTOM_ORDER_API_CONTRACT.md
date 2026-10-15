# Custom Order API Contract

Dokumen ini mendeskripsikan contract backend untuk order manual yang dibuat seller. Pelanggan dianggap sudah mengonfirmasi sebelum seller menyimpan order.

## Endpoint

`POST /orders/custom`

- Authentication: JWT pengguna internal dengan role Staff, Admin, atau Owner.
- Success: `201 Created`, response memakai bentuk `OrderOut`.
- Tidak mengirim email otomatis. Seller yang membuat order bertanggung jawab menindaklanjuti pelanggan.
- Endpoint ini khusus seller. Contract `POST /orders` untuk chatbot/service key dan `POST /orders/buyer` tidak berubah.

## Request

```json
{
  "customer_name": "Alice",
  "customer_phone": "081234567890",
  "customer_address": "Jl. Mawar No. 12",
  "metode_pengiriman": "pickup",
  "fulfillment_date": "2026-10-18T10:00:00+07:00",
  "notes": "Dihias pakai meses dan lilin, untuk ulang tahun.",
  "payment_method_preference": "qris",
  "items": [
    {
      "product_id": 12,
      "qty": 1,
      "custom_decoration_charge": "0.00"
    }
  ],
  "accessories": [
    {"stock_item_id": 34, "quantity": "50"},
    {"stock_item_id": 35, "quantity": "1"}
  ]
}
```

### Field dan aturan

| Field | Wajib | Aturan |
| --- | --- | --- |
| `customer_name` | Ya | String, 1–100 karakter. |
| `customer_phone` | Ya | String, 10–16 karakter; dinormalisasi untuk pencocokan customer. |
| `customer_address` | Tidak | String, maksimal 500 karakter. |
| `metode_pengiriman` | Tidak | `pickup` atau `delivery`; default `pickup`. |
| `fulfillment_date` | Ya | ISO datetime dengan timezone disarankan. Tanggal bisnis WIB wajib H+1 sampai H+30. Berbeda dari `created_at`. |
| `notes` | Tidak | Instruksi bebas, maksimal 500 karakter. Gunakan untuk permintaan dekorasi/pesan, bukan untuk menggantikan item terstruktur. |
| `payment_method_preference` | Tidak | String maksimal 50 karakter. |
| `items` | Ya | Minimal satu item produk katalog. |
| `items[].product_id` | Ya | ID produk aktif dan tersedia yang punya resep stok. |
| `items[].qty` | Tidak | Integer positif; default `1`; minimal mengikuti `minimum_order` produk. |
| `items[].custom_decoration_charge` | Tidak | Biaya dekorasi seller, non-negatif, maksimal dua desimal; default `0.00`. |
| `accessories` | Tidak | Daftar tambahan stok; default kosong. |
| `accessories[].stock_item_id` | Ya per item | ID bahan/aksesoris dari master `StockItem`. |
| `accessories[].quantity` | Ya per item | Kuantitas positif, maksimal empat desimal, dalam satuan item stok tersebut. |

Produk menggunakan harga jual katalog, bukan harga dari request. Aksesoris ditagihkan sebesar `StockItem.harga_per_satuan × quantity`, tanpa markup. Biaya aksesoris dibulatkan ke dua desimal (`half up`). Total invoice adalah subtotal produk + dekorasi + aksesoris.

## Response

Response mengembalikan `OrderOut`, mencakup customer, order items, accessories, invoice, `amount_paid`, `amount_due`, dan payment status. Bagian relevan berbentuk seperti berikut:

```json
{
  "id": 501,
  "customer_id": 92,
  "status": "pending",
  "metode_pengiriman": "pickup",
  "total_harga_pesanan": "125500.00",
  "created_via": "seller",
  "fulfillment_date": "2026-10-18T03:00:00Z",
  "notes": "Dihias pakai meses dan lilin, untuk ulang tahun.",
  "order_items": [
    {
      "id": 801,
      "product_id": 12,
      "product_name": "Bolu Cokelat",
      "unit_price_snapshot": "125000.00",
      "jumlah": 1,
      "custom_decoration_charge": "0.00",
      "subtotal": "125000.00"
    }
  ],
  "accessories": [
    {
      "id": 40,
      "stock_item_id": 34,
      "stock_item_name": "Meses",
      "unit": "gram",
      "quantity": "50.0000",
      "unit_cost_snapshot": "10.0000",
      "subtotal": "500.00"
    },
    {
      "id": 41,
      "stock_item_id": 35,
      "stock_item_name": "Lilin",
      "unit": "pcs",
      "quantity": "1.0000",
      "unit_cost_snapshot": "0.0000",
      "subtotal": "0.00"
    }
  ],
  "invoice": {
    "total_tagihan": "125500.00",
    "status": "unpaid"
  },
  "amount_paid": "0.00",
  "amount_due": "125500.00",
  "payment_status": "unpaid"
}
```

Nilai response di atas hanya contoh. `fulfillment_date` dapat diserialisasi dalam UTC. `unit_price_snapshot`, nama produk, nama/satuan/biaya aksesoris menjaga harga dan tampilan order historis meskipun data master berubah.

## Stock, transaksi, dan error

- Saat disimpan, backend menjumlahkan kebutuhan resep dan aksesoris per `stock_item_id`, mengunci baris stok dengan urutan ID tetap, memvalidasi saldo, lalu menyimpan pengurangan, ledger `ORDER`, order items, aksesoris, dan invoice dalam satu transaksi.
- Jika stok tidak cukup, order gagal dan perubahan stok tidak tersimpan. Error utama: `400` untuk stok tidak cukup/produk tidak tersedia, `409` untuk konfigurasi resep atau biaya stok tidak valid, `422` untuk payload atau ID yang tidak ditemukan.
- Pembatalan order mengembalikan pemakaian sesuai ledger `ORDER` saat order dibuat, satu kali. Harga dan resep terkini tidak dipakai untuk menghitung reversal.
- Belum ada endpoint untuk mengubah item atau aksesoris pada order yang sudah tersimpan. Jangan mengedit detail order langsung di database.
- Skema database perlu migration `20261015_01_custom_order_accessories` sebelum endpoint ini dirilis.

## Catatan integrasi tim

- **Frontend:** ubah form lama yang mengirim `custom_product_name` dan `price` menjadi pilihan produk (`product_id`), tanggal pemenuhan wajib, dan daftar aksesoris stok. Payload lama tidak kompatibel dan akan ditolak validasi.
- **Chatbot:** tidak perlu memanggil endpoint ini; endpoint hanya menerima Staff/Admin/Owner JWT. Contract service `POST /orders` yang dipakai chatbot untuk pesanan normal tidak berubah. Jika bot mengumpulkan detail permintaan manual, seller tetap mencatatnya lewat flow ini.

## Draft pesan untuk tim

### Untuk Ainaya (Frontend)

> Hai Ainaya, ada perubahan contract untuk form custom order seller. `POST /orders/custom` sekarang wajib mengirim `fulfillment_date` (H+1 sampai H+30 WIB), `items[].product_id` dari katalog + `qty` + `custom_decoration_charge`, dan boleh mengirim `accessories[]` berisi `stock_item_id` + `quantity`. Harga produk diambil backend dari katalog; biaya aksesoris diambil dari `StockItem.harga_per_satuan` saat disimpan dan ditambahkan ke invoice tanpa markup. Nama/harga produk serta nama/satuan/biaya aksesoris kembali sebagai snapshot di response (`product_name`, `unit_price_snapshot`, dan `accessories`). Payload lama dengan `custom_product_name`/`price` tidak kompatibel. Ambil pilihan produk dari `GET /products/` dan pilihan bahan/aksesoris dari `GET /stock/`. Detail contoh ada di `CUSTOM_ORDER_API_CONTRACT.md`.

### Untuk Kevin (Chatbot)

> Hai Kevin, custom order seller sekarang memakai produk katalog + aksesoris stok, tanggal fulfillment wajib, dan stok dipesan saat seller menyimpan order. Namun `POST /orders/custom` khusus JWT Staff/Admin/Owner, jadi bot tidak perlu dan tidak bisa memanggil endpoint itu. Contract chatbot untuk order reguler via `POST /orders` / service key tidak berubah. Kalau bot menerima permintaan manual seperti dekorasi atau tanggal khusus, teruskan informasinya ke seller untuk dicatat lewat custom order; detailnya ada di `CUSTOM_ORDER_API_CONTRACT.md`.

# Draft katalog Toti Cakery

Status: draft estimasi untuk ditinjau pemilik; belum dimasukkan ke katalog live.

## Isi
- `products.csv`: 30 produk terpisah (cupcake 8, cake 10, cookies 12), deskripsi dan harga usulan.
- `recipes.csv`: pemakaian bahan per satu unit produk yang dijual (box/paket/cake).
- `inventory_new_items.csv`: bahan/kemasan tambahan, stok awal seluruhnya 0, beserta harga satuan estimasi.

## Asumsi resep
- Cupcakes: ukuran reguler, frosting buttercream sederhana, tanpa topper custom. Cupcake Tart Isi 7 diasumsikan tujuh cupcake dalam kemasan tart.
- Cake: sponge dua lapis diameter sesuai nama, frosting sederhana; dekorasi custom, topper, lilin, dan ongkir tidak termasuk.
- Cookies: mini berarti lima cookies diameter 7 cm; Bento Cookies satu cookie 10 cm; Giant Cookies satu cookie 15 cm. Formula dasar cookies mini lima buah diskalakan berdasarkan perkiraan berat untuk ukuran 10/15 cm.
- Resep adalah starting point untuk uji baker, bukan resep produksi yang sudah divalidasi. Sesuaikan gram/ml setelah menimbang batch nyata.

## Biaya dan stok
HPP aplikasi dihitung dari `jumlah_dibutuhkan × harga_per_satuan` setiap bahan resep, termasuk kemasan jika dimasukkan ke recipe. Semua bahan baru disarankan stok awal 0. Harga satuan pada daftar inventory hanya perkiraan agar draft HPP bisa dihitung; ganti dengan harga faktur pemasok sebelum produk dijual. Jangan mengubah stok barang yang sudah ada menjadi 0 saat import.

Item bawaan yang terlihat pada seed backend dan dipakai ulang di resep: Tepung Terigu Protein Sedang, Gula Pasir, Mentega Wisman, Telur Ayam. Harga seed backend bukan bukti biaya pemasok produksi saat ini; cek Inventory live.

## Harga usulan
Harga adalah positioning basic sampai mid-range untuk Batam dan perlu divalidasi terhadap HPP aktual. Benchmark yang ditemukan bervariasi: cupcake isi 6 decorated di Batam Rp102.000–Rp121.000; cake 18 cm Batam Rp98.000 pada satu listing sederhana dan Rp225.000 pada bakery lain. Variasi topping/dekorasi sangat berpengaruh.
- https://www.carikulinerindonesia.com/178056398/queen-pastry-batam-queen-cake-ultah-murah-permata-baloi
- https://shopee.co.id/Kue-Ulang-Tahun-Ukuran-18cm-Birthday-Cake-Ready-Batam-Kue-Ulang-tahun-Vancy-Cake-Cake-Ulang-Taun-Lucu-Cake-Ulang-Tahun-Simple-Hadiah-Ulang-Tahun-i.927184307.26951371038
- https://menukuliner.net/menu/164294/sp-bakery-batam

## Gambar
Kolom `foto` masih `MENUNGGU_FOTO_ASLI`. Setelah foto tersedia, upload lewat endpoint gambar produk/cloud image service; belum ada foto asli atau URL gambar yang dimasukkan.

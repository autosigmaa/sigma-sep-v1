# SIGMA Kernel V0

Kernel LangGraph untuk mengatur pekerjaan carousel yang dijalankan oleh n8n.
Kode Python mengatur progres, checkpoint, dan approval. Panggilan OpenRouter,
fal.ai, penyimpanan gambar, dan Discord tetap berada di workflow n8n milikmu.

```text
prepare → create_package → generate_assets → approval → finalize
             tunggu n8n       tunggu n8n     tunggu manusia
```

`completed` berarti hasil disetujui. Belum ada publikasi Instagram.
Belum ada workflow n8n atau kredensial provider yang dipasang oleh proyek ini.

## Jalankan

Membutuhkan `uv`, Python 3.12, serta macOS/Linux. `uv sync` memasang dependensi
sesuai `uv.lock`; tidak membutuhkan Docker atau database terpisah.

```sh
uv sync --locked
```

File `.env` lokal sudah dibuat saat setup awal. Jika menyiapkan mesin baru,
buat token secara lokal dengan perintah berikut (hanya jika `.env` belum ada):

```sh
uv run python -c 'from pathlib import Path; import secrets; p=Path(".env"); p.open("x").write("SIGMA_API_TOKEN=" + secrets.token_urlsafe(32) + "\nSIGMA_DB_PATH=data/checkpoints.sqlite\n")'
```

Kemudian, dari folder proyek:

```sh
set -a
source .env
set +a
uv run uvicorn sigma.api:create_app --factory --host 127.0.0.1 --port 8000
```

- API interaktif: <http://127.0.0.1:8000/docs>. Klik **Authorize**, masukkan nilai `SIGMA_API_TOKEN` dari `.env`.
- Health: <http://127.0.0.1:8000/health>.
- Seluruh endpoint job memerlukan header `X-Sigma-Key`.
- `.env` dan `data/` tidak masuk Git. Simpan folder data untuk mempertahankan job.
- Jalankan satu worker. Server kedua dengan database yang sama sengaja ditolak.

Uji tanpa provider, biaya API, atau n8n:

```sh
uv run python -m unittest discover -s tests -v
```

## Kontrak HTTP untuk n8n

Untuk deployment VPS, lihat [panduan Docker](DEPLOY.md).

Base URL adalah alamat kernel yang **bisa diakses dari mesin n8n**.
`127.0.0.1` hanya berlaku jika n8n berada di mesin/proses jaringan yang sama.
n8n dalam Docker Desktop dapat memakai `host.docker.internal` jika kernel di-bind
ke interface yang sesuai. n8n cloud/VPS membutuhkan konektivitas privat atau
deployment kernel di server yang terjangkau; server lokal ini belum otomatis
tersedia di internet. Gunakan HTTPS saat melewati jaringan yang tidak dipercaya.

Di n8n buat credential **Header Auth**, nama header `X-Sigma-Key`, nilainya token
dari `.env`. Jangan taruh token dalam URL, pesan Discord, atau JSON workflow.

### 1. Mulai job: `POST /jobs`

Contoh satu slide untuk uji pertama; default tiga slide, maksimal sepuluh:

```json
{
  "job_id": "marlov-001",
  "brand_id": "marlov",
  "task_type": "carousel",
  "input": {"brief": "Perkenalkan brand Marlov tanpa mengarang klaim produk.", "slide_count": 1}
}
```

Respons penting:

```json
{
  "job_id": "marlov-001",
  "created": true,
  "status": "waiting_external",
  "current_step": "create_package",
  "next_action": {
    "action_id": "marlov-001:create_package:1",
    "type": "create_package",
    "input": {"brief": "Perkenalkan brand Marlov tanpa mengarang klaim produk.", "slide_count": 1}
  }
}
```

Respons sebenarnya juga menyertakan `artifacts`, `approval`, dan `error`.
Gunakan `action_id` dari respons, jangan merakitnya sendiri. Simpan juga
`job_id` di execution n8n. ID yang sama dengan input sama mengembalikan job
existing dengan `created: false`; input berbeda mendapat HTTP 409.

**Workflow start hanya meneruskan ke provider jika `created: true`.**
Jika job sudah ada, baca status dan pulihkan execution yang lama terlebih dahulu.
Kernel tidak melakukan dispatch atau polling n8n secara otomatis.

### 2. Kirim paket: `POST /jobs/{job_id}/results`

Carousel Agent di n8n menghasilkan JSON berikut. Kernel memvalidasi kontraknya
sebelum mengizinkan pembuatan gambar.

```json
{
  "action_id": "marlov-001:create_package:1",
  "outcome": "success",
  "data": {
    "caption": "Mari mengenal Marlov.",
    "slides": [{
      "number": 1,
      "title": "Halo, Marlov",
      "body": "Kenali cerita di balik brand kami.",
      "image_prompt": "Create a finished portrait carousel slide. Include the exact Indonesian title Halo, Marlov and body Kenali cerita di balik brand kami. Use clear typography and generous spacing."
    }]
  }
}
```

Jumlah slide wajib sesuai permintaan; nomor harus lengkap dari 1 sampai jumlah
slide, tanpa duplikat. Output AI yang tidak valid mendapat HTTP 422, tidak
memajukan job. Validasi isi dan ketepatan klaim tetap dilakukan agent/manusia.

Respons berikutnya memiliki `next_action.type: generate_assets` dan paket
tervalidasi di `next_action.input.package` serta `artifacts.package`.
Paket teks kecil disimpan dalam checkpoint supaya MVP belum memerlukan
penyimpanan paket terpisah. File gambar dan knowledge besar tidak disimpan di sana.

### 3. Kirim gambar: `POST /jobs/{job_id}/results`

```json
{
  "action_id": "marlov-001:generate_assets:1",
  "outcome": "success",
  "data": {"slides": [{"number": 1, "url": "https://example.com/slide-1.png"}]}
}
```

URL di atas hanya contoh kontrak, bukan gambar yang telah dibuat. Workflow n8n
memanggil fal.ai per slide, menyimpan file pada storage pilihanmu, lalu mengirim
URL hasil. Hindari menjadikan URL provider sementara sebagai satu-satunya arsip.
Semua slide harus selesai sebelum mengirim hasil batch ke kernel.

Respons: `status: waiting_approval`. `next_action` berisi action approval dan
paket serta URL slide. Kernel memvalidasi URL/nomor slide, tidak mengunduh atau
menjamin kualitas gambar. Review tulisan pada slide sebelum approve.

### 4. Keputusan: `POST /jobs/{job_id}/approval`

```json
{"action_id": "marlov-001:approval:1", "decision": "approve"}
```

`approve` menghasilkan `completed`; `reject` menghasilkan `rejected`.
Awalnya bisa dicoba lewat `/docs`. Nanti workflow Discord menampilkan preview,
menunggu respons manusia yang berwenang, lalu mengirim keputusan ini dengan
credential kernel. Jangan memetakan output model menjadi approval manusia.
Revisi, regenerasi, serta publish Instagram belum termasuk V0; buat job baru
dengan brief yang diperbaiki jika hasil ditolak.

## Workflow n8n yang perlu kamu buat

Satu workflow berurutan cukup:

1. **Manual Trigger + Edit Fields**: job ID, brand, brief, jumlah slide.
2. **HTTP Request → POST /jobs**; **If created** untuk mencegah start duplikat.
3. **Carousel Agent + OpenRouter**: buat caption, title, body, dan image_prompt
   per slide; minta jumlah slide tepat dan JSON sesuai kontrak di atas.
4. **HTTP Request → kirim paket**; gunakan paket tervalidasi dari respons.
5. **Split Out slides → fal.ai → simpan gambar → Aggregate**: satu gambar
   lengkap bertulisan per slide. Pertahankan `number` saat menggabungkan hasil.
6. **HTTP Request → kirim hasil gambar**.
7. **Discord Send and Wait for Response**, atau mekanisme approval Discord
   yang tersedia di versimu: kirim preview ke channel review, batasi approver.
8. **HTTP Request → POST /approval** berdasarkan keputusan manusia.

Respons HTTP kernel adalah checkpoint penghubung antarbagian. Simpan responsnya
agar node n8n berikutnya memakai action ID yang tepat. Koneksi, credential,
model, storage, channel, dan kontrol approver disetel di n8n, tanpa mengubah kernel.

## Error, retry, dan restart

`GET /jobs/{job_id}` mengembalikan status tanpa menjalankan pekerjaan.
`POST /jobs/{job_id}/resume` melanjutkan langkah internal yang terputus.
Jika masih menunggu hasil/approval atau job sudah terminal, endpoint ini hanya
mengembalikan status. Ia tidak melewati approval atau menjalankan provider.

Jika n8n memastikan suatu percobaan gagal, kirim:

```json
{
  "action_id": "marlov-001:generate_assets:1",
  "outcome": "failed",
  "error": {"message": "Provider rejected the request before generation.", "retryable": true}
}
```

- `retryable: true`: kernel memberi action baru untuk langkah yang sama,
  maksimum tiga percobaan total per langkah. n8n yang memutuskan kapan menjalankannya.
- `retryable: false` atau percobaan ketiga gagal: job `failed`. Job ini terminal;
  perbaiki masalah lalu buat job baru.
- Kiriman hasil/approval identik dengan action ID yang sama aman dikirim ulang.
  Payload berbeda untuk action yang sudah selesai mendapat HTTP 409.
- Timeout dengan hasil provider tidak diketahui: **jangan otomatis generate lagi**.
  Periksa execution n8n/request ID fal.ai, pulihkan hasil yang sudah dibuat, lalu
  kirim ulang callback. Untuk sebagian slide yang sukses, gunakan hasilnya kembali.
- Jika callback tidak mendapat respons, baca status atau kirim ulang callback yang
  sama. Jangan ulangi node provider hanya untuk mengulang callback.
- Setiap mutasi diserialkan, disimpan dengan durability sinkron, dan hanya setelah
  itu respons sukses dikirim. `schema_version: 1` menolak checkpoint versi lain.

Tidak ada janji exactly-once untuk API provider: kernel mencegah penerimaan hasil
ganda, sementara deduplikasi panggilan berbayar menjadi tanggung jawab n8n.
V0 belum memiliki worker pool, scheduler, auto-recovery n8n, atau database terdistribusi.

## Verifikasi

Suite pengujian mencakup restart setiap tahap, proses mati mendadak, pemulihan
node internal yang gagal, approval/reject, batas retry, validasi, autentikasi,
dua callback bersamaan, dan penolakan server kedua. Pengujian memakai data contoh;
belum membuktikan koneksi n8n, kualitas konten, hasil fal.ai, atau pengiriman Discord.

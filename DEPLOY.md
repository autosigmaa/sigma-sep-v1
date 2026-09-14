# Deploy kernel di VPS menggunakan Docker

Paket deploy sudah disiapkan. Konfigurasi Compose dapat divalidasi tanpa server
Docker; build image dan uji container masih perlu dilakukan di Docker Engine
yang berjalan. Ini belum berarti kernel sudah di-deploy ke VPS.

## Apa yang terpasang di mana?

- VPS: Docker Engine dan plugin Docker Compose.
- Image kernel: Python 3.12, dependensi dari `uv.lock`, dan kode SIGMA.
- Volume `kernel_data`: checkpoint SQLite, terpisah dari image.
- File `.env` di VPS: token API; dimasukkan saat container dijalankan, bukan saat build.

Tidak perlu memasang Python, uv, LangGraph, atau FastAPI langsung di VPS.
Saat build pertama Docker mengunduh base image dan dependensi, sehingga perlu
koneksi internet. Host Linux amd64 atau arm64 membangun image untuk arsitekturnya.

## 1. Siapkan VPS dan salin proyek

Pasang Docker dari [petunjuk resmi sesuai OS](https://docs.docker.com/engine/install/).
Jika Docker/Compose sudah tersedia melalui panel VPS, gunakan instalasi tersebut.
Cek:

```sh
docker version
docker compose version
```

Salin folder proyek ke lokasi milik akunmu, misalnya `~/sigma-kernel`.
File yang dibutuhkan hanya `Dockerfile`, `.dockerignore`, `compose.yaml`,
`pyproject.toml`, `uv.lock`, dan folder `sigma/`.
Tidak perlu mengirim `.venv`, `__pycache__`, atau database eksperimen lokal.

## 2. Buat token di VPS

Dari folder proyek, jalankan sekali. Perintah menolak menimpa `.env` yang ada:

```sh
(umask 077; set -C; printf 'SIGMA_API_TOKEN=%s\n' "$(openssl rand -hex 32)" > .env)
```

Simpan token itu sebagai credential Header Auth n8n dengan nama header
`X-Sigma-Key`. Jangan letakkan dalam URL webhook atau pesan Discord.

## 3. Build dan hidupkan

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 kernel
curl --fail http://127.0.0.1:8000/health
```

Hasil health yang diharapkan: `{"status":"ok"}`. Container berjalan sebagai
user non-root, satu worker. Ia tetap berjalan setelah sesi SSH ditutup.
Pastikan Docker service aktif saat boot. `restart: unless-stopped` menghidupkan
container lagi setelah proses keluar/reboot, kecuali sengaja dihentikan.
Healthcheck mendeteksi kondisi unhealthy, **tidak otomatis me-restart proses yang
macet**. Pemantauan/alert 24 jam belum dikonfigurasi.

## 4. Mengakses dari n8n

Default port hanya tersedia pada localhost VPS. Jalur koneksi menyesuaikan
lokasi n8n, tanpa mengubah graph:

- **n8n berjalan langsung pada VPS yang sama:** gunakan `http://127.0.0.1:8000`.
- **n8n dalam Docker:** sambungkan kedua service ke Docker network yang sama,
  kemudian gunakan `http://kernel:8000`. Definisi network perlu disesuaikan
  dengan Compose n8n yang benar-benar kamu gunakan.
- **n8n cloud/server berbeda:** sediakan HTTPS reverse proxy atau koneksi privat
  menuju kernel. Domain, sertifikat, dan routing belum dikonfigurasi di paket ini.

Untuk melihat dokumentasi API secara privat dari laptop, buat SSH tunnel:

```sh
ssh -L 18000:127.0.0.1:8000 USER@IP_VPS
```

Ganti `USER` dan `IP_VPS` dengan milikmu. Buka `http://127.0.0.1:18000/docs`,
lalu Authorize memakai token VPS. Jangan menyalin token ke percakapan ini.

## 5. Update, restart, dan backup

Setelah menyalin kode baru, build ulang memakai perintah yang sama:

```sh
docker compose up -d --build
```

Volume data tetap dipakai. Jangan menjalankan `docker compose down -v` karena
opsi `-v` menghapus volume data. Perubahan versi schema checkpoint memerlukan
migrasi terlebih dahulu; V0 belum menyediakan migrasi lintas schema.

Backup konsisten SQLite bisa dibuat saat server berjalan menggunakan API backup
SQLite, kemudian disalin ke filesystem VPS:

```sh
mkdir -p backups
docker compose exec -T kernel python -c 'import sqlite3; source=sqlite3.connect("/data/checkpoints.sqlite"); target=sqlite3.connect("/data/backup.sqlite"); source.backup(target); target.close(); source.close()'
docker compose cp kernel:/data/backup.sqlite "backups/checkpoints-$(date +%Y%m%d-%H%M%S).sqlite"
```

Simpan salinan backup juga di luar VPS. Volume Docker menjaga data saat container
diganti, tetapi tidak melindungi dari kehilangan disk/server. Cadangkan `.env`
secara terpisah di tempat aman. Backup dan pemantauan terjadwal belum dipasang.

## Batas implementasi sekarang

Kernel menerima job, hasil, dan approval melalui HTTP; mengatur graph; serta
mempertahankan checkpoint setelah restart. Kernel **belum mengirim instruksi ke
webhook n8n secara otomatis**. Memasang container tidak menambahkan fitur tersebut.
Setelah lingkungan VPS/n8n diketahui, sambungan webhook, pelacakan pengiriman,
dan workflow Discord dapat dibuat serta diuji bertahap sesuai kontrak README.

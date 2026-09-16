# Deploy dan uji kernel menggunakan Docker/Podman

Paket deploy menggunakan Dockerfile dan Compose yang sama untuk Docker atau
Podman. Pengujian lokal memakai Podman pada Linux arm64; deployment VPS tetap
harus diperiksa pada mesin tujuan. Ini belum berarti kernel sudah di-deploy ke VPS.

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

Jika muncul `address already in use`, port 8000 sudah dipakai proses lain
(termasuk kernel Python lokal). Tambahkan `SIGMA_PORT=18000` ke `.env`, lalu
jalankan kembali `docker compose up -d --build`. Health dan dokumentasi sekarang
berada di `http://127.0.0.1:18000`. Port di dalam container tetap 8000.
`SIGMA_PORT` hanya mengatur port host pada Compose, bukan perintah uvicorn lokal.

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
mempertahankan checkpoint setelah restart. **Webhook berada di n8n.** Workflow
memanggil kernel dan menjalankan `next_action` dari respons, lalu mengirim callback.
Pembagian ini tidak membutuhkan dispatcher atau antrean di Python. Panduan
[kontrak workflow](README.md#workflow-n8n-yang-perlu-kamu-buat) menjelaskan hubungan
job kernel, persistent Agent published/session, execution n8n, dan request provider.
Koneksi layanan nyata dan deployment VPS belum dibuktikan oleh pengujian lokal.

## Uji HTTP dan pemulihan container lokal

Jalankan dari folder proyek lengkap (termasuk `tests/`) dengan `.env` yang sudah
berisi token valid. Diperlukan Podman, provider Compose yang mendukung `--wait`,
Bash, dan curl. Pada macOS, hidupkan VM Podman jika belum berjalan:

```sh
podman machine start
podman info
podman compose version
bash tests/run_container_smoke.sh podman
```

Port default pengujian adalah `127.0.0.1:18001`; jika dipakai, jalankan:

```sh
SIGMA_SMOKE_PORT=18002 bash tests/run_container_smoke.sh podman
```

Runner membangun image, menunggu container healthy, dan memeriksa `/health` dari
host. Driver HTTP stdlib di dalam container meniru peran n8n tanpa memanggil
Agent, fal.ai, storage, atau Discord. Dua job berbeda brand dibuat dan diproses
bersamaan; semua konten dan URL gambar adalah data contoh.

Runner memakai project unik `sigma-smoke-<timestamp>-<pid>`, image sendiri,
dan volume `<project>_kernel_data`. Container pengujian memakai `restart: no`
agar SIGKILL terkontrol. Dua kali container dimatikan paksa lalu diganti dengan
volume yang sama: setelah paket tersimpan dan setelah gambar tersimpan.
Driver mengambil status HTTP kembali, memeriksa `job`/`next_action`, mengirim
ulang callback sebelumnya, lalu melanjutkan sampai terminal.

Pada keberhasilan, output terakhir pengujian berisi:

```text
PASS: container HTTP scenarios and two forced-kill recoveries
```

Trap membersihkan container, network, volume, dan tag image khusus tes saat
runner selesai atau gagal; resource deployment yang sudah ada tidak disentuh.
Jika runner sendiri terkena SIGKILL atau host mati, trap tidak bisa berjalan.
Cari nama project tes yang tepat dari output sebelumnya dan bersihkan hanya
resource miliknya (ganti placeholder, jangan gunakan nama deployment):

```sh
podman compose -p sigma-smoke-TIMESTAMP-PID -f compose.yaml down --volumes
podman image rm localhost/sigma-smoke-TIMESTAMP-PID:local
```

Jangan menjalankan `down --volumes` terhadap deployment yang datanya ingin disimpan.
Runner juga menerima `docker` sebagai argumen; verifikasi di bawah dilakukan
menggunakan Podman 5.8.3 pada Linux arm64 melalui VM lokal.

### Hasil verifikasi

- Delapan pengujian kernel lulus, termasuk kompatibilitas checkpoint v1 tanpa migrasi.
- Build image dan pemeriksaan health lulus; runner HTTP keluar dengan kode 0.
- Dua job berbeda brand tetap terpisah, sampai `completed` dan `rejected`.
- Dua SIGKILL dan penggantian container mempertahankan checkpoint serta konteks job.
- Respons callback sengaja diabaikan untuk meniru respons hilang; pengiriman ulang
  sebelum/sesudah restart tidak memajukan state dua kali.
- Callback job lain, hasil tidak valid, approval terlalu awal, dan request tanpa
  token ditolak tanpa perubahan state.
- Retry berhenti setelah tiga percobaan; `/resume` tidak membuka kembali job
  `completed`, `rejected`, maupun `failed`.
- Container, network, volume, dan tag image tes dibersihkan setelah pengujian.

Ini membuktikan kontrak dan pemulihan kernel pada batas checkpoint. Tidak menguji
putusnya listrik di tengah disk write, koneksi provider nyata, atau jaminan
panggilan berbayar hanya sekali. n8n tetap harus memeriksa execution/request
sebelumnya ketika hasil provider tidak diketahui.

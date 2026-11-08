# Telegram Cloud Indexer & Search Dashboard

Memindai grup/chat Telegram pribadi untuk media, lalu menghasilkan **mesin pencari offline berbasis HTML** yang interaktif dan ringan — tanpa mengunduh file ke hard drive.

## Fitur

- 🔍 Pemindaian cepat seluruh chat Telegram (foto, video, audio, dokumen)
- 📄 Generate `katalog.html` — search engine offline yang bisa dibuka langsung di browser
- 🗂️ Metadata tersimpan di `catalog.json` (nama file, ukuran, kategori, duplikat, link langsung)
- 🖼️ **Web dashboard** (`server.py`) dengan preview visual lazy-loaded dari cloud
- ⬇️ Unduh on-demand (streaming langsung tanpa memenuhi disk)
- 🗑️ Hapus file dari cloud + pembersih duplikat batch

## Persyaratan

- Python 3.9+
- [Telethon](https://github.com/LonamiWebs/Telethon)
- aiohttp (untuk server.py)

```bash
pip install -r requirements.txt
```

## Konfigurasi

Buat file `config.json` dengan kredensial API Telegram kamu:

```json
{
  "api_id": 12345678,
  "api_hash": "abcdef1234567890abcdef1234567890"
}
```

> ⚠️ `config.json` dan file `*.session` TIDAK ikut di-commit (sudah diabaikan oleh `.gitignore`). Jangan pernah membagikan kredensial ini.

## Pemakaian

### 1. Jalankan indexer

```bash
bash run.sh
# atau langsung:
python3 indexer.py
```

Hasilnya:
- `catalog.json` — database metadata
- `katalog.html` — search engine offline (buka di browser)

### 2. Jalankan dashboard web

```bash
bash start_dashboard.sh
# atau langsung:
python3 server.py
```

## Struktur

```
telegram-cloud-indexer/
├── indexer.py          # Pemindai & generator katalog HTML
├── server.py           # Web dashboard & file manager
├── run.sh              # Menjalankan indexer
├── start_dashboard.sh  # Menjalankan server
├── config.json         # Kredensial API (dikecualikan dari git)
├── catalog.json        # Hasil generate (dikecualikan)
├── katalog.html        # Search engine (dikecualikan)
├── thumb_cache/        # Cache thumbnail (dikecualikan)
└── venv/               # Virtual environment (dikecualikan)
```

## Lisensi

[MIT](LICENSE)

#!/usr/bin/env python3
"""
Telegram Cloud Web Dashboard & File Manager
Author: Antigravity
Features:
- Visual Previews (lazy-loaded thumbnails from cloud)
- On-Demand Download (streamed directly without bloating disk)
- Cloud Deletion (delete unwanted/duplicate files permanently from Telegram)
- Batch Duplicate Cleaner (clean 1.8 GB wasted space with 1 click)
- Silky Smooth Infinite Scroll for 20,000+ files
"""

import os
import sys
import json
import asyncio
from aiohttp import web
from telethon import TelegramClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CATALOG_FILE = os.path.join(BASE_DIR, "catalog.json")
SESSION_FILE = os.path.join(BASE_DIR, "telegram_indexer.session")
THUMB_CACHE_DIR = os.path.join(BASE_DIR, "thumb_cache")

os.makedirs(THUMB_CACHE_DIR, exist_ok=True)

# Global variables
client = None
target_entity = None
catalog_data = {}
items_map = {}

def load_catalog():
    global catalog_data, items_map
    if os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, "r", encoding="utf-8") as f:
            catalog_data = json.load(f)
            items_map = {item["id"]: item for item in catalog_data.get("items", [])}
            print(f"[OK] Loaded {len(items_map)} items from catalog.json")
    else:
        catalog_data = {"items": [], "breakdown": {}}
        items_map = {}

def save_catalog():
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog_data, f, indent=2, ensure_ascii=False)

def format_size(bytes_size):
    if not bytes_size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} PB"

async def init_telegram():
    global client, target_entity
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError("config.json not found!")
    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)

    client = TelegramClient(SESSION_FILE, cfg["api_id"], cfg["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        print("[ERROR] Session is not authorized. Run ./run.sh first to login!")
        sys.exit(1)

    group_name = catalog_data.get("group_name", "Jbech")
    async for dialog in client.iter_dialogs(limit=50):
        if dialog.title == group_name or (group_name.lower() in dialog.title.lower()):
            target_entity = dialog.entity
            print(f"[OK] Connected to Telegram group: '{dialog.title}' (ID: {dialog.id})")
            break

    if not target_entity:
        print(f"[WARN] Group '{group_name}' not found directly, using first group...")
        async for dialog in client.iter_dialogs(limit=10):
            if dialog.is_group or dialog.is_channel:
                target_entity = dialog.entity
                break

# --- API HANDLERS ---

async def handle_get_catalog(request):
    return web.json_response(catalog_data)

async def handle_thumbnail(request):
    msg_id = int(request.match_info['msg_id'])
    cache_path = os.path.join(THUMB_CACHE_DIR, f"{msg_id}.jpg")

    if os.path.exists(cache_path):
        return web.FileResponse(cache_path)

    try:
        msg = await client.get_messages(target_entity, ids=msg_id)
        if not msg or not msg.media:
            return web.Response(status=404)

        data = await client.download_media(msg, file=bytes, thumb=-1)
        if data:
            with open(cache_path, "wb") as f:
                f.write(data)
            return web.Response(body=data, content_type="image/jpeg")
        else:
            return web.Response(status=404)
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def handle_download(request):
    msg_id = int(request.match_info['msg_id'])
    item = items_map.get(msg_id)
    if not item:
        return web.Response(status=404, text="File not found in catalog")

    try:
        msg = await client.get_messages(target_entity, ids=msg_id)
        if not msg or not msg.media:
            return web.Response(status=404, text="Media not found on Telegram")

        filename = item.get("file_name", f"file_{msg_id}")
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'application/octet-stream',
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Length': str(item.get("size_bytes", 0))
            }
        )
        await response.prepare(request)

        async for chunk in client.iter_download(msg.media, request_size=128 * 1024):
            await response.write(chunk)

        await response.write_eof()
        return response
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def handle_delete(request):
    msg_id = int(request.match_info['msg_id'])
    item = items_map.get(msg_id)
    if not item:
        return web.json_response({"ok": False, "error": "Item not in catalog"}, status=404)

    try:
        await client.delete_messages(target_entity, msg_id)

        # Remove from local cache and catalog
        cache_path = os.path.join(THUMB_CACHE_DIR, f"{msg_id}.jpg")
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
            except Exception:
                pass

        if msg_id in items_map:
            del items_map[msg_id]
        catalog_data["items"] = [i for i in catalog_data["items"] if i["id"] != msg_id]
        catalog_data["total_files"] = len(catalog_data["items"])

        # Re-calc size
        total_b = sum(i.get("size_bytes", 0) for i in catalog_data["items"])
        catalog_data["total_bytes"] = total_b
        catalog_data["total_bytes_str"] = format_size(total_b)

        save_catalog()
        return web.json_response({"ok": True, "deleted_id": msg_id})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_batch_delete_duplicates(request):
    """
    Deletes all duplicate copies (items where is_duplicate=True and is_original=False).
    Keeps the original copy safe.
    """
    try:
        copies_to_delete = [
            i["id"] for i in catalog_data.get("items", [])
            if i.get("is_duplicate") and not i.get("is_original")
        ]

        if not copies_to_delete:
            return web.json_response({"ok": True, "deleted_count": 0, "freed_str": "0 B"})

        total_freed_bytes = sum(items_map[mid].get("size_bytes", 0) for mid in copies_to_delete if mid in items_map)

        # Telegram delete_messages can take a list of message IDs up to 100 at a time
        chunk_size = 100
        for i in range(0, len(copies_to_delete), chunk_size):
            chunk = copies_to_delete[i:i + chunk_size]
            await client.delete_messages(target_entity, chunk)
            await asyncio.sleep(0.5)

        # Update catalog
        deleted_set = set(copies_to_delete)
        catalog_data["items"] = [i for i in catalog_data["items"] if i["id"] not in deleted_set]
        for mid in deleted_set:
            if mid in items_map:
                del items_map[mid]

        catalog_data["total_files"] = len(catalog_data["items"])
        total_b = sum(i.get("size_bytes", 0) for i in catalog_data["items"])
        catalog_data["total_bytes"] = total_b
        catalog_data["total_bytes_str"] = format_size(total_b)

        # Reset duplicate flags on remaining items
        for it in catalog_data["items"]:
            it["is_duplicate"] = False
            it["is_original"] = False
            it["dup_copies"] = 1

        catalog_data["duplicates_summary"] = {
            "total_duplicate_files": 0,
            "total_groups": 0,
            "wasted_bytes": 0,
            "wasted_str": "0 B"
        }

        save_catalog()
        return web.json_response({
            "ok": True,
            "deleted_count": len(copies_to_delete),
            "freed_str": format_size(total_freed_bytes)
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_index(request):
    html = """<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Telegram Cloud Dashboard & Manager</title>
  <style>
    :root {
      --bg: #0b1120;
      --card-bg: #1e293b;
      --card-hover: #273548;
      --accent: #38bdf8;
      --accent-hover: #0ea5e9;
      --danger: #ef4444;
      --danger-hover: #dc2626;
      --success: #10b981;
      --warning: #f59e0b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --border: #334155;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 20px;
    }
    .container { max-width: 1380px; margin: 0 auto; }
    header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 20px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    .brand h1 { font-size: 24px; color: var(--accent); display: flex; align-items: center; gap: 8px; }
    .brand p { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
    
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      text-align: center;
    }
    .stat-card.warning { border-color: var(--warning); background: #1c1917; }
    .stat-val { font-size: 22px; font-weight: 700; color: var(--accent); }
    .stat-card.warning .stat-val { color: #fbbf24; }
    .stat-lbl { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

    .dup-banner {
      background: linear-gradient(135deg, #2d1804 0%, #1c1917 100%);
      border: 1px solid var(--warning);
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 20px;
      display: none;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .dup-banner-text h3 { color: #fbbf24; font-size: 16px; margin-bottom: 4px; }
    .dup-banner-text p { color: #fde68a; font-size: 13px; }
    .btn-clean-all {
      background: #d97706;
      color: white;
      border: none;
      padding: 10px 18px;
      border-radius: 8px;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: background 0.2s;
    }
    .btn-clean-all:hover { background: #b45309; }

    .controls {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .search-row { display: flex; gap: 10px; flex-wrap: wrap; }
    .search-input {
      flex: 1;
      min-width: 260px;
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      color: white;
      font-size: 14px;
      outline: none;
    }
    .search-input:focus { border-color: var(--accent); }
    .select-dropdown {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      color: white;
      font-size: 13px;
      outline: none;
      cursor: pointer;
    }
    .filters-row { display: flex; gap: 8px; flex-wrap: wrap; }
    .filter-btn {
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 6px 14px;
      color: var(--text-muted);
      font-size: 13px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .filter-btn:hover, .filter-btn.active {
      background: var(--accent);
      color: #0b1120;
      font-weight: 600;
      border-color: var(--accent);
    }
    .filter-btn.dup-btn:hover, .filter-btn.dup-btn.active {
      background: var(--warning);
      color: #0b1120;
      font-weight: 700;
      border-color: var(--warning);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.15s, border-color 0.15s;
    }
    .card.is-dup { border-left: 4px solid var(--warning); }
    .card:hover { transform: translateY(-2px); border-color: var(--accent); }

    /* Visual Preview Box */
    .preview-box {
      width: 100%;
      height: 170px;
      background: #090d16;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      overflow: hidden;
      cursor: pointer;
    }
    .preview-img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform 0.2s;
    }
    .preview-img:hover { transform: scale(1.04); }
    .preview-fallback {
      font-size: 44px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      color: var(--text-muted);
    }
    .preview-fallback span { font-size: 12px; font-weight: 600; }

    .card-body { padding: 14px; flex: 1; }
    .file-title {
      font-size: 14px;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 6px;
    }
    .file-caption {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 10px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      font-style: italic;
    }
    .badges { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
    .badge {
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 4px;
      background: #334155;
      color: #e2e8f0;
    }
    .badge.size { background: #0369a1; color: #e0f2fe; font-weight: 600; }
    .badge.orig { background: #065f46; color: #a7f3d0; font-weight: 600; }
    .badge.copy { background: #78350f; color: #fde68a; font-weight: 600; }

    .card-actions {
      display: flex;
      gap: 6px;
      padding: 0 14px 14px;
    }
    .btn-act {
      flex: 1;
      padding: 8px 10px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      text-align: center;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      transition: all 0.15s;
    }
    .btn-download { background: #0284c7; color: white; }
    .btn-download:hover { background: var(--accent-hover); }
    .btn-tg { background: #1e3a8a; color: #bfdbfe; }
    .btn-tg:hover { background: #1d4ed8; color: white; }
    .btn-del { background: #450a0a; color: #fca5a5; }
    .btn-del:hover { background: var(--danger); color: white; }

    .load-more-container {
      text-align: center;
      padding: 30px;
    }
    .btn-load-more {
      background: #1e293b;
      border: 1px solid var(--border);
      color: var(--accent);
      padding: 10px 24px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-load-more:hover { background: var(--accent); color: #0b1120; }

    /* Modal Lightbox */
    .modal {
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0; top: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.85);
      align-items: center;
      justify-content: center;
    }
    .modal img { max-width: 90%; max-height: 85%; border-radius: 8px; }
    .modal-close {
      position: absolute; top: 20px; right: 24px;
      font-size: 32px; color: white; cursor: pointer;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <h1>☁️ Telegram Cloud Manager</h1>
        <p>Grup: <strong id="lblGroupName">-</strong> • Terhubung langsung ke Cloud Telegram</p>
      </div>
    </header>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-val" id="statFiles">0</div>
        <div class="stat-lbl">Total File</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="statCloud">0 B</div>
        <div class="stat-lbl">Kapasitas Cloud</div>
      </div>
      <div class="stat-card warning">
        <div class="stat-val" id="statDups">0</div>
        <div class="stat-lbl" id="statDupsWasted">⚠️ Duplikat (0 B Sia-sia)</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="statDocs">0</div>
        <div class="stat-lbl">📄 Dokumen</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="statPhotos">0</div>
        <div class="stat-lbl">📸 Foto</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="statVideos">0</div>
        <div class="stat-lbl">🎬 Video</div>
      </div>
    </div>

    <!-- Duplicate Cleanup Banner -->
    <div id="dupBanner" class="dup-banner">
      <div class="dup-banner-text">
        <h3>⚠️ Pembersih Duplikat Otomatis</h3>
        <p id="dupBannerDesc">Ditemukan file duplikat. Bersihkan semua salinan untuk menghemat storage cloud.</p>
      </div>
      <button onclick="cleanAllDuplicates()" class="btn-clean-all">
        🗑️ Hapus Semua Salinan Duplikat
      </button>
    </div>

    <div class="controls">
      <div class="search-row">
        <input type="text" id="searchInput" class="search-input" placeholder="🔍 Cari nama file, caption, tanggal, atau ekstensi..." autofocus>
        <select id="sortSelect" class="select-dropdown">
          <option value="newest">📅 Tanggal: Terbaru</option>
          <option value="oldest">📅 Tanggal: Terlama</option>
          <option value="largest">💾 Ukuran: Terbesar</option>
          <option value="smallest">💾 Ukuran: Terkecil</option>
          <option value="name_asc">🔤 Nama: A - Z</option>
        </select>
      </div>

      <div class="filters-row">
        <button class="filter-btn active" data-cat="all">Semua</button>
        <button class="filter-btn dup-btn" data-cat="duplicate">⚠️ Duplikat</button>
        <button class="filter-btn" data-cat="photo">📸 Foto</button>
        <button class="filter-btn" data-cat="video">🎬 Video</button>
        <button class="filter-btn" data-cat="document">📄 Dokumen</button>
        <button class="filter-btn" data-cat="audio">🎵 Audio</button>
        <button class="filter-btn" data-cat="archive">📦 Arsip</button>
      </div>
    </div>

    <div id="resultsCount" style="font-size:13px; color:var(--text-muted); margin-bottom:12px;">Memuat data...</div>
    <div id="itemsGrid" class="grid"></div>

    <div class="load-more-container">
      <button id="btnLoadMore" onclick="loadMore()" class="btn-load-more" style="display:none;">
        Tampilkan Lebih Banyak ⬇️
      </button>
    </div>
  </div>

  <!-- Lightbox Modal -->
  <div id="imageModal" class="modal" onclick="closeModal()">
    <span class="modal-close">&times;</span>
    <img id="modalImg" src="">
  </div>

  <script>
    let allItems = [];
    let filteredItems = [];
    let currentFilter = 'all';
    let searchQuery = '';
    let currentSort = 'newest';
    let displayedCount = 0;
    const PAGE_SIZE = 48; // Smooth 48 items per batch

    const icons = {
      document: '📄',
      photo: '📸',
      video: '🎬',
      audio: '🎵',
      archive: '📦',
      other: '📁'
    };

    async function init() {
      try {
        const res = await fetch('/api/catalog');
        const data = await res.json();
        allItems = data.items || [];

        document.getElementById('lblGroupName').textContent = data.group_name || 'Jbech';
        document.getElementById('statFiles').textContent = data.total_files || allItems.length;
        document.getElementById('statCloud').textContent = data.total_bytes_str || '0 B';

        const dup = data.duplicates_summary || {};
        document.getElementById('statDups').textContent = dup.total_duplicate_files || 0;
        document.getElementById('statDupsWasted').textContent = `⚠️ Duplikat (${dup.wasted_str || '0 B'} Sia-sia)`;

        const bk = data.breakdown || {};
        document.getElementById('statDocs').textContent = bk.document || 0;
        document.getElementById('statPhotos').textContent = bk.photo || 0;
        document.getElementById('statVideos').textContent = bk.video || 0;

        if (dup.total_duplicate_files > 0) {
          const banner = document.getElementById('dupBanner');
          banner.style.display = 'flex';
          document.getElementById('dupBannerDesc').textContent = 
            `Ditemukan ${dup.total_duplicate_files} file duplikat (${dup.total_groups} kelompok). Menghapus salinannya akan menghemat ${dup.wasted_str} di cloud!`;
        }

        applyFilters();
      } catch (e) {
        document.getElementById('resultsCount').textContent = 'Gagal memuat katalog: ' + e;
      }
    }

    function applyFilters() {
      filteredItems = allItems.filter(item => {
        let matchCat = false;
        if (currentFilter === 'all') matchCat = true;
        else if (currentFilter === 'duplicate') matchCat = !!item.is_duplicate;
        else matchCat = (item.category === currentFilter);

        const q = searchQuery.toLowerCase().trim();
        const matchSearch = !q ||
          item.file_name.toLowerCase().includes(q) ||
          (item.caption && item.caption.toLowerCase().includes(q)) ||
          item.ext.toLowerCase().includes(q) ||
          item.date_str.toLowerCase().includes(q);

        return matchCat && matchSearch;
      });

      filteredItems.sort((a, b) => {
        if (currentFilter === 'duplicate') {
          if (a.dup_group_id !== b.dup_group_id) return b.size_bytes - a.size_bytes;
          return a.timestamp - b.timestamp;
        }
        if (currentSort === 'newest') return b.timestamp - a.timestamp;
        if (currentSort === 'oldest') return a.timestamp - b.timestamp;
        if (currentSort === 'largest') return b.size_bytes - a.size_bytes;
        if (currentSort === 'smallest') return a.size_bytes - b.size_bytes;
        if (currentSort === 'name_asc') return a.file_name.localeCompare(b.file_name);
        return 0;
      });

      displayedCount = 0;
      document.getElementById('itemsGrid').innerHTML = '';
      loadMore();
    }

    function loadMore() {
      const grid = document.getElementById('itemsGrid');
      const nextBatch = filteredItems.slice(displayedCount, displayedCount + PAGE_SIZE);
      displayedCount += nextBatch.length;

      document.getElementById('resultsCount').textContent = 
        `Menampilkan ${displayedCount} dari ${filteredItems.length} item (${allItems.length} total file di cloud)`;

      const html = nextBatch.map(item => {
        const isVisual = (item.category === 'photo' || item.category === 'video');
        const icon = icons[item.category] || '📁';
        
        let previewHtml = '';
        if (isVisual) {
          previewHtml = `
            <div class="preview-box" onclick="openModal('/api/thumb/${item.id}')">
              <img src="/api/thumb/${item.id}" loading="lazy" class="preview-img" alt="${item.file_name}"
                   onerror="this.parentElement.innerHTML='<div class=\\'preview-fallback\\'>${icon}<span>${item.ext.toUpperCase()}</span></div>'">
            </div>
          `;
        } else {
          previewHtml = `
            <div class="preview-box" style="background:#0f172a;">
              <div class="preview-fallback">
                ${icon}
                <span>${item.ext ? item.ext.toUpperCase() : item.category.toUpperCase()}</span>
              </div>
            </div>
          `;
        }

        let dupBadge = '';
        if (item.is_duplicate) {
          if (item.is_original) {
            dupBadge = `<span class="badge orig">⭐ VERSI ASLI</span>`;
          } else {
            dupBadge = `<span class="badge copy">⚠️ SALINAN DUPLIKAT</span>`;
          }
        }

        const captionHtml = item.caption ? `<div class="file-caption" title="${item.caption}">${item.caption}</div>` : '';

        return `
          <div class="card ${item.is_duplicate ? 'is-dup' : ''}" id="card-${item.id}">
            ${previewHtml}
            <div class="card-body">
              <div class="file-title" title="${item.file_name}">${item.file_name}</div>
              <div class="badges">
                <span class="badge size">${item.size_str}</span>
                <span class="badge">${item.ext ? item.ext.toUpperCase() : item.category.toUpperCase()}</span>
                <span class="badge">${item.date_str}</span>
                ${dupBadge}
              </div>
              ${captionHtml}
            </div>
            <div class="card-actions">
              <a href="/api/download/${item.id}" class="btn-act btn-download" title="Download on-demand">
                ⬇️ Unduh
              </a>
              <a href="${item.link}" target="_blank" rel="noopener noreferrer" class="btn-act btn-tg" title="Buka di Telegram">
                🚀 Tele
              </a>
              <button onclick="deleteFile(${item.id}, '${item.file_name.replace(/'/g, "\\'")}')" class="btn-act btn-del" title="Hapus dari Cloud Telegram">
                🗑️ Hapus
              </button>
            </div>
          </div>
        `;
      }).join('');

      grid.insertAdjacentHTML('beforeend', html);

      const btnMore = document.getElementById('btnLoadMore');
      if (displayedCount < filteredItems.length) {
        btnMore.style.display = 'inline-block';
      } else {
        btnMore.style.display = 'none';
      }
    }

    // Lightbox
    function openModal(src) {
      document.getElementById('modalImg').src = src;
      document.getElementById('imageModal').style.display = 'flex';
    }
    function closeModal() {
      document.getElementById('imageModal').style.display = 'none';
    }

    // Delete single file
    async function deleteFile(id, name) {
      if (!confirm(`Hapus permanen '${name}' dari Cloud Telegram?\\nPesan ini akan dihapus dari grup Anda.`)) {
        return;
      }
      try {
        const res = await fetch('/api/delete/' + id, { method: 'POST' });
        const data = await res.json();
        if (data.ok) {
          // Remove card from UI
          const el = document.getElementById('card-' + id);
          if (el) el.remove();
          allItems = allItems.filter(i => i.id !== id);
          filteredItems = filteredItems.filter(i => i.id !== id);
          document.getElementById('resultsCount').textContent = `File #${id} berhasil dihapus dari cloud Telegram!`;
        } else {
          alert('Gagal menghapus: ' + data.error);
        }
      } catch (err) {
        alert('Error: ' + err);
      }
    }

    // Batch delete duplicates
    async function cleanAllDuplicates() {
      if (!confirm('Apakah Anda yakin ingin MENGHAPUS SEMUA SALINAN DUPLIKAT?\\n\\nFile versi asli (tertua) akan TETAP AMAN. Hanya salinan kopiannya yang akan dihapus dari Cloud Telegram.')) {
        return;
      }
      const banner = document.getElementById('dupBanner');
      banner.innerHTML = '<div style="color:#fbbf24;font-weight:600;">⏳ Sedang menghapus salinan duplikat dari Cloud Telegram... Mohon tunggu...</div>';

      try {
        const res = await fetch('/api/delete_duplicates_batch', { method: 'POST' });
        const data = await res.json();
        if (data.ok) {
          alert(`Sukses! ${data.deleted_count} file duplikat telah dibersihkan.\\nKapasitas cloud berhasil dihemat: ${data.freed_str}`);
          window.location.reload();
        } else {
          alert('Gagal membersihkan: ' + data.error);
        }
      } catch (err) {
        alert('Error: ' + err);
      }
    }

    // Infinite scroll
    window.addEventListener('scroll', () => {
      if (window.innerHeight + window.scrollY >= document.body.offsetHeight - 500) {
        if (displayedCount < filteredItems.length) {
          loadMore();
        }
      }
    });

    // Event listeners
    document.getElementById('searchInput').addEventListener('input', (e) => {
      searchQuery = e.target.value;
      applyFilters();
    });
    document.getElementById('sortSelect').addEventListener('change', (e) => {
      currentSort = e.target.value;
      applyFilters();
    });
    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.cat;
        applyFilters();
      });
    });

    init();
  </script>
</body>
</html>
"""
    return web.Response(text=html, content_type='text/html')

def create_app():
    app = web.Application(client_max_size=1024**3)
    app.router.add_get('/', handle_index)
    app.router.add_get('/api/catalog', handle_get_catalog)
    app.router.add_get('/api/thumb/{msg_id}', handle_thumbnail)
    app.router.add_get('/api/download/{msg_id}', handle_download)
    app.router.add_post('/api/delete/{msg_id}', handle_delete)
    app.router.add_post('/api/delete_duplicates_batch', handle_batch_delete_duplicates)
    return app

async def start_server():
    load_catalog()
    print("Menghubungkan ke Telegram...")
    await init_telegram()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 8080)
    await site.start()

    print("\n" + "=" * 65)
    print("🚀 TELEGRAM CLOUD DASHBOARD AKTIF!")
    print("👉 Buka di browser laptop Anda: http://localhost:8080")
    print("=" * 65)
    print("Fitur:")
    print("- 🖼️  Preview Visual (Thumbnail langsung dari cloud)")
    print("- ⬇️  Download on-demand (Hanya download yang diinginkan)")
    print("- 🗑️  Hapus dari Cloud (Hapus file tunggal langsung dari Telegram)")
    print("- ⚡  Hapus Semua Duplikat (Bersihkan 1.8 GB salinan sekaligus)")
    print("\nTekan Ctrl+C untuk menghentikan server.\n")

    # Keep running forever
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(start_server())
    except (KeyboardInterrupt, SystemExit):
        print("\nServer dihentikan.")

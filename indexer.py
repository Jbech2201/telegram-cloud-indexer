#!/usr/bin/env python3
"""
Telegram Cloud Indexer & Search Dashboard Generator
Author: Antigravity
Desc: Scans a private Telegram group/chat for media and generates an interactive,
      lightweight offline HTML search engine without downloading files to your hard drive.
"""

import os
import sys
import json
import asyncio
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
    Channel,
    Chat
)

CONFIG_FILE = "config.json"
CATALOG_JSON = "catalog.json"
HTML_OUTPUT = "katalog.html"

def format_size(bytes_size):
    if not bytes_size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} PB"

def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def detect_category(extension, mime_type, media_kind):
    ext = (extension or "").lower().strip(".")
    mime = (mime_type or "").lower()

    if media_kind == "photo" or ext in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "heic", "svg"]:
        return "photo"
    if media_kind == "video" or ext in ["mp4", "mkv", "avi", "mov", "webm", "flv", "m4v"]:
        return "video"
    if media_kind == "audio" or ext in ["mp3", "m4a", "wav", "flac", "ogg", "aac", "opus"]:
        return "audio"
    if ext in ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "epub"]:
        return "document"
    if ext in ["zip", "rar", "7z", "tar", "gz", "bz2"]:
        return "archive"
    
    if "pdf" in mime or "officedocument" in mime or "text" in mime or "document" in mime:
        return "document"
    if "video" in mime:
        return "video"
    if "image" in mime:
        return "photo"
    if "audio" in mime:
        return "audio"
        
    return "other"

def detect_duplicates(items):
    from collections import defaultdict
    by_size = defaultdict(list)
    for it in items:
        if it.get('size_bytes', 0) > 0:
            by_size[it['size_bytes']].append(it)

    dup_count = 0
    wasted_bytes = 0
    group_idx = 1

    for sz, group in by_size.items():
        if len(group) > 1:
            group.sort(key=lambda x: x['timestamp'])
            group_id = f"g_{group_idx}"
            group_idx += 1
            dup_count += len(group)
            wasted_bytes += sz * (len(group) - 1)
            for idx, item in enumerate(group):
                item['is_duplicate'] = True
                item['dup_group_id'] = group_id
                item['is_original'] = (idx == 0)
                item['dup_copies'] = len(group)
        else:
            group[0]['is_duplicate'] = False
            group[0]['dup_group_id'] = None
            group[0]['is_original'] = False
            group[0]['dup_copies'] = 1

    return {
        'total_duplicate_files': dup_count,
        'total_groups': group_idx - 1,
        'wasted_bytes': wasted_bytes,
        'wasted_str': format_size(wasted_bytes)
    }

def generate_html(catalog_data, group_name):
    items_json = json.dumps(catalog_data["items"])
    total_files = catalog_data["total_files"]
    total_bytes = catalog_data["total_bytes_str"]
    breakdown = catalog_data["breakdown"]
    generated_at = catalog_data["generated_at"]
    dup_info = catalog_data.get("duplicates_summary", {
        "total_duplicate_files": 0,
        "wasted_str": "0 B"
    })
    dup_files = dup_info.get("total_duplicate_files", 0)
    dup_wasted = dup_info.get("wasted_str", "0 B")

    html_content = f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Katalog Telegram Cloud: {group_name}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-hover: #273548;
      --accent: #38bdf8;
      --accent-hover: #0ea5e9;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --border: #334155;
      --badge-bg: #334155;
      --warning: #f59e0b;
      --warning-bg: #78350f;
      --success: #10b981;
      --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      background-color: var(--bg);
      color: var(--text-main);
      font-family: var(--font-family);
      line-height: 1.5;
      padding: 24px;
    }}
    .container {{
      max-width: 1280px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }}
    .header-title h1 {{
      font-size: 24px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
      color: #38bdf8;
    }}
    .header-title p {{
      color: var(--text-muted);
      font-size: 14px;
      margin-top: 4px;
    }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .stat-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      text-align: center;
    }}
    .stat-card.warning {{
      border-color: #f59e0b;
      background: #1c1917;
    }}
    .stat-value {{
      font-size: 22px;
      font-weight: 700;
      color: #38bdf8;
    }}
    .stat-card.warning .stat-value {{
      color: #fbbf24;
    }}
    .stat-label {{
      font-size: 13px;
      color: var(--text-muted);
      margin-top: 2px;
    }}
    .control-panel {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 24px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}
    .search-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .search-input {{
      flex: 1;
      min-width: 250px;
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 16px;
      color: var(--text-main);
      font-size: 15px;
      outline: none;
      transition: border-color 0.2s;
    }}
    .search-input:focus {{
      border-color: var(--accent);
    }}
    .select-dropdown {{
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 14px;
      color: var(--text-main);
      font-size: 14px;
      outline: none;
      cursor: pointer;
    }}
    .filters-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}
    .filter-btn {{
      background: #0f172a;
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 6px 14px;
      color: var(--text-muted);
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }}
    .filter-btn:hover, .filter-btn.active {{
      background: var(--accent);
      color: #0f172a;
      font-weight: 600;
      border-color: var(--accent);
    }}
    .filter-btn.warning:hover, .filter-btn.warning.active {{
      background: #f59e0b;
      color: #0f172a;
      border-color: #f59e0b;
      font-weight: 700;
    }}
    .results-count {{
      font-size: 14px;
      color: var(--text-muted);
      margin-bottom: 12px;
    }}
    .grid-container {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
      gap: 16px;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.15s, border-color 0.15s;
    }}
    .card.is-dup {{
      border-left: 4px solid #f59e0b;
    }}
    .card:hover {{
      transform: translateY(-2px);
      border-color: var(--accent);
    }}
    .card-top {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .icon-box {{
      width: 44px;
      height: 44px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      background: #0f172a;
      flex-shrink: 0;
    }}
    .card-info {{
      flex: 1;
      overflow: hidden;
    }}
    .file-name {{
      font-size: 15px;
      font-weight: 600;
      color: var(--text-main);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 4px;
    }}
    .file-caption {{
      font-size: 13px;
      color: var(--text-muted);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      margin-bottom: 8px;
      font-style: italic;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
    }}
    .badge {{
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 6px;
      background: var(--badge-bg);
      color: #cbd5e1;
    }}
    .badge.size {{
      background: #0369a1;
      color: #e0f2fe;
      font-weight: 600;
    }}
    .badge.original {{
      background: #065f46;
      color: #a7f3d0;
      font-weight: 600;
    }}
    .badge.copy {{
      background: #78350f;
      color: #fde68a;
      font-weight: 600;
    }}
    .btn-open {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      width: 100%;
      background: #0284c7;
      color: white;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      padding: 8px 12px;
      border-radius: 8px;
      transition: background 0.2s;
    }}
    .btn-open:hover {{
      background: var(--accent-hover);
    }}
    .no-results {{
      text-align: center;
      padding: 48px;
      color: var(--text-muted);
      grid-column: 1 / -1;
      font-size: 16px;
    }}
    @media (max-width: 640px) {{
      body {{ padding: 12px; }}
      .grid-container {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="header-title">
        <h1>☁️ Katalog Cloud Telegram</h1>
        <p>Grup: <strong>{group_name}</strong> • Diperbarui: {generated_at}</p>
      </div>
    </header>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-value">{total_files}</div>
        <div class="stat-label">Total File</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{total_bytes}</div>
        <div class="stat-label">Total Kapasitas Cloud</div>
      </div>
      <div class="stat-card warning">
        <div class="stat-value">{dup_files}</div>
        <div class="stat-label">⚠️ Duplikat ({dup_wasted} Sia-sia)</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{breakdown.get('document', 0)}</div>
        <div class="stat-label">📄 Dokumen</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{breakdown.get('photo', 0)}</div>
        <div class="stat-label">📸 Foto</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{breakdown.get('video', 0)}</div>
        <div class="stat-label">🎬 Video</div>
      </div>
    </div>

    <div class="control-panel">
      <div class="search-row">
        <input type="text" id="searchInput" class="search-input" placeholder="🔍 Cari nama file, keterangan/caption, atau ekstensi..." autofocus>
        <select id="sortSelect" class="select-dropdown">
          <option value="newest">📅 Tanggal: Terbaru</option>
          <option value="oldest">📅 Tanggal: Terlama</option>
          <option value="largest">💾 Ukuran: Terbesar</option>
          <option value="smallest">💾 Ukuran: Terkecil</option>
          <option value="name_asc">🔤 Nama: A - Z</option>
        </select>
      </div>

      <div class="filters-row">
        <span style="font-size: 13px; color: var(--text-muted); margin-right: 4px;">Kategori:</span>
        <button class="filter-btn active" data-cat="all">Semua ({total_files})</button>
        <button class="filter-btn warning" data-cat="duplicate">⚠️ Duplikat ({dup_files})</button>
        <button class="filter-btn" data-cat="document">📄 Dokumen ({breakdown.get('document', 0)})</button>
        <button class="filter-btn" data-cat="photo">📸 Foto ({breakdown.get('photo', 0)})</button>
        <button class="filter-btn" data-cat="video">🎬 Video ({breakdown.get('video', 0)})</button>
        <button class="filter-btn" data-cat="audio">🎵 Audio ({breakdown.get('audio', 0)})</button>
        <button class="filter-btn" data-cat="archive">📦 Arsip ({breakdown.get('archive', 0)})</button>
      </div>
    </div>

    <div id="resultsCount" class="results-count">Menampilkan semua item...</div>
    <div id="itemsGrid" class="grid-container"></div>
  </div>

  <script>
    const items = {items_json};
    let currentFilter = 'all';
    let searchQuery = '';
    let currentSort = 'newest';

    const icons = {{
      document: '📄',
      photo: '📸',
      video: '🎬',
      audio: '🎵',
      archive: '📦',
      other: '📁'
    }};

    function render() {{
      const grid = document.getElementById('itemsGrid');
      const resultsCount = document.getElementById('resultsCount');

      const filtered = items.filter(item => {{
        let matchesCategory = false;
        if (currentFilter === 'all') {{
          matchesCategory = true;
        }} else if (currentFilter === 'duplicate') {{
          matchesCategory = !!item.is_duplicate;
        }} else {{
          matchesCategory = (item.category === currentFilter);
        }}

        const q = searchQuery.toLowerCase().trim();
        const matchesSearch = !q || 
          item.file_name.toLowerCase().includes(q) || 
          (item.caption && item.caption.toLowerCase().includes(q)) ||
          item.ext.toLowerCase().includes(q) ||
          item.date_str.toLowerCase().includes(q);
        return matchesCategory && matchesSearch;
      }});

      filtered.sort((a, b) => {{
        if (currentFilter === 'duplicate') {{
          // Group duplicates together by dup_group_id, then by timestamp
          if (a.dup_group_id !== b.dup_group_id) {{
            return b.size_bytes - a.size_bytes;
          }}
          return a.timestamp - b.timestamp;
        }}
        if (currentSort === 'newest') return b.timestamp - a.timestamp;
        if (currentSort === 'oldest') return a.timestamp - b.timestamp;
        if (currentSort === 'largest') return b.size_bytes - a.size_bytes;
        if (currentSort === 'smallest') return a.size_bytes - b.size_bytes;
        if (currentSort === 'name_asc') return a.file_name.localeCompare(b.file_name);
        return 0;
      }});

      resultsCount.textContent = `Menampilkan ${{filtered.length}} dari ${{items.length}} item`;

      if (filtered.length === 0) {{
        grid.innerHTML = `<div class="no-results">Tidak ada file yang cocok dengan pencarian "<strong>${{searchQuery}}</strong>".</div>`;
        return;
      }}

      grid.innerHTML = filtered.map(item => {{
        const icon = icons[item.category] || '📁';
        const captionHtml = item.caption ? `<div class="file-caption" title="${{item.caption}}">${{item.caption}}</div>` : '';
        
        let dupBadge = '';
        if (item.is_duplicate) {{
          if (item.is_original) {{
            dupBadge = `<span class="badge original">⭐ VERSI ASLI (1/${{item.dup_copies || 2}})</span>`;
          }} else {{
            dupBadge = `<span class="badge copy">⚠️ SALINAN DUPLIKAT</span>`;
          }}
        }}

        return `
          <div class="card ${{item.is_duplicate ? 'is-dup' : ''}}">
            <div>
              <div class="card-top">
                <div class="icon-box">${{icon}}</div>
                <div class="card-info">
                  <div class="file-name" title="${{item.file_name}}">${{item.file_name}}</div>
                  <div class="badges">
                    <span class="badge size">${{item.size_str}}</span>
                    <span class="badge">${{item.ext ? item.ext.toUpperCase() : item.category.toUpperCase()}}</span>
                    <span class="badge">${{item.date_str}}</span>
                    ${{dupBadge}}
                  </div>
                </div>
              </div>
              ${{captionHtml}}
            </div>
            <a href="${{item.link}}" target="_blank" rel="noopener noreferrer" class="btn-open">
              🚀 Buka di Telegram
            </a>
          </div>
        `;
      }}).join('');
    }}

    document.getElementById('searchInput').addEventListener('input', (e) => {{
      searchQuery = e.target.value;
      render();
    }});

    document.getElementById('sortSelect').addEventListener('change', (e) => {{
      currentSort = e.target.value;
      render();
    }});

    document.querySelectorAll('.filter-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentFilter = btn.dataset.cat;
        render();
      }});
    }});

    render();
  </script>
</body>
</html>
"""
    with open(HTML_OUTPUT, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n[OK] Dashboard HTML berhasil dibuat: {HTML_OUTPUT}")

async def main():
    print("=" * 60)
    print("     TELEGRAM CLOUD INDEXER & SEARCH DASHBOARD      ")
    print("=" * 60)
    print("Script ini HANYA membaca metadata (nama, tanggal, ukuran, link).")
    print("FILE ASLI TETAP 100% DI CLOUD TELEGRAM (TIDAK DIDOWNLOAD).\n")

    cfg = get_config()
    api_id = cfg.get("api_id")
    api_hash = cfg.get("api_hash")

    if not api_id or not api_hash:
        print("Kredensial Telegram API belum ditemukan.")
        print("Silakan dapatkan API ID & API Hash secara gratis dari:")
        print("👉 https://my.telegram.org (Menu: API development tools)\n")
        try:
            api_id = input("Masukkan API ID   : ").strip()
            api_hash = input("Masukkan API HASH : ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nDibatalkan.")
            return

        if not api_id or not api_hash:
            print("Error: API ID dan API Hash wajib diisi!")
            return
        
        cfg["api_id"] = int(api_id)
        cfg["api_hash"] = api_hash
        save_config(cfg)

    session_path = os.path.join(os.path.dirname(__file__), "telegram_indexer.session")
    client = TelegramClient(session_path, cfg["api_id"], cfg["api_hash"])

    print("\nMenghubungkan ke Telegram...")
    await client.start()
    print("[OK] Berhasil login ke akun Telegram!")

    print("\nMencari daftar grup & chat Anda...")
    dialogs = []
    async for dialog in client.iter_dialogs(limit=40):
        # We focus on Groups and Channels
        if dialog.is_group or dialog.is_channel:
            dialogs.append(dialog)

    if not dialogs:
        print("Tidak ditemukan grup atau channel di akun Anda.")
        return

    print("\nPILIH GRUP PENYIMPANAN ANDA:")
    print("-" * 50)
    for idx, d in enumerate(dialogs, 1):
        unread = f" ({d.unread_count} unread)" if d.unread_count else ""
        print(f"[{idx:2d}] {d.title}{unread}")
    print("-" * 50)

    selected_dialog = None
    while not selected_dialog:
        try:
            choice = input(f"Pilih nomor grup (1-{len(dialogs)}) atau ketik nama: ").strip()
            if not choice:
                continue
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(dialogs):
                    selected_dialog = dialogs[idx - 1]
                else:
                    print("Nomor tidak valid.")
            else:
                # Cari berdasarkan nama grup
                matches = [d for d in dialogs if choice.lower() in d.title.lower()]
                if len(matches) == 1:
                    selected_dialog = matches[0]
                    print(f"-> Memilih: {selected_dialog.title}")
                elif len(matches) > 1:
                    print(f"Ditemukan beberapa grup yang cocok dengan '{choice}':")
                    for m in matches:
                        print(f" - [{dialogs.index(m) + 1}] {m.title}")
                    print("Silakan masukkan nomor grup yang lebih spesifik.")
                else:
                    print(f"Tidak ditemukan grup dengan nama '{choice}'.")
        except (KeyboardInterrupt, EOFError):
            print("\nDibatalkan.")
            return

    target_entity = selected_dialog.entity
    group_name = selected_dialog.title
    print(f"\nMemulai indexing untuk grup: '{group_name}'...")
    print("Memindai pesan cloud Telegram (proses ini cepat dan tidak mendownload file)...")

    # Format base link for messages
    raw_id = str(abs(target_entity.id))
    if raw_id.startswith("100") and len(raw_id) > 10:
        channel_id = raw_id[3:]
    else:
        channel_id = raw_id

    username = getattr(target_entity, 'username', None)

    items = []
    total_bytes = 0
    breakdown = {"document": 0, "photo": 0, "video": 0, "audio": 0, "archive": 0, "other": 0}
    scanned_count = 0

    async for msg in client.iter_messages(target_entity):
        scanned_count += 1
        if scanned_count % 50 == 0:
            print(f"-> Telah memindai {scanned_count} pesan...", end="\r", flush=True)

        if not msg.media:
            continue

        file_size = 0
        file_name = ""
        mime_type = ""
        media_kind = "other"
        ext = ""

        try:
            # Check media types
            if isinstance(msg.media, MessageMediaPhoto) or msg.photo:
                media_kind = "photo"
                file_name = f"photo_{msg.date.strftime('%Y%m%d_%H%M%S')}_{msg.id}.jpg"
                ext = "jpg"
                if msg.file and hasattr(msg.file, 'size') and msg.file.size:
                    file_size = msg.file.size
            elif isinstance(msg.media, MessageMediaDocument) or msg.document:
                doc = msg.media.document if hasattr(msg.media, 'document') else msg.document
                if doc:
                    file_size = doc.size or 0
                    mime_type = getattr(doc, 'mime_type', "") or ""
                    # Get filename from attributes
                    if hasattr(doc, 'attributes') and doc.attributes:
                        for attr in doc.attributes:
                            if isinstance(attr, DocumentAttributeFilename):
                                file_name = attr.file_name
                            elif isinstance(attr, DocumentAttributeVideo):
                                media_kind = "video"
                            elif isinstance(attr, DocumentAttributeAudio):
                                media_kind = "audio"
                    
                    if not file_name:
                        guessed_ext = mime_type.split("/")[-1] if "/" in mime_type else "bin"
                        file_name = f"doc_{msg.date.strftime('%Y%m%d_%H%M%S')}_{msg.id}.{guessed_ext}"
                    
                    if "." in file_name:
                        ext = file_name.rsplit(".", 1)[-1].lower()
            else:
                if msg.file:
                    file_size = getattr(msg.file, 'size', 0) or 0
                    file_name = getattr(msg.file, 'name', None) or f"file_{msg.id}"
                    file_ext = getattr(msg.file, 'ext', None)
                    ext = file_ext.strip(".") if file_ext else ""

            if not file_name:
                continue

            cat = detect_category(ext, mime_type, media_kind)
            breakdown[cat] = breakdown.get(cat, 0) + 1
            total_bytes += file_size

            # Direct deep link
            if username:
                link = f"https://t.me/{username}/{msg.id}"
            else:
                link = f"https://t.me/c/{channel_id}/{msg.id}"

            caption = (msg.message or "").strip()

            items.append({
                "id": msg.id,
                "file_name": file_name,
                "ext": ext,
                "category": cat,
                "size_bytes": file_size,
                "size_str": format_size(file_size),
                "timestamp": int(msg.date.timestamp()),
                "date_str": msg.date.strftime("%Y-%m-%d %H:%M"),
                "caption": caption,
                "link": link
            })
        except Exception as item_err:
            # Skip any corrupt single message without crashing
            continue

    print(f"\n[SELESAI] Total {scanned_count} pesan diperiksa.")
    print(f"Ditemukan {len(items)} file media.")
    print(f"Total kapasitas di cloud: {format_size(total_bytes)}")

    # Deteksi duplikat otomatis
    dup_info = detect_duplicates(items)
    print(f"[DUPLIKAT] Ditemukan {dup_info['total_duplicate_files']} file duplikat dalam {dup_info['total_groups']} kelompok.")
    print(f"[DUPLIKAT] Potensi kapasitas yang terbuang: {dup_info['wasted_str']}")

    catalog_data = {
        "group_name": group_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": len(items),
        "total_bytes": total_bytes,
        "total_bytes_str": format_size(total_bytes),
        "breakdown": breakdown,
        "duplicates_summary": dup_info,
        "items": items
    }

    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog_data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Database metadata tersimpan di: {CATALOG_JSON}")

    generate_html(catalog_data, group_name)
    print("\n" + "=" * 60)
    print(f"Silakan buka file '{HTML_OUTPUT}' di browser laptop kamu!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())

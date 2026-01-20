from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import uuid
import subprocess
import requests
from pathlib import Path
import logging
import threading
import time
import json

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Directories
UPLOAD_FOLDER = '/tmp/uploads'
OUTPUT_FOLDER = '/tmp/outputs'
JOB_STORAGE_FILE = '/tmp/job_status.json'

Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
Path(OUTPUT_FOLDER).mkdir(parents=True, exist_ok=True)

# Global dictionary to store job status
job_status = {}

def save_jobs():
    """Save job status to file"""
    try:
        with open(JOB_STORAGE_FILE, 'w') as f:
            json.dump(job_status, f)
        logger.info(f"Saved {len(job_status)} jobs to disk")
    except Exception as e:
        logger.error(f"Failed to save jobs: {e}")

def load_jobs():
    """Load job status from file"""
    global job_status
    try:
        if os.path.exists(JOB_STORAGE_FILE):
            with open(JOB_STORAGE_FILE, 'r') as f:
                job_status = json.load(f)
            logger.info(f"Loaded {len(job_status)} jobs from disk")
    except Exception as e:
        logger.error(f"Failed to load jobs: {e}")
        job_status = {}

# Load existing jobs on startup
load_jobs()

@app.route('/')
def index():
    """Root endpoint"""
    return jsonify({
        "service": "Islamic Reels Video Processing API",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "create_reel": "/create-reel (POST)",
            "job_status": "/job-status/<job_id> (GET)"
        }
    }), 200

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "Islamic Reels Video Processing API",
        "version": "3.3.0",
        "optimizations": "30s max, 5min timeout, file-based persistence",
        "platform": "Render.com",
        "active_jobs": len(job_status)
    }), 200

@app.route('/create-reel', methods=['POST'])
def create_reel():
    """Main endpoint to create Instagram Reel - Async version"""
    try:
        data = request.json
        logger.info(f"Received request: {data}")
        
        # Extract parameters
        video_id = data.get('videoId')
        music_id = data.get('musicId')
        overlays = data.get('overlays', {})
        max_duration = int(data.get('maxDuration', 60))
        
        if not video_id or not music_id:
            return jsonify({"status": "error", "message": "Missing videoId or musicId"}), 400
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())[:8]
        logger.info(f"Job ID: {job_id}")
        
        # Initialize job status
        job_status[job_id] = {
            "status": "processing",
            "progress": 0,
            "message": "Job started",
            "timestamp": time.time()
        }
        save_jobs()  # SAVE TO FILE
        
        # Start processing in background thread
        thread = threading.Thread(
            target=process_reel_async,
            args=(job_id, video_id, music_id, overlays, max_duration, request.url_root)
        )
        thread.daemon = True
        thread.start()
        
        # Return immediately with job ID
        return jsonify({
            "status": "processing",
            "jobId": job_id,
            "statusUrl": f"{request.url_root}job-status/{job_id}",
            "message": "Video processing started"
        }), 202
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def process_reel_async(job_id, video_id, music_id, overlays, max_duration, base_url):
    """Background processing function"""
    try:
        job_status[job_id] = {
            "status": "downloading",
            "progress": 20,
            "message": "Downloading files...",
            "timestamp": time.time()
        }
        save_jobs()
        
        # Download files
        video_path = download_google_drive(video_id, f"{UPLOAD_FOLDER}/video_{job_id}.mp4")
        music_path = download_google_drive(music_id, f"{UPLOAD_FOLDER}/music_{job_id}.mp3")
        
        if not video_path or not music_path:
            job_status[job_id] = {
                "status": "error",
                "progress": 0,
                "message": "Download failed",
                "timestamp": time.time()
            }
            save_jobs()
            return
        
        job_status[job_id] = {
            "status": "processing",
            "progress": 50,
            "message": "Processing video...",
            "timestamp": time.time()
        }
        save_jobs()
        
        # Get durations
        video_dur = get_duration(video_path)
        music_dur = get_duration(music_path)
        actual_max = min(max_duration, 30)
        final_dur = min(video_dur, music_dur, actual_max)

        if final_dur <= 0:
            final_dur = 20
        
        logger.info(f"Durations - Video: {video_dur}s, Music: {music_dur}s, Final: {final_dur}s")
        
        # Process video
        output_path = f"{OUTPUT_FOLDER}/reel_{job_id}.mp4"
        success = process_video(video_path, music_path, output_path, overlays, final_dur)
        
        if not success:
            job_status[job_id] = {
                "status": "error",
                "progress": 0,
                "message": "Processing failed",
                "timestamp": time.time()
            }
            save_jobs()
            cleanup([video_path, music_path])
            return
        
        # Generate public URL
        public_url = f"{base_url}outputs/reel_{job_id}.mp4"
        
        # Update final status
        job_status[job_id] = {
            "status": "completed",
            "progress": 100,
            "videoUrl": public_url,
            "duration": final_dur,
            "audioReplaced": True,
            "message": "Video processing completed successfully",
            "timestamp": time.time()
        }
        save_jobs()
        
        # Cleanup source files
        cleanup([video_path, music_path])
        
        logger.info(f"Job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Async processing error: {str(e)}")
        job_status[job_id] = {
            "status": "error",
            "progress": 0,
            "message": str(e),
            "timestamp": time.time()
        }
        save_jobs()


def cleanup_old_jobs():
    """Clean up jobs older than 2 hours"""
    current_time = time.time()
    jobs_to_delete = []
    
    for job_id, status in job_status.items():
        if 'timestamp' in status:
            if current_time - status['timestamp'] > 7200:
                jobs_to_delete.append(job_id)
    
    for job_id in jobs_to_delete:
        del job_status[job_id]
        logger.info(f"Cleaned up old job: {job_id}")
    
    if jobs_to_delete:
        save_jobs()


@app.route('/job-status/<job_id>')
def get_job_status(job_id):
    """Get status of a processing job"""
    cleanup_old_jobs()
    
    if job_id not in job_status:
        return jsonify({
            "status": "error",
            "message": "Job not found or expired (jobs kept for 2 hours)"
        }), 404
    
    return jsonify(job_status[job_id]), 200


def download_google_drive(file_id, output_path):
    """Download file from Google Drive"""
    try:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        logger.info(f"Downloading: {url}")
        
        response = requests.get(url, stream=True, timeout=90)
        
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"Downloaded successfully: {output_path}")
            return output_path
        else:
            logger.error(f"Download failed with status: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return None


def get_duration(file_path):
    """Get media duration"""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        duration_str = result.stdout.strip()
        return float(duration_str) if duration_str else 20.0
    except Exception as e:
        logger.error(f"Duration detection error: {str(e)}")
        return 20.0


def process_video(video_path, music_path, output_path, overlays, duration):
    """Process video with FFmpeg"""
    try:
        import tempfile
        
        def create_text_file(text):
            if not text:
                return None
            fd, path = tempfile.mkstemp(suffix='.txt', dir=UPLOAD_FOLDER)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            return path
        
        top_text = overlays.get('top', {}).get('text', '')
        center_text = overlays.get('center', {}).get('text', '')
        bottom_text = overlays.get('bottom', {}).get('text', '')
        
        top_file = create_text_file(top_text)
        center_file = create_text_file(center_text)
        bottom_file = create_text_file(bottom_text)
        
        video_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        
        if top_file:
            video_filter += f",drawtext=textfile='{top_file}':fontsize=24:fontcolor=white:x=(w-text_w)/2:y=60:box=1:boxcolor=black@0.5:boxborderw=8"
        
        if center_file:
            video_filter += f",drawtext=textfile='{center_file}':fontsize=40:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.65:boxborderw=12"
        
        if bottom_file:
            video_filter += f",drawtext=textfile='{bottom_file}':fontsize=20:fontcolor=white:x=(w-text_w)/2:y=h-80:box=1:boxcolor=black@0.5:boxborderw=8"
        
        cmd = [
            'ffmpeg', '-i', video_path, '-i', music_path, '-t', str(duration),
            '-vf', video_filter, '-map', '0:v', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
            '-c:a', 'aac', '-b:a', '128k', '-shortest', '-y', output_path
        ]
        
        logger.info(f"Starting FFmpeg processing for {duration}s video...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        for f in [top_file, center_file, bottom_file]:
            if f and os.path.exists(f):
                os.remove(f)
        
        if result.returncode == 0:
            logger.info(f"Processing successful: {output_path}")
            return True
        else:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Processing error: {str(e)}")
        return False


def cleanup(files):
    """Delete files"""
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
                logger.info(f"Cleaned up: {f}")
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")


@app.route('/outputs/<filename>')
def serve_output(filename):
    """Serve processed video"""
    file_path = f"{OUTPUT_FOLDER}/{filename}"
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='video/mp4')
    return jsonify({"error": "File not found"}), 404


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Starting server on port {port} - Version 3.3.0 with file persistence")
    app.run(host='0.0.0.0', port=port, debug=False)

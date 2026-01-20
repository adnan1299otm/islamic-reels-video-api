from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import uuid
import subprocess
import requests
from pathlib import Path
import logging

app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Directories
UPLOAD_FOLDER = '/tmp/uploads'
OUTPUT_FOLDER = '/tmp/outputs'
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
Path(OUTPUT_FOLDER).mkdir(parents=True, exist_ok=True)

@app.route('/')
def index():
    """Root endpoint"""
    return jsonify({
        "service": "Islamic Reels Video Processing API",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "create_reel": "/create-reel (POST)"
        }
    }), 200

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "Islamic Reels Video Processing API",
        "version": "2.0.0",
        "platform": "Render.com"
    }), 200

@app.route('/create-reel', methods=['POST'])
def create_reel():
    """Main endpoint to create Instagram Reel"""
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
        
        # Download from Google Drive
        video_path = download_google_drive(video_id, f"{UPLOAD_FOLDER}/video_{job_id}.mp4")
        music_path = download_google_drive(music_id, f"{UPLOAD_FOLDER}/music_{job_id}.mp3")
        
        if not video_path or not music_path:
            return jsonify({"status": "error", "message": "Download failed"}), 400
        
        # Get durations
        video_dur = get_duration(video_path)
        music_dur = get_duration(music_path)
        final_dur = min(video_dur, music_dur, max_duration)
        
        if final_dur <= 0:
            final_dur = 30
        
        logger.info(f"Durations - Video: {video_dur}, Music: {music_dur}, Final: {final_dur}")
        
        # Process video
        output_path = f"{OUTPUT_FOLDER}/reel_{job_id}.mp4"
        success = process_video(video_path, music_path, output_path, overlays, final_dur)
        
        if not success:
            return jsonify({"status": "error", "message": "Processing failed"}), 500
        
        # Generate public URL
        public_url = f"{request.url_root}outputs/reel_{job_id}.mp4"
        
        # Cleanup
        cleanup([video_path, music_path])
        
        return jsonify({
            "status": "success",
            "videoUrl": public_url,
            "duration": final_dur,
            "audioReplaced": True,
            "jobId": job_id
        }), 200
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def download_google_drive(file_id, output_path):
    """Download file from Google Drive"""
    try:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        logger.info(f"Downloading: {url}")
        
        response = requests.get(url, stream=True, timeout=60)
        
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        duration_str = result.stdout.strip()
        return float(duration_str) if duration_str else 30.0
    except Exception as e:
        logger.error(f"Duration detection error: {str(e)}")
        return 30.0


def process_video(video_path, music_path, output_path, overlays, duration):
    """Process video with FFmpeg"""
    try:
        import tempfile
        
        def create_text_file(text):
            """Create temporary file with text"""
            if not text:
                return None
            fd, path = tempfile.mkstemp(suffix='.txt', dir=UPLOAD_FOLDER)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            return path
        
        # Extract texts
        top_text = overlays.get('top', {}).get('text', '')
        center_text = overlays.get('center', {}).get('text', '')
        bottom_text = overlays.get('bottom', {}).get('text', '')
        
        # Create text files
        top_file = create_text_file(top_text)
        center_file = create_text_file(center_text)
        bottom_file = create_text_file(bottom_text)
        
        # Build filter
        video_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        
        if top_file:
            video_filter += f",drawtext=textfile='{top_file}':fontsize=26:fontcolor=white:x=(w-text_w)/2:y=80:box=1:boxcolor=black@0.5:boxborderw=10"
        
        if center_file:
            video_filter += f",drawtext=textfile='{center_file}':fontsize=44:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.65:boxborderw=15"
        
        if bottom_file:
            video_filter += f",drawtext=textfile='{bottom_file}':fontsize=22:fontcolor=white:x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.5:boxborderw=10"
        
        # FFmpeg command
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', music_path,
            '-t', str(duration),
            '-vf', video_filter,
            '-map', '0:v',
            '-map', '1:a',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '28',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-shortest',
            '-y',
            output_path
        ]
        
        logger.info("Starting FFmpeg processing...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        # Cleanup text files
        for f in [top_file, center_file, bottom_file]:
            if f and os.path.exists(f):
                os.remove(f)
        
        if result.returncode == 0:
            logger.info(f"Processing successful: {output_path}")
            return True
        else:
            logger.error(f"FFmpeg stderr: {result.stderr}")
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
    logger.info(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

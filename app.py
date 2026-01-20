from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import uuid
import subprocess
import requests
from pathlib import Path

app = Flask(__name__)
CORS(app)

# Directories
UPLOAD_FOLDER = '/tmp/uploads'
OUTPUT_FOLDER = '/tmp/outputs'
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
Path(OUTPUT_FOLDER).mkdir(parents=True, exist_ok=True)

@app.route('/health', methods=['GET'])
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
        
        # Extract parameters
        video_id = data.get('videoId')
        music_id = data.get('musicId')
        overlays = data.get('overlays', {})
        max_duration = int(data.get('maxDuration', 60))
        
        if not video_id or not music_id:
            return jsonify({"status": "error", "message": "Missing videoId or musicId"}), 400
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())[:8]
        
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
        app.logger.error(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def download_google_drive(file_id, output_path):
    """Download file from Google Drive"""
    try:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        response = requests.get(url, stream=True, timeout=60)
        
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return output_path
        return None
    except:
        return None


def get_duration(file_path):
    """Get media duration"""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        duration_str = result.stdout.strip()
        return float(duration_str) if duration_str else 30.0
    except:
        return 30.0


def process_video(video_path, music_path, output_path, overlays, duration):
    """Process video with FFmpeg"""
    try:
        # Extract texts
        top = overlays.get('top', {}).get('text', '').replace("'", "\\'")
        center = overlays.get('center', {}).get('text', '').replace("'", "\\'")
        bottom = overlays.get('bottom', {}).get('text', '').replace("'", "\\'")
        
        # Build filter
        filters = ["scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"]
        
        if top:
            filters.append(f"drawtext=text='{top}':fontsize=26:fontcolor=white:x=(w-text_w)/2:y=80:box=1:boxcolor=black@0.5:boxborderw=10")
        
        if center:
            filters.append(f"drawtext=text='{center}':fontsize=44:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.65:boxborderw=15")
        
        if bottom:
            filters.append(f"drawtext=text='{bottom}':fontsize=22:fontcolor=white:x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.5:boxborderw=10")
        
        filter_str = ",".join(filters)
        
        # FFmpeg command
        cmd = [
            'ffmpeg', '-i', video_path, '-i', music_path, '-t', str(duration),
            '-filter_complex', f'[0:v]{filter_str}[v]',
            '-map', '[v]', '-map', '1:a',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
            '-c:a', 'aac', '-b:a', '128k', '-shortest', '-y', output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except:
        return False


def cleanup(files):
    """Delete files"""
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
        except:
            pass


@app.route('/outputs/<filename>', methods=['GET'])
def serve_output(filename):
    """Serve processed video"""
    file_path = f"{OUTPUT_FOLDER}/{filename}"
    if os.path.exists(file_path):
        return send_file(file_path, mimetype='video/mp4')
    return jsonify({"error": "File not found"}), 404


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
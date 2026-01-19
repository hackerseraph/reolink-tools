#!/usr/bin/env python3
"""
Motion Detection Scanner
Flask app to select a region of interest and scan videos for motion.
"""

import cv2
import numpy as np
from flask import Flask, render_template, request, jsonify, Response
from pathlib import Path
import base64
import json
from concurrent.futures import ThreadPoolExecutor
import threading

app = Flask(__name__)

# Configuration
DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
MOTION_THRESHOLD = 25  # Pixel difference threshold
MIN_MOTION_AREA = 500  # Minimum contour area to count as motion
FRAME_SKIP = 5  # Process every Nth frame for speed

# Global state for scanning
scan_state = {
    'running': False,
    'progress': 0,
    'total': 0,
    'current_file': '',
    'results': [],
    'cancelled': False
}
scan_lock = threading.Lock()


def get_video_files():
    """Get all video files from downloads directory."""
    if not DOWNLOADS_DIR.exists():
        return []
    
    extensions = {'.mp4', '.avi', '.mkv', '.mov', '.flv'}
    files = []
    for f in sorted(DOWNLOADS_DIR.iterdir()):
        if f.suffix.lower() in extensions:
            files.append(f)
    return files


def get_first_frame(video_path):
    """Extract the first frame from a video for ROI selection."""
    cap = cv2.VideoCapture(str(video_path))
    ret, frame = cap.read()
    cap.release()
    
    if ret:
        # Resize for web display if too large
        max_width = 1280
        if frame.shape[1] > max_width:
            scale = max_width / frame.shape[1]
            frame = cv2.resize(frame, None, fx=scale, fy=scale)
        return frame
    return None


def detect_motion_in_roi(video_path, roi, threshold=MOTION_THRESHOLD, min_area=MIN_MOTION_AREA):
    """
    Detect motion within a specific ROI in a video.
    Returns (has_motion, motion_frames, total_frames, max_motion_area)
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False, 0, 0, 0
    
    # Get video properties
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # Scale ROI to video dimensions if needed
    x, y, w, h = roi['x'], roi['y'], roi['width'], roi['height']
    scale_x = width / roi.get('frame_width', width)
    scale_y = height / roi.get('frame_height', height)
    
    x = int(x * scale_x)
    y = int(y * scale_y)
    w = int(w * scale_x)
    h = int(h * scale_y)
    
    # Ensure ROI is within bounds
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = min(w, width - x)
    h = min(h, height - y)
    
    prev_gray = None
    motion_frames = 0
    max_motion_area = 0
    frame_count = 0
    
    while True:
        # Check for cancellation
        with scan_lock:
            if scan_state['cancelled']:
                cap.release()
                return False, 0, 0, 0
        
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_count += 1
        
        # Skip frames for speed
        if frame_count % FRAME_SKIP != 0:
            continue
        
        # Extract ROI
        roi_frame = frame[y:y+h, x:x+w]
        if roi_frame.size == 0:
            continue
            
        gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        
        if prev_gray is None:
            prev_gray = gray
            continue
        
        # Compute difference
        frame_delta = cv2.absdiff(prev_gray, gray)
        thresh = cv2.threshold(frame_delta, threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        
        # Find contours
        contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        for contour in contours:
            area = cv2.contourArea(contour)
            if area > min_area:
                motion_frames += 1
                max_motion_area = max(max_motion_area, area)
                break  # Count frame once even if multiple contours
        
        prev_gray = gray
    
    cap.release()
    
    has_motion = motion_frames > 0
    return has_motion, motion_frames, total_frames // FRAME_SKIP, max_motion_area


def scan_videos_worker(roi, video_files):
    """Worker function to scan videos for motion."""
    global scan_state
    
    results = []
    
    for idx, video_file in enumerate(video_files):
        with scan_lock:
            if scan_state['cancelled']:
                break
            scan_state['progress'] = idx + 1
            scan_state['current_file'] = video_file.name
        
        has_motion, motion_frames, total_frames, max_area = detect_motion_in_roi(
            video_file, roi
        )
        
        if has_motion:
            results.append({
                'filename': video_file.name,
                'path': str(video_file),
                'motion_frames': motion_frames,
                'total_frames': total_frames,
                'motion_percent': round(motion_frames / max(total_frames, 1) * 100, 1),
                'max_area': max_area
            })
    
    with scan_lock:
        scan_state['results'] = sorted(results, key=lambda x: x['motion_percent'], reverse=True)
        scan_state['running'] = False
        scan_state['current_file'] = ''


@app.route('/')
def index():
    """Main page."""
    return render_template('index.html')


@app.route('/api/videos')
def list_videos():
    """List available video files."""
    videos = get_video_files()
    return jsonify({
        'videos': [{'name': v.name, 'path': str(v)} for v in videos],
        'count': len(videos)
    })


@app.route('/api/frame/<path:filename>')
def get_frame(filename):
    """Get first frame of a video as base64 image."""
    video_path = DOWNLOADS_DIR / filename
    if not video_path.exists():
        return jsonify({'error': 'Video not found'}), 404
    
    frame = get_first_frame(video_path)
    if frame is None:
        return jsonify({'error': 'Could not read video'}), 500
    
    # Encode as JPEG
    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    
    return jsonify({
        'image': f'data:image/jpeg;base64,{img_base64}',
        'width': frame.shape[1],
        'height': frame.shape[0]
    })


@app.route('/api/scan', methods=['POST'])
def start_scan():
    """Start scanning videos for motion in the selected ROI."""
    global scan_state
    
    with scan_lock:
        if scan_state['running']:
            return jsonify({'error': 'Scan already in progress'}), 400
    
    data = request.json
    roi = data.get('roi')
    
    if not roi:
        return jsonify({'error': 'No ROI specified'}), 400
    
    video_files = get_video_files()
    if not video_files:
        return jsonify({'error': 'No video files found'}), 400
    
    with scan_lock:
        scan_state = {
            'running': True,
            'progress': 0,
            'total': len(video_files),
            'current_file': '',
            'results': [],
            'cancelled': False
        }
    
    # Start scanning in background thread
    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(scan_videos_worker, roi, video_files)
    
    return jsonify({'status': 'started', 'total': len(video_files)})


@app.route('/api/scan/status')
def scan_status():
    """Get current scan status."""
    with scan_lock:
        return jsonify({
            'running': scan_state['running'],
            'progress': scan_state['progress'],
            'total': scan_state['total'],
            'current_file': scan_state['current_file'],
            'results': scan_state['results'] if not scan_state['running'] else [],
            'results_count': len(scan_state['results'])
        })


@app.route('/api/scan/cancel', methods=['POST'])
def cancel_scan():
    """Cancel the current scan."""
    global scan_state
    with scan_lock:
        scan_state['cancelled'] = True
    return jsonify({'status': 'cancelled'})


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    """Get or update detection settings."""
    global MOTION_THRESHOLD, MIN_MOTION_AREA, FRAME_SKIP
    
    if request.method == 'POST':
        data = request.json
        MOTION_THRESHOLD = data.get('threshold', MOTION_THRESHOLD)
        MIN_MOTION_AREA = data.get('min_area', MIN_MOTION_AREA)
        FRAME_SKIP = data.get('frame_skip', FRAME_SKIP)
    
    return jsonify({
        'threshold': MOTION_THRESHOLD,
        'min_area': MIN_MOTION_AREA,
        'frame_skip': FRAME_SKIP
    })


@app.route('/video/<path:filename>')
def serve_video(filename):
    """Serve video file for playback."""
    video_path = DOWNLOADS_DIR / filename
    if not video_path.exists():
        return "Video not found", 404
    
    # Stream the video file
    def generate():
        with open(video_path, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                yield chunk
    
    return Response(
        generate(),
        mimetype='video/mp4',
        headers={
            'Content-Disposition': f'inline; filename="{filename}"',
            'Accept-Ranges': 'bytes'
        }
    )


if __name__ == '__main__':
    print(f"\n🎬 Motion Detection Scanner")
    print(f"   Downloads folder: {DOWNLOADS_DIR}")
    print(f"   Videos found: {len(get_video_files())}")
    print(f"\n   Open http://localhost:5000 in your browser\n")
    app.run(debug=True, host='0.0.0.0', port=5000)

# Reolink Tools

A collection of tools for working with Reolink NVR/camera recordings.

## Tools Included

### 1. Video Downloader (`app.py`)
Download a full day's worth of video recordings from your Reolink NVR/camera. The Reolink web interface only allows downloading 5-minute segments - this tool automates the process.

**Features:**
- Downloads video in 5-minute chunks (matching NVR behavior)
- Parallel workers for faster downloads (~2x speed with 2 workers)
- Interactive mode with channel and date selection
- Support for both high quality (main) and low quality (sub) streams
- Automatic retry on failures
- Skips already downloaded files

### 2. Motion Detection Scanner (`motion_detection/app.py`)
A Flask-based web app to scan downloaded videos for motion in a specific region of interest.

**Features:**
- Web-based ROI (Region of Interest) selection
- Scans all videos in the downloads folder
- Shows results sorted by motion percentage
- Configurable detection sensitivity
- Video playback directly in the browser

## Requirements

- Python 3.8+
- Reolink NVR or camera with network access

## Installation

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/reolink-tools.git
cd reolink-tools
```

2. Create and activate a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up your credentials:
```bash
cp .env.template .env
# Edit .env with your NVR's IP, username, and password
```

## Usage

### Video Downloader

**Interactive Mode (Recommended):**
```bash
python app.py
```

This will:
1. Load credentials from `.env` (or prompt if missing)
2. Connect to your NVR/camera
3. Show available channels with camera names
4. Display dates with recordings (last 30 days)
5. Let you select quality and number of parallel workers
6. Download all recordings for the selected day

**Direct Download Mode:**
```bash
python app.py --date 2024-01-15 --channel 0
```

**All Options:**
```bash
python app.py \
  --host 192.168.1.100 \
  --username admin \
  --password yourpassword \
  --channel 0 \
  --date 2024-01-15 \
  --quality high \
  --workers 2 \
  --output ./downloads
```

### Motion Detection Scanner

```bash
cd motion_detection
python app.py
```

Open http://localhost:5000 in your browser, then:
1. Drag to select a region of interest on the video frame
2. Adjust detection settings if needed
3. Click "Start Scan" to analyze all videos
4. Click on any result to play the video

## Configuration

### .env File
```
REOLINK_HOST=192.168.1.100
REOLINK_USERNAME=admin
REOLINK_PASSWORD=your_password
REOLINK_CHANNEL=0
```

### Motion Detection Settings
- **Threshold** (1-100): Lower = more sensitive to small changes
- **Min Area**: Minimum pixel area to count as motion
- **Frame Skip**: Process every Nth frame (higher = faster, less accurate)

## Output

Videos are saved to `./downloads/` with the format:
```
YYYY-MM-DD_YYYYMMDD_HHMMSS.mp4
```

Example: `2024-01-15_20240115_143022.mp4`

## Troubleshooting

### Download Issues
- **503 errors**: Normal with parallel workers, the tool retries automatically
- **Session errors**: The NVR limits concurrent connections, using 2 workers is recommended
- **Slow downloads**: Each 5-minute chunk takes time to transfer from the NVR

### Motion Detection Issues
- **No videos found**: Make sure you've downloaded videos first
- **Scanner is slow**: Increase Frame Skip setting

## Technical Details

- Uses the `reolink-aio` library to communicate with the NVR API
- Downloads use the VOD (Video on Demand) endpoint
- Each parallel worker creates its own authenticated session
- Motion detection uses OpenCV frame differencing

## License

MIT

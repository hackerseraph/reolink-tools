#!/usr/bin/env python3
"""
Reolink Full Day Video Downloader
Downloads a full day's worth of video recordings in parallel 5-minute chunks.
Supports both high quality (main) and low quality (sub) streams.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import os
from dotenv import load_dotenv
from reolink_aio.api import Host


# Download in 5-minute chunks (same as web interface)
CHUNK_DURATION_MINUTES = 5
# Number of parallel worker connections (each is a separate login session)
NUM_WORKERS = 2


def load_config():
    """Load configuration from .env file if it exists."""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print("✓ Loaded .env file")
        return True
    return False


def get_credential(env_var, prompt, default=None, password=False):
    """Get credential from environment or prompt user."""
    value = os.getenv(env_var)
    if value:
        if password:
            print(f"✓ {prompt}: {'*' * len(value)}")
        else:
            print(f"✓ {prompt}: {value}")
        return value
    
    if password:
        import getpass
        value = getpass.getpass(f"  Enter {prompt}: ")
    else:
        default_str = f" [{default}]" if default else ""
        value = input(f"  Enter {prompt}{default_str}: ").strip()
        if not value and default:
            value = default
    return value


async def download_chunk_simple(api, channel, filename, stream, start_time, end_time, output_path):
    """Download a single chunk of video. Returns (success, size_mb)."""
    chunk_id = f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
    
    # Skip if already exists
    if output_path.exists() and output_path.stat().st_size > 0:
        size_mb = output_path.stat().st_size / (1024 * 1024)
        return True, size_mb, chunk_id, "exists"
    
    max_retries = 5
    for retry in range(max_retries):
        try:
            result = await api.download_vod(
                channel=channel,
                filename=filename,
                stream=stream,
                start_time=start_time,
                end_time=end_time
            )
            
            with open(output_path, 'wb') as f:
                async for chunk in result.stream.iter_chunked(64 * 1024):
                    f.write(chunk)
            
            if output_path.exists() and output_path.stat().st_size > 0:
                size_mb = output_path.stat().st_size / (1024 * 1024)
                return True, size_mb, chunk_id, "downloaded"
                
        except Exception as e:
            error_msg = str(e).lower()
            wait_time = (retry + 1) * 2
            
            if "503" in str(e) or "busy" in error_msg:
                wait_time = (retry + 1) * 5
                if retry < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
            elif "closed" in error_msg or "session" in error_msg:
                if retry < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    try:
                        await api.login()
                    except:
                        pass
                    continue
            else:
                if retry < max_retries - 1:
                    await asyncio.sleep(wait_time)
                    continue
    
    # Clean up partial file
    if output_path.exists():
        output_path.unlink()
    
    return False, 0, chunk_id, "failed"


async def worker(worker_id, host, username, password, channel, stream, queue, progress, lock):
    """Worker that downloads chunks from the queue using its own connection."""
    api = Host(host, username, password)
    
    try:
        await api.login()
        await api.get_host_data()
        
        while True:
            try:
                chunk = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            
            success, size_mb, chunk_id, status = await download_chunk_simple(
                api=api,
                channel=channel,
                filename=chunk['filename'],
                stream=stream,
                start_time=chunk['start'],
                end_time=chunk['end'],
                output_path=chunk['output']
            )
            
            async with lock:
                if success:
                    progress['downloaded'] += 1
                    progress['size_mb'] += size_mb
                    if status == "exists":
                        print(f"  [W{worker_id}] ⏭ {chunk_id} (exists: {size_mb:.1f} MB) [{progress['downloaded']}/{progress['total']}]")
                    else:
                        print(f"  [W{worker_id}] ✓ {chunk_id} ({size_mb:.1f} MB) [{progress['downloaded']}/{progress['total']}]")
                else:
                    progress['failed'] += 1
                    print(f"  [W{worker_id}] ✗ {chunk_id} (failed) [{progress['downloaded']}/{progress['total']}]")
            
            # Small delay between downloads for this worker
            await asyncio.sleep(0.3)
            
    except Exception as e:
        print(f"  [W{worker_id}] Error: {e}")
    finally:
        try:
            await api.logout()
        except:
            pass


async def download_day_recordings_parallel(host, username, password, date, output_dir, channel, stream='main', num_workers=NUM_WORKERS):
    """Download all recordings using multiple parallel worker connections."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Set time range for the entire day
    start_time = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(days=1) - timedelta(seconds=1)
    
    quality_label = "HIGH (main)" if stream == 'main' else "LOW (sub)"
    
    print(f"\n📥 Downloading recordings from {start_time.date()}")
    print(f"   Quality: {quality_label}")
    print(f"   Chunk size: {CHUNK_DURATION_MINUTES} minutes")
    print(f"   Workers: {num_workers} parallel connections")
    
    # Use a temporary connection to get VOD files
    api = Host(host, username, password)
    try:
        await api.login()
        await api.get_host_data()
        
        status_list, vod_files = await api.request_vod_files(
            channel=channel,
            start=start_time,
            end=end_time
        )
    except Exception as e:
        print(f"❌ Error searching for recordings: {e}")
        return
    finally:
        try:
            await api.logout()
        except:
            pass
    
    if not vod_files:
        print("⚠ No recordings found for the specified date.")
        return
    
    print(f"✓ Found {len(vod_files)} recording segments")
    
    # Build list of all chunks to download
    chunks_to_download = []
    
    for vod_file in vod_files:
        vod_start = vod_file.start_time
        vod_end = vod_file.end_time
        
        chunk_start = vod_start
        while chunk_start < vod_end:
            chunk_end = min(chunk_start + timedelta(minutes=CHUNK_DURATION_MINUTES), vod_end)
            
            timestamp_str = chunk_start.strftime("%Y%m%d_%H%M%S")
            output_filename = f"{date.strftime('%Y-%m-%d')}_{timestamp_str}.mp4"
            file_path = output_path / output_filename
            
            chunks_to_download.append({
                'filename': vod_file.file_name,
                'start': chunk_start,
                'end': chunk_end,
                'output': file_path
            })
            
            chunk_start = chunk_end
    
    total_chunks = len(chunks_to_download)
    print(f"  Total chunks: {total_chunks}")
    
    # Estimate size
    est_size_gb = (total_chunks * CHUNK_DURATION_MINUTES * 37) / 1024
    if stream == 'sub':
        est_size_gb /= 10
    print(f"  Estimated size: ~{est_size_gb:.1f} GB")
    print()
    
    # Create queue with all chunks
    queue = asyncio.Queue()
    for chunk in chunks_to_download:
        queue.put_nowait(chunk)
    
    # Progress tracking
    progress = {'downloaded': 0, 'failed': 0, 'total': total_chunks, 'size_mb': 0}
    lock = asyncio.Lock()
    
    print(f"⚡ Starting {num_workers} parallel workers...")
    if num_workers > 1:
        print(f"   (occasional 503 errors are normal - workers retry automatically)")
    print()
    start = datetime.now()
    
    # Start workers
    workers = []
    for i in range(num_workers):
        w = asyncio.create_task(worker(
            worker_id=i + 1,
            host=host,
            username=username,
            password=password,
            channel=channel,
            stream=stream,
            queue=queue,
            progress=progress,
            lock=lock
        ))
        workers.append(w)
        # Stagger worker starts to avoid login race
        await asyncio.sleep(1)
    
    # Wait for all workers to complete
    await asyncio.gather(*workers)
    
    elapsed = (datetime.now() - start).total_seconds()
    
    # Summary
    print(f"\n{'='*60}")
    print(f"✓ Download complete!")
    print(f"  Chunks downloaded: {progress['downloaded']}/{total_chunks}")
    if progress['failed'] > 0:
        print(f"  Chunks failed: {progress['failed']}")
    print(f"  Total size: {progress['size_mb']:.1f} MB ({progress['size_mb']/1024:.2f} GB)")
    print(f"  Time elapsed: {int(elapsed//60)}m {int(elapsed%60)}s")
    if elapsed > 0:
        print(f"  Speed: {progress['size_mb']/elapsed:.1f} MB/s")
    print(f"  Location: {output_path.resolve()}")
    print(f"{'='*60}")


async def list_available_dates(api, channel):
    """Get list of dates that have recordings."""
    print(f"\n🔍 Scanning for recording dates...")
    
    dates_with_recordings = []
    end_date = datetime.now()
    
    for days_ago in range(30):
        check_date = end_date - timedelta(days=days_ago)
        start_time = check_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(hours=1)
        
        try:
            status_list, vod_files = await api.request_vod_files(
                channel=channel,
                start=start_time,
                end=end_time
            )
            if vod_files:
                dates_with_recordings.append(check_date.date())
        except:
            continue
    
    return sorted(dates_with_recordings, reverse=True)


async def interactive_mode(host, username, password):
    """Run in interactive mode."""
    print(f"\n{'='*60}")
    print("🎥 Reolink Full Day Video Downloader")
    print(f"{'='*60}")
    
    api = Host(host, username, password)
    
    try:
        await api.login()
        await api.get_host_data()
        
        print(f"✓ Connected to {api.nvr_name if api.is_nvr else 'camera'}")
        
        # Get channels
        if api.is_nvr:
            channels = list(api._channels) if isinstance(api._channels, (list, dict)) else [0]
        else:
            channels = [0]
        
        # Show channels
        print(f"\n📹 Available Channels:")
        for ch in channels:
            try:
                name = api.camera_name(ch)
                print(f"  [{ch}] {name}")
            except:
                print(f"  [{ch}] Channel {ch}")
        
        # Select channel
        if len(channels) == 1:
            selected_channel = channels[0]
        else:
            while True:
                try:
                    ch_input = input(f"\nSelect channel {channels}: ").strip()
                    selected_channel = int(ch_input)
                    if selected_channel in channels:
                        break
                except ValueError:
                    pass
                print("Invalid selection")
        
        # Get dates with recordings
        dates = await list_available_dates(api, selected_channel)
        
        if not dates:
            print("\n⚠ No recordings found in the past 30 days")
            return
        
        # Show dates
        print(f"\n📅 Recording Dates:")
        for idx, date in enumerate(dates[:15], 1):
            days_ago = (datetime.now().date() - date).days
            age = "today" if days_ago == 0 else f"{days_ago} days ago"
            print(f"  [{idx}] {date.strftime('%Y-%m-%d (%A)')} - {age}")
        
        # Select date
        while True:
            try:
                date_input = input(f"\nSelect date [1-{min(len(dates), 15)}]: ").strip()
                date_idx = int(date_input) - 1
                if 0 <= date_idx < min(len(dates), 15):
                    selected_date = datetime.combine(dates[date_idx], datetime.min.time())
                    break
            except ValueError:
                pass
            print("Invalid selection")
        
        # Select quality
        print(f"\n📊 Stream Quality:")
        print(f"  [1] HIGH (main) - Full resolution, larger files (~37 MB/min)")
        print(f"  [2] LOW (sub) - Reduced resolution, smaller files")
        
        while True:
            quality_input = input("\nSelect quality [1/2]: ").strip()
            if quality_input == "1":
                stream = "main"
                break
            elif quality_input == "2":
                stream = "sub"
                break
            print("Invalid selection")
        
        # Select number of workers
        print(f"\n⚡ Parallel Workers (separate connections):")
        print(f"  [1] 1 worker  (safest, slowest)")
        print(f"  [2] 2 workers (recommended, ~2x faster)")
        
        while True:
            worker_input = input("\nSelect workers [1-2, default=2]: ").strip()
            if worker_input == "":
                num_workers = 2
                break
            try:
                num_workers = int(worker_input)
                if 1 <= num_workers <= 2:
                    break
            except ValueError:
                pass
            print("Invalid selection")
        
        # Confirm
        camera_name = api.camera_name(selected_channel)
        print(f"\n{'='*60}")
        print(f"📥 Ready to download:")
        print(f"   Camera: {camera_name} (Channel {selected_channel})")
        print(f"   Date: {selected_date.strftime('%Y-%m-%d')}")
        print(f"   Quality: {'HIGH (main)' if stream == 'main' else 'LOW (sub)'}")
        print(f"   Workers: {num_workers} parallel connections")
        print(f"{'='*60}")
        
        confirm = input("\nProceed? [Y/n]: ").strip().lower()
        if confirm and confirm != 'y':
            print("Cancelled")
            return
        
        # Logout before starting parallel downloads (they create their own connections)
        await api.logout()
        
        # Download with parallel workers
        await download_day_recordings_parallel(
            host=host,
            username=username,
            password=password,
            date=selected_date,
            output_dir="./downloads",
            channel=selected_channel,
            stream=stream,
            num_workers=num_workers
        )
        return  # Already logged out
        
    finally:
        try:
            await api.logout()
        except:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Download a full day of video from Reolink NVR/camera"
    )
    parser.add_argument("--host", help="NVR IP address (or set REOLINK_HOST in .env)")
    parser.add_argument("--username", help="Username (or set REOLINK_USERNAME in .env)")
    parser.add_argument("--password", help="Password (or set REOLINK_PASSWORD in .env)")
    parser.add_argument("--date", help="Date to download (YYYY-MM-DD)")
    parser.add_argument("--channel", type=int, help="Channel number")
    parser.add_argument("--quality", choices=['high', 'low'], default='high',
                        help="Video quality: high (main stream) or low (sub stream)")
    parser.add_argument("--workers", type=int, default=2, choices=[1,2],
                        help="Number of parallel worker connections (1-2, default=2)")
    parser.add_argument("--output", default="./downloads", help="Output directory")
    
    args = parser.parse_args()
    
    load_config()
    
    print("\n🔐 Credentials:")
    host = args.host or get_credential("REOLINK_HOST", "Host/IP")
    username = args.username or get_credential("REOLINK_USERNAME", "Username", "admin")
    password = args.password or get_credential("REOLINK_PASSWORD", "Password", password=True)
    
    if not all([host, username, password]):
        print("\n❌ Missing credentials")
        return
    
    stream = 'main' if args.quality == 'high' else 'sub'
    
    if not args.date:
        asyncio.run(interactive_mode(host, username, password))
    else:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
        channel = args.channel or int(os.getenv("REOLINK_CHANNEL", "0"))
        
        async def direct_download():
            await download_day_recordings_parallel(
                host=host,
                username=username,
                password=password,
                date=target_date,
                output_dir=args.output,
                channel=channel,
                stream=stream,
                num_workers=args.workers
            )
        
        asyncio.run(direct_download())


if __name__ == "__main__":
    main()

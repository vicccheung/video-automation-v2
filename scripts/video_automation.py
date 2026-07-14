#!/usr/bin/env python3
"""
Video Automation Pipeline: 抽帧 → 生成文案 → 合成字幕视频

Usage:
    python video_automation.py --input <video_folder> --output <output_folder> [--api-key <BAILIAN_API_KEY>]

Requirements:
    - FFmpeg (will auto-detect; if missing, guide user to install)
    - Python packages: requests, edge-tts (auto-installed into venv if missing)
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ============================================================
# Constants
# ============================================================

# 百炼平台 API 配置
BAILIAN_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
VISION_MODEL = "qwen-vl-max"  # 可替换为 qwen-vl-plus 或具体版本
TTS_MODEL = "cosyvoice-v1"    # 百炼 TTS 模型
TTS_VOICE = "longxiaochun"    # 百炼 TTS 音色（可更换）

# 抽帧间隔（秒）
FRAME_INTERVAL = 5
# 最多发送给模型的帧数
MAX_FRAMES_FOR_MODEL = 20

# 口播文案 Prompt 模板
SCRIPT_PROMPT_TEMPLATE = """请分析以下游戏推广视频的多帧画面截图，并撰写一段口播推广文案，要求口语化、有吸引力、节奏感强、适合游戏买量素材推广。

【视频信息】
本视频时长约{VIDEO_DURATION}秒，请确保口播文案的朗读时长与视频时长完全匹配（正常语速约4.5字/秒），让观众看到最后一帧时文案恰好播完，口播必须覆盖整个视频，不能提前结束也不能超出视频时长。若视频较短（如15-25秒），文案应精炼集中在一个核心卖点；若视频较长（如45-60秒），可展开多个卖点叙述。

【严格要求】
1. 第一句话必须以"这是一款"开头，例如"这是一款热血的RPG游戏"、"这是一款策略卡牌游戏"、"这是一款画面精美的动作游戏"等。根据画面内容判断游戏类型填入星号位置。严禁以"你敢信的"、"你敢信"、"你敢相信"、"你敢想象"等词语开头，禁止使用任何"你敢X"句式。
2. 只输出文案正文，不要输出任何额外说明、前缀、后缀、标题、注释。
3. 不要出现"好的"、"以下为"、"开头："、"结尾："、"文案："等词语。
4. 文案字数必须严格按视频时长换算：{VIDEO_DURATION}秒 × 约4.5字/秒 = 约{TARGET_WORDS}字，实际输出控制在{TARGET_WORDS_MIN}-{TARGET_WORDS_MAX}字。口播必须覆盖整个视频时长，既不能提前结束也不能超出。这是硬性要求，严禁超字数。
5. 直接输出文案，从第一句开始写。"""

# 支持的视频格式
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm"}


def build_script_prompt(video_duration):
    """Build the script generation prompt with video-duration-aware word count targets."""
    dur = max(video_duration, 5)  # minimum 5s to avoid zero targets
    target_chars = int(dur * 4.5)  # 4.5 chars/s — balanced for TTS ~5chars/s + atempo margin
    target_min = max(30, int(target_chars * 0.85))
    target_max = int(target_chars * 1.05)
    return SCRIPT_PROMPT_TEMPLATE.format(
        VIDEO_DURATION=f"{dur:.0f}",
        TARGET_WORDS=target_chars,
        TARGET_WORDS_MIN=target_min,
        TARGET_WORDS_MAX=target_max,
    )


# ============================================================
# Utility: Run subprocess with logging
# ============================================================

def run_cmd(cmd, desc="", check=True, capture=False, cwd=None):
    """Run a command and print progress."""
    print(f"  ▶ {desc}")
    sys.stdout.flush()
    kwargs = {}
    if cwd:
        kwargs["cwd"] = cwd
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)
            return result
        result = subprocess.run(cmd, check=check, **kwargs)
        return result
    except subprocess.CalledProcessError as e:
        print(f"  ✗ Command failed: {' '.join(cmd)}")
        if e.stderr:
            print(f"    stderr: {e.stderr[:500]}")
        raise
    except FileNotFoundError:
        print(f"  ✗ Command not found: {cmd[0]}")
        raise


# ============================================================
# Step 0: Prerequisites check
# ============================================================

def check_prerequisites():
    """Check FFmpeg and Python packages; install if possible."""
    print("\n[Step 0] Checking prerequisites...")
    issues = []

    # Check FFmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print("  ✓ FFmpeg found")
    except (FileNotFoundError, subprocess.CalledProcessError):
        issues.append("FFmpeg not found. Please install FFmpeg:\n"
                       "  - Windows (scoop): scoop install ffmpeg\n"
                       "  - macOS (brew):   brew install ffmpeg\n"
                       "  - Linux (apt):    sudo apt install ffmpeg")

    # Check / install Python packages
    # Note: pip install name may differ from import name (e.g., edge-tts → edge_tts)
    packages = [
        ("requests", "requests"),
        ("edge-tts", "edge_tts"),
    ]
    for pip_name, import_name in packages:
        try:
            __import__(import_name)
            print(f"  ✓ Python package '{pip_name}' found")
        except ImportError:
            print(f"  ⚠ Python package '{pip_name}' not found, installing...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name, "--quiet"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  ✓ '{pip_name}' installed successfully")
            else:
                issues.append(f"Failed to install '{pip_name}': {result.stderr[:200]}")

    if issues:
        for issue in issues:
            print(f"  ✗ {issue}")
        print("\nPlease resolve the above issues and re-run.")
        sys.exit(1)

    print("  ✓ All prerequisites ready.\n")


# ============================================================
# Step 1: Find video files
# ============================================================

def find_videos(input_dir):
    """Scan input_dir recursively for video files, return sorted list of Paths."""
    videos = []
    for f in Path(input_dir).rglob("*"):
        if f.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(f)
    videos.sort()
    return videos


# ============================================================
# Step 2: Extract keyframes every N seconds
# ============================================================

def extract_frames(video_path, output_dir, interval=FRAME_INTERVAL):
    """
    Extract 1 frame every `interval` seconds from video.
    Returns (frame_paths, timestamps, fps, duration, video_width, video_height).
    """
    video_stem = video_path.stem
    frames_dir = Path(output_dir) / f"{video_stem}_frames"
    os.makedirs(str(frames_dir), exist_ok=True)

    # Pattern: frame_0001.jpg, frame_0002.jpg, ...
    pattern = str(frames_dir / "frame_%04d.jpg")

    print(f"    Extracting frames (1 per {interval}s)...")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"fps=1/{interval}",
        "-q:v", "2",           # high quality JPEG
        "-y",                   # overwrite
        pattern
    ]
    run_cmd(cmd, "  FFmpeg frame extraction")

    # Collect extracted frames
    frame_files = sorted(frames_dir.glob("frame_*.jpg"))
    print(f"    Extracted {len(frame_files)} frames.")

    # Detect video FPS for later timestamp calculation
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    fps_result = subprocess.run(probe_cmd, capture_output=True, text=True)
    fps_str = fps_result.stdout.strip()
    try:
        num, den = fps_str.split("/")
        fps = float(num) / float(den)
    except:
        fps = 30.0

    # Get video duration
    duration_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    dur_result = subprocess.run(duration_cmd, capture_output=True, text=True)
    try:
        duration = float(dur_result.stdout.strip())
    except:
        duration = 0

    # Get video width (for orientation detection)
    width_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    width_result = subprocess.run(width_cmd, capture_output=True, text=True)
    try:
        video_width = int(width_result.stdout.strip())
    except:
        video_width = 1280

    # Get video height (for subtitle positioning)
    height_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    height_result = subprocess.run(height_cmd, capture_output=True, text=True)
    try:
        video_height = int(height_result.stdout.strip())
    except:
        video_height = 720

    # Calculate timestamps for each frame
    timestamps = []
    for i in range(len(frame_files)):
        ts = i * interval
        timestamps.append(ts)

    return frame_files, timestamps, fps, duration, video_width, video_height


# ============================================================
# Step 3: Call 百炼 Vision API to generate script
# ============================================================

def encode_image_base64(image_path):
    """Read image and return base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_bailian_vision(api_key, frames, prompt, max_frames=MAX_FRAMES_FOR_MODEL):
    """
    Send frames to 百炼 Qwen vision model and get script.
    Samples frames if there are too many.
    Returns the generated script text.
    """
    # Sample frames if too many
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        selected = [frames[int(i * step)] for i in range(max_frames)]
        print(f"    Sampling {max_frames} frames from {len(frames)} total...")
    else:
        selected = frames

    print(f"    Sending {len(selected)} frames to 百炼 Vision API ({VISION_MODEL})...")

    # Build content parts: text + images
    content_parts = [{"type": "text", "text": prompt}]
    for fp in selected:
        b64 = encode_image_base64(fp)
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": content_parts
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.8
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    import requests
    for attempt in range(3):
        try:
            resp = requests.post(
                BAILIAN_API_BASE,
                headers=headers,
                json=payload,
                timeout=120
            )
            if resp.status_code == 200:
                data = resp.json()
                script_text = data["choices"][0]["message"]["content"]
                print(f"    ✓ Script generated ({len(script_text)} chars)")
                return script_text
            elif resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"    Rate limited, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    API error {resp.status_code}: {resp.text[:300]}")
                if attempt < 2:
                    time.sleep(3)
        except Exception as e:
            print(f"    Request failed: {e}")
            if attempt < 2:
                time.sleep(3)

    raise RuntimeError(f"Failed to get response from 百炼 Vision API after 3 attempts")


# ============================================================
# Step 4: Generate TTS audio from script
# ============================================================

def generate_tts(script_text, output_path):
    """
    Generate TTS audio from script text using edge-tts.
    Also captures word-level timestamps for subtitle sync.
    Returns (output_path, word_timings) where word_timings is
    [(word_text, start_sec, end_sec), ...] or None if unavailable.
    """
    print(f"    Generating TTS audio (with word timestamps)...")

    # Sanitize script text for TTS
    text = script_text.strip()

    import edge_tts
    import asyncio

    async def _tts():
        communicate = edge_tts.Communicate(
            text,
            voice="zh-CN-YunjianNeural",  # 云健：有激情的男声，适合游戏/体育推广解说
            rate="+10%",
            pitch="+0Hz",
            boundary="WordBoundary"  # Explicitly request word-level boundaries
        )
        sub = edge_tts.SubMaker()
        audio_chunks = []

        async for message in communicate.stream():
            msg_type = message["type"]
            if msg_type in ("WordBoundary", "SentenceBoundary"):
                sub.feed(message)
            elif msg_type == "audio":
                audio_chunks.append(message["data"])

        # Write audio file
        with open(str(output_path), "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

        # Build word timings from SubMaker cues
        # SubMaker.cues is a list of Subtitle objects with .start (timedelta),
        # .end (timedelta), .content (str)
        word_timings = []
        for cue in sub.cues:
            start_sec = cue.start.total_seconds()
            end_sec = cue.end.total_seconds()
            word_timings.append((cue.content, start_sec, end_sec))
        return word_timings

    word_timings = asyncio.run(_tts())
    print(f"    ✓ TTS audio saved ({output_path}), captured {len(word_timings)} word timestamps")
    return output_path, word_timings


# ============================================================
# Step 5: Create ASS subtitle file
# ============================================================

# ASS coordinate system (standard default, avoids libass MarginV bug on Windows)
ASS_PLAYRES_X = 384
ASS_PLAYRES_Y = 288

def format_ass_time(seconds):
    """Convert seconds to ASS timestamp format: h:mm:ss.cc (centiseconds)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_ass(script_text, ass_path, tts_duration, video_height, word_timings=None, max_chars_per_line=14, is_landscape=False):
    """
    Generate an ASS subtitle file with proper coordinate system.
    Uses PlayResY=288 (ASS standard).

    Subtitle positioning:
    - Portrait (竖版): ~384px from video bottom (original behavior)
    - Landscape (横版): 1/4 from video bottom (for better readability)

    If word_timings is provided (from edge-tts SubMaker), subtitle segments
    are mapped to actual word boundaries for frame-perfect sync.
    Otherwise falls back to proportional character-count distribution.

    Subtitle splitting strategy:
    1. Split by sentence-ending punctuation (。！？.!?)
    2. Within each sentence, try to split at clause punctuation (，、；,;)
       keeping each chunk <= max_chars_per_line
    3. Hard-force-break any remaining chunk that still exceeds max_chars_per_line
    """
    # Step 1: split by sentence endings
    sentences = re.split(r'(?<=[。！？.!?])', script_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        sentences = [script_text.strip()] if script_text.strip() else [""]

    # Step 2: within each sentence, split at clause punctuation greedily
    chunks = []
    for sent in sentences:
        if len(sent) <= max_chars_per_line:
            chunks.append(sent)
        else:
            parts = re.split(r'(?<=[，、；,;])', sent)
            parts = [p for p in parts if p]
            current = ""
            for part in parts:
                if len(current) + len(part) <= max_chars_per_line:
                    current += part
                else:
                    if current:
                        chunks.append(current)
                    current = part
            if current:
                chunks.append(current)

    # Step 3: hard-break any chunk still exceeding max_chars_per_line
    lines_list = []
    for chunk in chunks:
        while len(chunk) > max_chars_per_line:
            lines_list.append(chunk[:max_chars_per_line])
            chunk = chunk[max_chars_per_line:]
        if chunk:
            lines_list.append(chunk)

    # ---- Timing: use word-level timestamps if available, else proportional ----
    if word_timings and len(word_timings) > 0:
        # edge-tts WordBoundary events contain only spoken words (no punctuation).
        # Build a character-index → time mapping from word timings.
        tts_text = "".join(w[0] for w in word_timings)  # spoken text, no punctuation
        # Per-character timing data
        char_times = []  # (start_sec, end_sec) per character in tts_text
        for word_text, w_start, w_end in word_timings:
            n = len(word_text)
            if n == 0:
                continue
            for j in range(n):
                t_start = w_start + (w_end - w_start) * j / n
                t_end = w_start + (w_end - w_start) * (j + 1) / n
                char_times.append((t_start, t_end))

        # Strip punctuation from script text to match tts_text
        punctuation_pat = re.compile(r'[。，！？.!?,;:：；、\s]')
        clean_full = punctuation_pat.sub('', script_text)

        # Map each subtitle line (without punctuation) to character positions in clean_full/tts_text
        result_timings = []
        search_pos = 0
        for line in lines_list:
            clean_line = punctuation_pat.sub('', line)
            idx = clean_full.find(clean_line, search_pos)
            if idx >= 0 and idx < len(char_times) and idx + len(clean_line) - 1 < len(char_times):
                seg_start = char_times[idx][0]
                seg_end = char_times[idx + len(clean_line) - 1][1]
                result_timings.append((seg_start, seg_end, line))
                search_pos = idx + len(clean_line)
            else:
                result_timings.append(None)  # signal fallback needed

        # Fallback: for any line without timing, use proportional distribution
        # IMPORTANT: current_fallback must track the last known end time so fallback
        # segments do not overlap with word-timed segments.
        total_chars = sum(len(l) for l in lines_list)
        if total_chars == 0:
            total_chars = 1
        final_timings = []
        last_end = 0.0  # tracks the end of the last appended segment
        for i, line in enumerate(lines_list):
            if i < len(result_timings) and result_timings[i] is not None:
                seg_st, seg_et, seg_line = result_timings[i]
                # Ensure no overlap with previous segment
                seg_st = max(seg_st, last_end)
                # Ensure minimum duration of 0.3s
                if seg_et - seg_st < 0.3:
                    seg_et = seg_st + max(0.3, len(seg_line) * 0.12)
                seg_et = min(seg_et, tts_duration)
                final_timings.append((seg_st, seg_et, seg_line))
                last_end = seg_et
            else:
                line_dur = max(1.0, len(line) * 0.15)  # ~0.15s per character
                st = last_end
                et = min(st + line_dur, tts_duration)
                final_timings.append((st, et, line))
                last_end = et
    else:
        # Proportional fallback
        total_chars = sum(len(l) for l in lines_list)
        if total_chars == 0:
            total_chars = 1
        current_time = 0.0
        final_timings = []
        for line in lines_list:
            line_dur = max(1.5, (len(line) / total_chars) * tts_duration)
            st = current_time
            et = min(current_time + line_dur, tts_duration)
            final_timings.append((st, et, line))
            current_time = et

    print(f"    Generating ASS ({len(final_timings)} segments, TTS duration: {tts_duration:.1f}s)...")

    # Calculate MarginV in ASS PlayResY=288 space:
    # - 竖版 (portrait): ~384px from video bottom
    # - 横版 (landscape): 1/4 of video height from bottom
    # ASS coordinates are scaled from PlayResY to video_height.
    if is_landscape:
        # 横版：视频下方往上的四分之一处
        margin_v_px = video_height * 0.25
        margin_v_ass = max(10, int(margin_v_px * ASS_PLAYRES_Y / video_height))
        print(f"    Landscape video: margin={margin_v_px:.0f}px from bottom (1/4 of {video_height}px)")
    else:
        # 竖版：保持原位置 ~384px from bottom
        margin_v_px = 384
        margin_v_ass = max(10, int(margin_v_px * ASS_PLAYRES_Y / video_height))
        print(f"    Portrait video: margin=384px from bottom")

    # Build ASS file content
    ass_lines = []
    ass_lines.append("[Script Info]")
    ass_lines.append("ScriptType: v4.00+")
    ass_lines.append(f"PlayResX: {ASS_PLAYRES_X}")
    ass_lines.append(f"PlayResY: {ASS_PLAYRES_Y}")
    ass_lines.append("ScaledBorderAndShadow: yes")
    ass_lines.append("")

    ass_lines.append("[V4+ Styles]")
    ass_lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    ass_lines.append(f"Style: Default,Microsoft YaHei,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,{margin_v_ass},1")
    ass_lines.append("")

    ass_lines.append("[Events]")
    ass_lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    # Use final_timings (word-aligned or proportional) to generate dialogues
    for st, et, line in final_timings:
        if not line:
            continue
        # Guard: end must be strictly after start
        if et <= st:
            et = st + max(0.3, len(line) * 0.12)
        start_str = format_ass_time(st)
        end_str = format_ass_time(et)

        # Escape ASS special characters in text: { } are used for override tags
        safe_text = line.replace("{", "\\{").replace("}", "\\}")

        ass_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{safe_text}")

    # Extend last entry to fill up to tts_duration if it ends early
    # Use safe split: only split on the first 9 commas (Dialogue has 9 fixed fields before Text)
    if ass_lines and final_timings and final_timings[-1][1] < tts_duration - 0.1:
        last_dialogue_idx = len(ass_lines) - 1
        last_line = ass_lines[last_dialogue_idx]
        # Split into exactly 10 parts: fields 0-8 + text (which may contain commas)
        parts = last_line.split(",", 9)
        if len(parts) >= 3:
            parts[2] = format_ass_time(tts_duration)
            ass_lines[last_dialogue_idx] = ",".join(parts)

    ass_content = "\n".join(ass_lines)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    print(f"    ✓ ASS file saved ({ass_path}), MarginV={margin_v_ass} (ASS-space)")
    return ass_path


def _get_audio_duration(audio_path):
    """Get duration of an audio file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except (ValueError, IndexError):
        return 30.0  # fallback


# ============================================================
# Step 6: Composite final video
# ============================================================

def composite_video(video_path, tts_path, ass_path, output_path, 
                    tts_duration=None, video_duration=None,
                    original_audio_volume=0.3, tts_volume=1.5, video_height=720):
    """
    Final composition (video-duration is the hard upper limit):
    - Original video with original audio reduced to `original_audio_volume`
    - TTS voiceover mixed in at `tts_volume` volume
    - Subtitles burned in via ASS filter (transparent BG, 384px from bottom, single line)
    - If TTS > video: speed up TTS via atempo (max 1.15x) so narration fits.
    - If TTS < video: slow down TTS via atempo (min 0.85x) to stretch audio.
    - Subtitles are re-timed proportionally when TTS speed is adjusted.
    """

    print(f"    Composing final video...")

    # --- Determine effective TTS path (may be sped up) ---
    effective_tts = Path(tts_path)
    tts_sped_up = False
    tts_speed = 1.0  # no speed change by default
    if tts_duration is not None and video_duration is not None:
        ratio = tts_duration / max(video_duration, 0.5)
        if ratio > 1.01:  # TTS is longer → speed up
            needed_speed = min(ratio, 1.30)  # cap at 1.30x
            print(f"    ⚠ TTS ({tts_duration:.1f}s) > Video ({video_duration:.1f}s), "
                  f"speed up {needed_speed:.2f}x via atempo")
            sped_tts = output_path.with_name(output_path.stem + "_tts_sped.mp3")
            atempo_cmd = [
                "ffmpeg", "-i", str(tts_path),
                "-filter:a", f"atempo={needed_speed:.4f}",
                "-y", str(sped_tts)
            ]
            run_cmd(atempo_cmd, f"  Speed up TTS ({needed_speed:.2f}x)")
            effective_tts = sped_tts
            tts_sped_up = True
            tts_speed = needed_speed
        elif ratio < 0.99:  # TTS is shorter → slow down to stretch
            needed_speed = max(ratio, 0.75)  # don't go below 0.75x (audio quality)
            print(f"    ⚠ TTS ({tts_duration:.1f}s) < Video ({video_duration:.1f}s), "
                  f"slow down {needed_speed:.2f}x via atempo to stretch audio")
            sped_tts = output_path.with_name(output_path.stem + "_tts_sped.mp3")
            atempo_cmd = [
                "ffmpeg", "-i", str(tts_path),
                "-filter:a", f"atempo={needed_speed:.4f}",
                "-y", str(sped_tts)
            ]
            run_cmd(atempo_cmd, f"  Slow down TTS ({needed_speed:.2f}x)")
            effective_tts = sped_tts
            tts_sped_up = True  # reuse flag so subtitles get re-timed
            tts_speed = needed_speed
        else:
            print(f"    ✓ TTS ({tts_duration:.1f}s) ≈ Video ({video_duration:.1f}s), "
                  f"lengths match OK")

    # --- Regenerate ASS with speed-adjusted timings if TTS speed was changed ---
    if tts_sped_up:
        print(f"    Re-timing subtitles for speed-adjusted TTS ({tts_speed:.2f}x)...")
        _retime_ass_for_speed(ass_path, tts_speed)

    # Temp file for mixed audio
    mixed_audio = output_path.with_name(output_path.stem + "_mixed_audio.aac")

    # Step 6a: Mix original audio (reduced) + TTS audio (boosted)
    # duration=first ensures mixed audio ends with the video's audio length
    print(f"    Mixing audio tracks (TTS volume: {tts_volume}x)...")
    mix_cmd = [
        "ffmpeg",
        "-i", str(video_path),                # input 0: original video (with audio)
        "-i", str(effective_tts),              # input 1: TTS audio (possibly sped up)
        "-filter_complex",
        f"[0:a]volume={original_audio_volume}[orig];"
        f"[1:a]volume={tts_volume}[tts];"
        f"[orig][tts]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "[aout]",
        "-y", str(mixed_audio)
    ]
    run_cmd(mix_cmd, "  Audio mixing")

    # Step 6b: Composite video + mixed audio + subtitles via ASS filter
    print(f"    Adding subtitles and finalizing...")

    # Copy ASS to a simple temp path (neutral name, no special chars)
    import shutil, tempfile
    subs_workdir = Path(tempfile.gettempdir()) / "vidauto_subs"
    os.makedirs(str(subs_workdir), exist_ok=True)
    simple_ass = subs_workdir / "subs.ass"
    shutil.copy2(ass_path, simple_ass)

    # Always use -shortest: video duration is the hard limit
    final_cmd = [
        "ffmpeg",
        "-i", str(video_path),                # original video (video stream)
        "-i", str(mixed_audio),               # mixed audio
        "-vf", "ass=subs.ass",
        "-map", "0:v",                         # video from original
        "-map", "1:a",                         # audio from mixed
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-y", str(output_path)
    ]
    run_cmd(final_cmd, "  Final composition", cwd=str(subs_workdir))

    # Cleanup temp ASS
    if simple_ass.exists():
        simple_ass.unlink()
    try:
        subs_workdir.rmdir()
    except OSError:
        pass

    # Cleanup temp files
    if mixed_audio.exists():
        mixed_audio.unlink()
    if tts_sped_up and effective_tts.exists():
        effective_tts.unlink()

    print(f"    ✓ Final video: {output_path}")


def _retime_ass_for_speed(ass_path, speed_factor):
    """Rescale all timestamps in an ASS file by speed_factor (e.g. 1.10 = 10% faster)."""
    import re
    with open(ass_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    result = []
    time_re = re.compile(
        r"(Dialogue:\s*\d+,\s*)(\d+:\d{2}:\d{2}\.\d{2})(\s*,\s*)(\d+:\d{2}:\d{2}\.\d{2})(.*)"
    )

    def _scale_time(ts_str):
        """Scale an ASS timestamp (H:MM:SS.cs) by speed_factor."""
        parts = ts_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s_cs = parts[2].split(".")
        s = int(s_cs[0])
        cs = int(s_cs[1])
        total_cs = ((h * 3600 + m * 60 + s) * 100 + cs) / speed_factor
        total_cs = int(total_cs)
        cs = total_cs % 100
        total_s = total_cs // 100
        h = total_s // 3600
        m = (total_s % 3600) // 60
        s = total_s % 60
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    for line in lines:
        m = time_re.match(line)
        if m:
            start_scaled = _scale_time(m.group(2))
            end_scaled = _scale_time(m.group(4))
            line = f"{m.group(1)}{start_scaled}{m.group(3)}{end_scaled}{m.group(5)}\n"
        result.append(line)

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(result)


# ============================================================
# Process single video (full pipeline)
# ============================================================

def process_single_video(video_path, api_key, output_dir, temp_dir):
    """Run the full pipeline on one video file."""
    video_stem = video_path.stem
    print(f"\n{'='*60}")
    print(f"Processing: {video_path.name}")
    print(f"{'='*60}")

    # Video-specific temp directory
    video_temp = Path(temp_dir) / video_stem
    os.makedirs(str(video_temp), exist_ok=True)

    frames_dir = video_temp / "frames"
    os.makedirs(str(frames_dir), exist_ok=True)

    # ------ Step 2: Extract frames ------
    print(f"\n[Step 2] Extracting frames (every {FRAME_INTERVAL}s)...")
    try:
        frame_files, timestamps, fps, duration, video_width, video_height = extract_frames(video_path, frames_dir)
        # Detect video orientation
        is_landscape = video_width >= video_height
        orientation = "横版(landscape)" if is_landscape else "竖版(portrait)"
        print(f"    Video: {video_width}x{video_height} → {orientation}")
    except Exception as e:
        print(f"  ✗ Frame extraction failed: {e}")
        return False

    if not frame_files:
        print(f"  ⚠ No frames extracted, skipping.")
        return False

    # ------ Step 3: Generate script via 百炼 Vision API ------
    print(f"\n[Step 3] Generating script via 百炼 Vision API...")
    try:
        dynamic_prompt = build_script_prompt(duration)
        script_text = call_bailian_vision(api_key, frame_files, dynamic_prompt)
        # Save script to file
        script_file = video_temp / f"{video_stem}_script.txt"
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(script_text)
        print(f"    Script saved: {script_file}")
    except Exception as e:
        print(f"  ✗ Script generation failed: {e}")
        traceback.print_exc()
        return False

    # ------ Step 4: Generate TTS ------
    print(f"\n[Step 4] Generating TTS voiceover...")
    tts_file = video_temp / f"{video_stem}_tts.mp3"
    word_timings = None
    try:
        _, word_timings = generate_tts(script_text, tts_file)
    except Exception as e:
        print(f"  ✗ TTS generation failed: {e}")
        traceback.print_exc()
        return False

    # Get actual TTS duration for subtitle sync
    tts_duration = _get_audio_duration(tts_file)
    print(f"    TTS duration: {tts_duration:.1f}s")

    # ------ Step 5: Generate ASS subtitles (synced with TTS word timings) ------
    print(f"\n[Step 5] Generating subtitles (word-level sync)...")
    ass_file = video_temp / f"{video_stem}_subs.ass"
    try:
        generate_ass(script_text, ass_file, tts_duration, video_height, word_timings=word_timings, is_landscape=is_landscape)
    except Exception as e:
        print(f"  ✗ Subtitle generation failed: {e}")
        return False

    # ------ Step 6: Composite final video ------
    print(f"\n[Step 6] Composing final video...")
    output_file = Path(output_dir) / f"{video_stem}_添加口播.mp4"
    try:
        composite_video(video_path, tts_file, ass_file, output_file, 
                        tts_duration=tts_duration, video_duration=duration,
                        video_height=video_height)
    except Exception as e:
        print(f"  ✗ Video composition failed: {e}")
        traceback.print_exc()
        return False

    print(f"\n  ✓ Done! Output: {output_file}")
    return True


# ============================================================
# Main
# ============================================================

def main():
    global FRAME_INTERVAL
    parser = argparse.ArgumentParser(
        description="Video Automation: 抽帧 → AI生成文案 → 合成字幕视频"
    )
    parser.add_argument("--input", "-i", required=True,
                        help="Input folder containing video files")
    parser.add_argument("--output", "-o", required=True,
                        help="Output folder for processed videos")
    parser.add_argument("--api-key", "-k",
                        default=os.environ.get("BAILIAN_API_KEY", ""),
                        help="百炼 API Key (or set BAILIAN_API_KEY env var)")
    parser.add_argument("--interval", type=int, default=FRAME_INTERVAL,
                        help=f"Frame extraction interval in seconds (default: {FRAME_INTERVAL})")
    parser.add_argument("--volume", type=float, default=0.3,
                        help="Original video volume after reduction (0.0-1.0, default: 0.3)")

    args = parser.parse_args()

    # Validate
    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"✗ Input folder not found: {input_dir}")
        sys.exit(1)

    output_dir = Path(args.output)
    os.makedirs(str(output_dir), exist_ok=True)

    if not args.api_key:
        print("✗ 百炼 API Key required. Set via --api-key or BAILIAN_API_KEY env var.")
        print("  Get your API key from: https://bailian.console.aliyun.com")
        sys.exit(1)

    # Override interval
    FRAME_INTERVAL = args.interval

    # ------ Step 0: Check prerequisites ------
    check_prerequisites()

    # ------ Step 1: Find videos ------
    print("\n[Step 1] Scanning for video files...")
    videos = find_videos(input_dir)
    if not videos:
        print(f"  No video files found in: {input_dir}")
        sys.exit(0)

    print(f"  Found {len(videos)} video(s):")
    for v in videos:
        print(f"    - {v.relative_to(input_dir) if v.is_relative_to(input_dir) else v.name}")

    # Create temp directory for intermediate files
    temp_dir = output_dir / ".temp_video_processing"
    import os as _os
    _os.makedirs(str(temp_dir), exist_ok=True)

    # Process each video
    success_count = 0
    for video in videos:
        ok = process_single_video(video, args.api_key, output_dir, temp_dir)
        if ok:
            success_count += 1

    # Cleanup temp directory
    print(f"\nCleaning up temp files...")
    import shutil
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Summary
    print(f"\n{'='*60}")
    print(f"Pipeline complete. {success_count}/{len(videos)} videos processed successfully.")
    print(f"Output folder: {output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

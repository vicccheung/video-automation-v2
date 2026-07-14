# Video Automation V2 — 视频自动化处理（横竖版自适应）

## Overview

本 Skill 是 video-automation 的增强版本，新增横竖版视频自动识别与差异化字幕定位功能。

**新增特性：**
- 自动检测视频横竖版方向（landscape / portrait）
- 竖版视频：字幕位置保持不变（底部 ~384px）
- 横版视频：字幕自动调整到视频下方 1/4 处，避免遮挡画面主体
- 优化百炼千问 Prompt：强制以"这是一款***的游戏"开头，杜绝"你敢信"等低质开头

工作流：**扫描视频 → 抽帧 → AI 视觉分析生成文案 → TTS 配音 → 字幕生成 → 合成输出**。

## Workflow Decision Tree

1. **用户要求处理视频文件夹** → 直接运行 `video_automation.py` 主脚本
2. **用户要求微调文案/配音/字幕** → 运行后修改对应中间文件，重新合成
3. **用户没有百炼 API Key** → 引导用户前往 https://bailian.console.aliyun.com 获取

## 前置条件检查

### 1. FFmpeg

```bash
ffmpeg -version
```

如果不可用：
- **Windows**: 运行原版 skill 中的 `scripts/install_ffmpeg_windows.py` 自动下载
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg`

### 2. Python 包

主脚本自动检测 `requests` 和 `edge-tts` 并安装到 venv。

### 3. 百炼 API Key

设置环境变量：`export BAILIAN_API_KEY=sk-xxxx` 或通过 `--api-key` 参数传入。

## 核心流水线

### Step 1 — 扫描视频素材

递归扫描输入文件夹，支持格式：`.mp4`, `.mov`, `.avi`, `.mkv`, `.flv`, `.wmv`, `.webm`。

### Step 2 — 抽帧 + 方向检测

使用 FFmpeg 按固定时间间隔提取 JPEG 关键帧，同时通过 ffprobe 获取视频宽高，判断横竖版方向。

**方向判断规则：** `video_width >= video_height` → 横版；否则 → 竖版。

### Step 3 — AI 视觉分析生成文案

使用百炼 Qwen 视觉模型分析画面，Prompt 要求：
- **必须以"这是一款***的游戏"开头**，根据画面自动判断游戏类型
- 严禁使用"你敢信的"、"你敢信"等开头句式
- 150-200字，30-60秒口播时长

### Step 4 — TTS 配音

使用 edge-tts（微软免费 TTS），默认音色 `zh-CN-YunjianNeural`（云健激情男声），语速 +10%。

### Step 5 — 生成字幕（横竖版自适应）

基于 edge-tts 词级时间戳生成逐字同步 ASS 字幕：

| 视频类型 | 字幕位置 | margin_v（ASS 坐标系） |
|---------|---------|----------------------|
| 竖版 (portrait) | 距底部 ~384px | `384 * 288 / video_height` |
| 横版 (landscape) | 距底部 1/4 高度 | `0.25 * 288` |

其他字幕样式（字体、颜色、描边、换行规则）与 V1 保持一致。

### Step 6 — 合成最终视频

FFmpeg 两步合成：混合音频（原视频音量降至 30% + TTS 1.5x 增益）→ 烧录 ASS 字幕。

## 使用方法

```bash
# 推荐：使用环境变量
export BAILIAN_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
python3 scripts/video_automation.py --input ./raw_videos --output ./output

# 或直接传参
python3 scripts/video_automation.py -i ./raw_videos -o ./output -k sk-xxxxx

# 自定义参数
python3 scripts/video_automation.py -i ./game_videos -o ./output --interval 3 --volume 0.2
```

### 参数说明

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--input` | `-i` | 输入视频文件夹（必填） | — |
| `--output` | `-o` | 输出文件夹（必填） | — |
| `--api-key` | `-k` | 百炼 API Key | 环境变量 |
| `--interval` | — | 抽帧间隔（秒） | 5 |
| `--volume` | — | 原视频音量保留比例 | 0.3 |

## 输出文件结构

```
output/
├── video1_添加口播.mp4          # 最终合成视频
├── video2_添加口播.mp4
└── .temp_video_processing/   # 临时文件（处理完自动删除）
```

## 与原版区别

| 特性 | V1 | V2 |
|------|----|----|
| 横竖版识别 | 不支持 | 自动识别 |
| 横版字幕位置 | 固定在底部 | 距底部 1/4 处 |
| 竖版字幕位置 | 底部 ~384px | 底部 ~384px（不变） |
| Prompt 开头要求 | 无特殊要求 | 强制"这是一款***的游戏"开头 |
| "你敢信"禁用 | 未禁止 | 明确禁止 |

## 故障排除

参考原版 video-automation skill 的故障排除章节。

## Resources

### scripts/
| 文件 | 说明 |
|------|------|
| `video_automation.py` | V2 主流水线脚本：抽帧+方向检测 → AI分析 → TTS → 横竖版自适应字幕 → 合成 |

# Video Automation V2 — 视频自动化处理 Skill

> 横竖版视频自适应的游戏推广视频自动化流水线：抽帧 → AI 视觉分析生成口播文案 → TTS 配音 → 横竖版自适应字幕 → 合成输出。

本仓库是一个 [WorkBuddy](https://www.codebuddy.cn/) 的 **Skill**（技能），是 `video-automation` 的增强版。

## 新增特性（相比 V1）

- 自动检测视频横竖版方向（landscape / portrait）
- 竖版视频：字幕位置保持不变（底部 ~384px）
- 横版视频：字幕自动调整到视频下方 1/4 处，避免遮挡画面主体
- 优化百炼千问 Prompt：强制以「这是一款***的游戏」开头，杜绝「你敢信」等低质开头

## 工作流

```
扫描视频 → 抽帧 → AI 视觉分析生成文案 → TTS 配音 → 字幕生成 → 合成输出
```

## 前置条件

1. **FFmpeg**：`ffmpeg -version` 可用（不可用请自行安装：`brew install ffmpeg` / `sudo apt install ffmpeg` / Windows 用 scoop 安装）
2. **Python 3.10+**
3. **百炼（阿里云 DashScope）API Key**：https://bailian.console.aliyun.com

## 安装方式

### 方式 A：直接克隆到 WorkBuddy 技能目录（推荐）

```bash
git clone https://github.com/OWNER/video-automation-v2.git \
  ~/.workbuddy/skills/video-automation-v2
```

重启 WorkBuddy 后即可在对话中通过 `@skill:video-automation-v2` 调用，或在设置中启用该技能。

### 方式 B：下载 ZIP 解压

1. 在 GitHub 页面点击 **Code → Download ZIP**
2. 解压后，将整个文件夹重命名为 `video-automation-v2`
3. 移动到 `~/.workbuddy/skills/` 目录下

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

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--input` | `-i` | 输入视频文件夹（必填） | — |
| `--output` | `-o` | 输出文件夹（必填） | — |
| `--api-key` | `-k` | 百炼 API Key | 环境变量 |
| `--interval` | — | 抽帧间隔（秒） | 5 |
| `--volume` | — | 原视频音量保留比例 | 0.3 |

## 输出结构

```
output/
├── video1_添加口播.mp4          # 最终合成视频
├── video2_添加口播.mp4
└── .temp_video_processing/   # 临时文件（处理完自动删除）
```

## 依赖

脚本运行时会自动检测并安装如下 Python 包：`requests`、`edge-tts`。
也可手动安装：`pip install -r requirements.txt`。

## License

[MIT](./LICENSE)

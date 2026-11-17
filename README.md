# DubCanvas

Create subtitles and multilingual video dubs on your Mac. Each project contains one source video and multiple dub versions, so you can try another language or voice without repeating source analysis.

## Install

**Requires Apple Silicon and macOS 14 or later.**

1. Download the latest ZIP from [Releases](https://github.com/wassayhassan/DubCanvas/releases/tag/app-updates).
2. Unzip it, move **DubCanvas.app** to **Applications**, and open it.
3. Choose **Download and Set Up**. The app installs its runtime and tools; Terminal, Homebrew, and Xcode are not required.

Keep an internet connection during setup and initial model downloads. Models can be large. Use **Check for Updates** in the app to install newer builds.

## Make a dub

1. Choose **New Project**, name it, and add a video file or video URL.
2. Select a target language and start a dub, or create a subtitles-only project.
3. Follow progress in the project, then preview and export the result.
4. Choose **New Dub** within the project to try another language or voice.

- **Voices:** English dubs default to Kokoro preset voices. Voice cloning is optional through Chatterbox and source voice references in Advanced. Other languages require a compatible engine, such as Chatterbox Multilingual or ElevenLabs; cloud providers require credentials.
- **Outputs:** SRT/VTT subtitles and dubbed video. Review names, translations, and timing before publishing.
- **Resume:** Keep the project's work folder to reuse analysis and completed stages.
- **Troubleshooting:** Open **Activity & Logs** to copy or save stage logs. Session and update logs are in `~/Library/Logs/DubCanvas`.

### Experimental quality pipeline

Macs with at least 48 GB memory can opt into the Chinese → English quality pipeline during setup or in **Settings → Quality Pipeline**. It uses additional models and isolated runtimes, and requires a Hugging Face read token and acceptance of speaker-model access conditions. Real-model performance and listening quality on M5 Max have not been benchmarked.

See [quality pipeline setup and limitations](docs/quality-pipeline.md) before enabling it.

## Run from source

```bash
git clone https://github.com/wassayhassan/DubCanvas.git
cd DubCanvas
zsh setup.sh
```

Optional local voice engines: `zsh macos/install_voice_engines.sh`.

### Command line

The CLI supports macOS, Linux, and Windows. On Linux or Windows, install FFmpeg/ffprobe, then run `bash setup-cross-platform.sh` or `.\setup-cross-platform.ps1`. Activate the installed environment before running:

```bash
python -m dubcanvas doctor
python -m dubcanvas run /path/to/video.mp4 -o ./output --target-language en
python -m dubcanvas --help
```

The legacy `python -m anime_dubber.cli` command still works. Existing `AnimeDubberOutput` folders remain supported; use `-o` to select a different output folder.

### Development checks

```bash
python -m pip install -c constraints.txt numpy soundfile opencv-python-headless psutil==7.2.2
python verify_source.py
python -m unittest discover -s tests
```

The Python suite focuses on end-to-end media processing, project data and recovery, timing, setup, and update safety. Small helper and configuration checks are intentionally limited to keep maintenance manageable.

Install FFmpeg/ffprobe and Rubber Band to exercise media integration tests. On macOS, run `swift test --package-path macos/DubCanvasApp` for the native app. Tests use model substitutes; passing them does not establish real-model dubbing quality.

More detail: [project architecture](docs/PROJECT_WORKSPACE_VNEXT.md) · [macOS interface design](docs/SWIFTUI_REDESIGN_SPEC.md).

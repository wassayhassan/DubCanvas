# DubCanvas

DubCanvas creates subtitles and multilingual dubs for videos. The macOS app organizes work into projects: each project holds one source video, shared speaker and subtitle analysis, and any number of dub versions. You can make another language or voice version without starting a separate project.

## Get started on macOS

Requires an Apple silicon Mac running macOS 14 or later. Install FFmpeg if setup asks for it, then run:

```bash
git clone https://github.com/wassayhassan/DubCanvas.git
cd DubCanvas
zsh setup.sh
```

Setup installs dependencies, runs checks, and installs the app in `/Applications`. Open the app from Finder or Spotlight. The installed app bundle is named `DubCanvas.app`. Existing installs of `AnimeDubber.app` can be removed after confirming the new app works.

1. Choose **New Project** and add a local video or video page URL.
2. Choose a target language and start the first dub, or create the project with subtitles only.
3. Open the project to track progress and download subtitles as soon as they are ready.
4. Open a completed dub to preview and export it. Use **New Dub** inside that project for another language, voice, or model. Use **Compare Versions** to review two completed dubs.

Automatic settings choose available local models. The first run may download model weights. For the best local voices, install the optional voice engines:

```bash
zsh macos/install_voice_engines.sh
```

Chatterbox and Kokoro provide local voices; ElevenLabs is optional and requires an API key in Settings. Non-English dubbing needs a multilingual voice engine such as Chatterbox Multilingual or ElevenLabs. If no suitable voice engine is available, subtitles can still be generated.

## Command line

The Python CLI also runs on macOS, Windows, and Linux. Install FFmpeg and ffprobe first. On Linux, run `bash setup-cross-platform.sh`; on Windows, run `.\setup-cross-platform.ps1` in PowerShell.

```bash
python -m dubcanvas doctor
python -m dubcanvas new-project /path/to/video.mp4 -o ./output --name "My video"
python -m dubcanvas run /path/to/video.mp4 -o ./output --target-language en
python -m dubcanvas projects -o ./output
```

Run `python -m dubcanvas --help` for more commands and options. The old `python -m anime_dubber.cli` entry point still works with existing scripts.
If an existing `AnimeDubberOutput` folder is present, the CLI continues using it by default. Pass `-o` to choose another folder.

## Notes

- Supported target languages in the macOS app include English, Spanish, French, German, Japanese, Korean, Chinese, Portuguese, Italian, Hindi, and Arabic. Voice availability depends on installed models and providers.
- Subtitles are saved as SRT/VTT files; finished dub versions include an exported video and review details.
- DubCanvas checks a clearly visible single face for speech onset and makes small automatic audio timing corrections when confident. It keeps source timing for animated faces it cannot track, off-screen speakers, and ambiguous shots; it does not redraw mouths.
- Results are generated automatically, so check names, translations, and timing before publishing.
- Keep the project's work folder if you want to pause or resume a dub.

For architecture and detailed design, see [the project workspace notes](docs/PROJECT_WORKSPACE_VNEXT.md) and [the SwiftUI design specification](docs/SWIFTUI_REDESIGN_SPEC.md).

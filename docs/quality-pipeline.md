# Quality v1: implementation and operating guide

This profile is connected to project analysis, subtitle-only jobs and full dub jobs. It is experimental: real MLX/MPS inference and M5 Max / 48 GB performance have not been verified in the Linux development environment. Existing providers remain the default; existing preferences, character edits and completed versions are preserved.

## What runs

BandIt v2 DnR-v3 dialogue/music/effects separation → pyannote Community-1 overlapping tracks → FireRedASR2-AED plus Qwen3-ASR on original and separated audio → Whisper large-v3 fallback for disputed windows → Qwen forced alignment → evidence-linked glossary and Hy-MT2 translation → independent Qwen3.8 review of every cue → complete SRT/VTT → immutable version cast → Qwen CustomVoice/Kokoro/opt-in Eleven v4/optional IndexTTS2.5 → English recognition/alignment QA → bounded timing and source-aware rewrite → pitch-preserving Rubber Band → background-preserving mix, two-pass loudness and validated mux.

Actual adapter execution is not the same as passing a model-quality benchmark. The stage's imports/APIs have been checked against primary implementation sources; model parity, audio quality and hardware performance remain release gates. Read [the requirement checklist](quality-pipeline-checklist.md) for the precise status of each acceptance criterion.

## Setup on an Apple Silicon Mac

Use native ARM64 Python 3.13 plus Python 3.12 for the diarization/Kokoro environment. Kokoro 0.9.4 explicitly excludes Python 3.13; this was caught by native ARM64 dependency CI. Do not upgrade the existing Chatterbox environment to install this profile.

```sh
python3.13 tools/install_quality.py --directory "$HOME/DubCanvasQualityEnvironments"
```

The installer creates five separate environments, runs pip check and saves each environment's exact resolved lock. It refuses to overwrite environments and does not download weights or execute the Hy adapter. The input requirement files pin the selected core runtime versions; the generated resolved locks freeze their transitive dependencies on the target Mac. Installation has NOT been tested on that Mac yet.

- Audio: MLX 0.31.2, MLX Audio 0.5.9, Whisper fallback.
- Translation: MLX 0.31.2 / MLX-LM 0.31.3, matching the Hy adapter's documented smoke-test runtime.
- Review: MLX 0.32.0 / MLX-VLM 0.6.8. This runtime cannot use the older MLX pin.
- Separation: PyTorch/torchaudio 2.9.1 and BandIt model-only dependencies. Importing MSST's complete inference/training dependency set introduces unnecessary CUDA packages and incompatible pins.
- Diarization/Kokoro: Python 3.12, PyTorch/torchaudio 2.9.1, pyannote.audio 4.0.7 and matched torchcodec 0.8.1; inference receives waveform arrays to avoid unnecessary decoder dependence. Pass `--torch-python /path/to/python3.12` if it is not on PATH.

Install ARM64 FFmpeg 9.0.2 and Rubber Band 4.0.0. The Rubber Band executable must support `-3` (R3/Finer) and `-T` (tempo factor). The host FFmpeg used for development tests is a different version, so 9.0.2 remains a target-binary validation gate.

Obtain the audited MSST model code at commit `84b1eac0887756b4f1a9d7a1ff49105939749ed2`, the matching `config_dnr_bandit_v2_mus64.yaml`, and DnR-v3 `checkpoint-multi.ckpt` from the official sources. Do not install MSST's entire requirements into pyannote's environment. BandIt loads only its model subtree; DubCanvas implements bounded 8-second overlap-add inference inside contextual 12-second parent windows.

BandIt code is Apache-2.0, weights CC-BY-SA-4.0. pyannote weights require accepting the model access conditions; configure `HF_TOKEN` for the initial download. Telemetry is disabled in the worker environment.

Hy's conversion includes executable `hy_v3.py`. Audit that adapter and resolve the conversion's licensing metadata conflict with the checked official upstream Apache license before distributing it. Supply a local model directory and the adapter checksum. DubCanvas refuses to auto-execute an unaudited downloaded Hy adapter. This audit/distribution decision is a documented blocker to a packaged one-click install, not a silent fallback.

Create a quality JSON file with the worker paths from `worker-paths.json` and your audited asset paths/checksums. Complete example keys are available through `anime_dubber.quality_models.DEFAULTS`; `tools/write_quality_settings.py` exports them. The UI has dedicated path controls and an advanced JSON editor.

```sh
python tools/write_quality_settings.py --output quality-settings.json
python -m dubcanvas quality-check quality-settings.json --json
python -m dubcanvas analyze /path/to/source.mp4 --pipeline-profile quality_v1 --quality-settings quality-settings.json
python -m dubcanvas run /path/to/source.mp4 --pipeline-profile quality_v1 --quality-settings quality-settings.json --subtitles-only
python -m dubcanvas run /path/to/source.mp4 --pipeline-profile quality_v1 --quality-settings quality-settings.json
```

Quality-check verifies configuration/platform/tool presence without downloading models. It is NOT a successful inference test. In the GUI use Settings → Quality Pipeline → Check Quality Setup, then choose the profile before creating a dub. Different language pairs retain the existing providers.

## Safety and localization behavior

Every model worker owns one model stage. Threads and separate project-window backend processes share a model-execution lock. Worker cancellation, deadline and RSS/system budget checks terminate the process; the Metal memory limit is configured separately. RSS is not claimed to be full physical unified-memory accounting. Default worker limit is 36 GB, system target 42 GB, context budget 4,096 tokens.

Chinese recognition scans original and separated audio even where diarization is silent, preventing VAD-only dropping of short/quiet lines. It retains all hypotheses and provisional source subtitles. Independent recognizers must agree sufficiently; Whisper is a third acoustic opinion, not permission for an LLM to invent dialogue. The provisional 18% normalized disagreement threshold is an engineering heuristic requiring bilingual calibration, not a published confidence probability.

Forced alignment yields Chinese character/subword spans and final English word spans. It does not validate text correctness. Speaker boundaries split cues instead of merging neighboring male/female turns. Ambiguous true overlap currently blocks with exact time and saved hypotheses: separate-speaker ASR for simultaneous dialogue is not yet implemented. Acoustic IDs are stable for reused project analysis, but semantic character identity across episodes is not certified. Automatic age/sex detection is deliberately not claimed; identified female American roles can use Kokoro, and users can choose per-character presets. Those limitations prevent claiming universal one-click character casting.

Glossary JSON stores source/English names, type, aliases, pronunciation, evidence and confirmation. The realm-order field is editable and checked by the meaning-review prompt. Concurrent glossary changes are protected by a process lock and conflicting terminology aborts instead of overwriting another edit. For shared episodes set `series_glossary_path` and `episode_context_path` to common files; related prior dialogue is retrieved with source hash/cue provenance rather than an invented story summary.

Translation checkpoints include source text, model revision, adapter identity, glossary and adjacent context. The independent reviewer checks EVERY cue for propositions, names, ranks, numbers, negation, honorifics and tone. Its errors require actual source/target evidence. Up to two repair cycles run, with translator and reviewer never loaded together. Incomplete/unresolved output publishes partial subtitles and blocks speech. Faithful text, reviewed spoken adaptations and glossary history remain separate artifacts.

## Voices, timing and mixing

Qwen uses fixed CustomVoice IDs and instructions for natural, shouted or whispered speech. Voice references are never passed to preset generation. Aiden/Ryan offer English presets; female American presets need audition/validation, with Kokoro available per role. The quality profile's global engine is separate from legacy Provider controls. Per-version overrides do not change project character defaults.

Optional IndexTTS2.5 cloning requires both `reference_consent=true` and an actual reference clip. Install IndexTTS in its separate environment and configure `clone_python`, `clone_model_dir`, `clone_config`. Native duration factor stays 1.0, so post-processing is the single measured acceleration source. Native duration-search optimization is not promoted until it is validated on the Mac.

Timing first permits genuine same-scene silence (250 ms on-screen; 600 ms explicitly off-screen). It considers measured natural takes and independently reviewed concise alternatives. Normal stretch ≤1.15, urgent ≤1.25, shouts/whispers ≤1.10. Optional exceptional rescue allows up to 1.5 for ordinary delivery; it does not bypass content QA. All generated lines are jointly checked for unintended overlap and protected cuts. No acceptable fit saves clips/subtitles/report and blocks rendering. The old overlap-tolerating fallback has also been removed from the legacy workflow. Automatic scene expansion/variable video retiming is not implemented; an explicit blocked line is the current fallback.

English ASR/alignment checks synthesized and fitted takes. Small WER cannot hide dropped negation or changed numeric tokens. This cannot guarantee the absence of nonverbal noise or perfect name pronunciation; listening validation remains required. Articulation is kept at natural generation speed and Rubber Band R3 changes tempo without pitch shift.

The background bed is streamed in 48 kHz float stereo. Samples outside replacement fades use original audio; dialogue windows use music+effects stems. Full original mix is never reintroduced under English. Mastering uses measured two-pass loudness, reserves AAC peak headroom and checks delivered true peak before marking output complete. Stem leakage and separator boundary artifacts require real-clip listening tests; source separation is not promised to be lossless.

## Cloud options

`allow_cloud` must be a JSON boolean true; strings such as "false" are rejected. The GUI explains the upload and cost. Eleven v4 sends final voice text and a selected voice ID; existing Eleven credentials are supported. Qwen cloud review uses `DUBCANVAS_QWEN_API_KEY` in the backend environment and the selected HTTPS compatible endpoint/model. Neither service is called in local mode. Logs redact credentials. Credentials are not embedded in quality JSON or model-worker requests.

Research-date prices: Eleven v4 $0.08/1,000 characters; Qwen3.8-Max-0902 $2/million input + $6/million output. Verify current billing/retention/region terms before use. Cloud billing and output quality have not been tested here because account credentials were not available.

## Release validation

Local tests exercise real process RPC/deadlines, configuration compatibility, completeness/review gates, timing, cache invalidation, version isolation, and real FFmpeg media decoding/mixing/mux/resume with model-output fixtures. They are not model benchmarks.

Before promotion run a held-out 60–90-minute cultivation corpus with bilingual annotations and a full-episode soak on the M5 Max. Compare FireRed, Qwen and Whisper CER/critical names/coverage; score translation omissions, negation and realm consistency; blind-test casting/expression/accent; check source leakage/effects damage; record timing, processing time, physical memory and swap. The validation manifest/harness is in `tools/evaluate_quality.py`. Human quality fields remain unavailable until bilingual/listening raters supply them; the harness never invents them.

No model changes have been benchmarked on the user's Mac, and the experimental profile has not been promoted to the app default. See the final checklist for blockers and observed test results.

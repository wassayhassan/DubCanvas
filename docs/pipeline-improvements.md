# November 2026 pipeline changes

| Area | Implementation |
| --- | --- |
| TTS work | Raw synthesis and trimmed speech are cached independently of time windows, tempo, pitch and gain. Identical text/voice reuses synthesis across timing attempts; rendered files have separate signatures. WAV duration reads and runner memoization avoid repeated ffprobe processes. |
| Vocal timing | Streaming energy VAD on isolated vocals supplies onset/offset snapping and disjoint leading/trailing silence budgets. Unknown vocal activity is occupied time. Speed is capped at 1.5× before bounded rewrites. |
| Timing recovery | A failed fit produces a silent voice cue, retains the full translation, and publishes `needs_review` in the timing report. The rest finishes without extending the video. CLI `--timing-failure stop` restores strict behavior. This is an incomplete spoken dub, clearly reported—not a successfully voiced line. |
| Translation | Diarization and safe same-speaker fragment merging precede translation. Requests include neighboring dialogue, speaker tags and soft duration/syllable budgets. Per-line context caches invalidate only affected neighbors; retries vary temperature. Default legacy review uses Qwen3-14B, separate from the 8B translator. Existing saved model choices remain intact. |
| Project vocabulary | No built-in series names. Project Settings saves glossary, context and recognition hints. CLI exposes `--glossary`, `--context` and `--asr-prompt`. |
| Optional cloud | Explicitly selected OpenAI-compatible HTTPS chat-completions endpoint/model and per-job consent. API key is session-only in the app, or `DUBCANVAS_TRANSLATION_API_KEY` in the backend environment. Saved jobs redact it. No automatic cloud fallback. |
| Source analysis | Cancellable isolated ASR worker, vocal-boundary chunks, overlap ownership, per-chunk checkpoints and confidence-triggered original-audio retry per cue. Repeated subtitle-credit/outro hallucinations are rejected. Demucs streams progress and separates long files in bounded windows with context. |
| Background | Streaming numpy/soundfile blending uses vocal activity plus source subtitle guards, preserving background outside smooth replacement envelopes. This catches untranscribed vocal sounds too. |
| Audio | Source-window RMS sets voice levels; final integrated LUFS follows the original. Rubber Band independently changes tempo and pitch in one pass when the filter or bundled executable is available. ElevenLabs retries 429/5xx/network errors and requests PCM, with an explicit plan-dependent MP3 fallback. |
| Export | Original tracks, chapters and metadata are mapped alongside the default dub and soft subtitles. Try stream-copy MP4 then MKV; legacy fallback uses CRF 18. Quality mode never re-encodes video. |
| Release trust | Signed manifest, embedded Ed25519 public key, digest verification and rollback rejection. See [owner setup](signed-updates.md). Publishing remains disabled until the release key is configured. |
| CI | Python 3.11/3.13 tests on three platforms; read-only packaging; separate gated publisher; pinned action SHAs; concurrency controls; Ruff, incremental Pyright and uploaded coverage reports. Dependency resolution still uses each shipped role's actual Python version. |
| Structure | Separate translation, ASR chunking, vocal analysis, separation, export and loudness modules; `TimingResolver`; synthesis provider registry. Pipelines copy caller configuration; voice selection no longer swaps a shared config field. Runner stage/diagnostic calls use its existing null callbacks. Tracked bytecode removed. |

## Limits and verification

Energy VAD is deliberately conservative, not a learned speech classifier: separation bleed can consume a gap and separated dialogue may still contain residual vocals. Windows use overlapping context, but listening tests are still needed for boundary artifacts. Haar-based visual alignment is no longer run automatically. Neither VAD nor timing repair establishes semantic translation quality.

The suite keeps focused orchestration and real-FFmpeg integration coverage, including raw-cache reuse, local translation invalidation, chunk resume, vocal masking, export preservation and signature rejection. Models are substituted in tests; full-model performance, voice quality and memory behavior on an M5 Max still need a hardware run. Pyright currently covers the new audio-analysis, translation, synthesis-registry and signing modules; it is an incremental boundary, not whole-repository static validation.

Runtime requirement files remain separate because the core, speech, separator, translator and reviewer environments have conflicting Python/ML dependency constraints. Merging them would break installation. Developer and publisher dependencies live in `requirements/`. The old root reports were already removed; committed `tests/__pycache__` files are now removed too.

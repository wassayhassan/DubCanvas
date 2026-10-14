# Quality pipeline implementation checklist

Target: M5 Max / 48 GB. Created before implementation, 2026-10-13.

Status vocabulary: pending; implemented/tested locally; implemented/hardware blocked; blocked. An implemented adapter is not a successfully benchmarked model.

| ID | Requirement and code change | Acceptance criterion | Initial status |
|---|---|---|---|
| Q01 | Versioned quality profile, provider/model manifests, CLI/backend/SwiftUI settings; preserve legacy preferences | Profile round-trips, legacy projects load without migration loss, selected models visible | pending |
| Q02 | Model-owning isolated workers, memory/context/deadline limits, explicit dependencies | Worker cancellation/deadline/memory failure saves outputs and terminates process; no concurrent large models | pending |
| Q03 | BandIt three-stem streamed separation provider | Correct dialogue/music/effects duration/channel output; no whole-episode GPU buffer; Mac run passes | pending |
| Q04 | FireRed primary, Qwen independent ASR, Whisper fallback and acoustic disagreements | Every recognized interval retained; hypotheses saved; unresolved disagreement blocks dub; target clips pass bilingual checks | pending |
| Q05 | Qwen Chinese/English alignment | Real returned timestamps mapped to cues; alignment never represented as content validation | pending |
| Q06 | pyannote overlapping diarization and persistent character cast | Adjacent speakers distinct; original overlaps retained; identity persists across retries and versions | pending |
| Q07 | Hy-MT2 translation with bounded scene context and project/series glossary | Complete ID coverage; checkpoints invalidate by source/model/glossary; names/ranks consistent on bilingual evaluation | pending |
| Q08 | Independent Qwen3.8 source-aware meaning review and bounded repairs | Every cue reviewed; structured evidence/errors; no silent review failure; reviewed faithful and spoken texts retained | pending |
| Q09 | Complete SRT/VTT before synthesis, stop on incomplete translation | Failure retains source and partial translations; no TTS called for incomplete/unreviewed quality output | pending |
| Q10 | Qwen fixed-preset expressive voices, Kokoro per-character fallback, optional cloning | Immutable cast IDs; expressive instructions reach provider; no implicit reference conditioning; accent/voice listening gate | pending |
| Q11 | Adaptive timing, Rubber Band, scene/speaker constraints, combined 1.5 cap | No invented overlap/scene crossing/drop; style limits enforced; no fit produces checkpoint and blocked render | pending |
| Q12 | English synthesis content QA | Generated speech recognized/aligned; missing/additional words flagged before mix; no unexpected voiced burst passes | pending |
| Q13 | Preserve background and stereo, controlled loudness/true peak and mux verification | Original outside dialogue windows preserved; output valid; no source bleed/clipping on representative scenes | pending |
| Q14 | Per-stage logs, checkpoint manifests, retries and multiple versions | Copy/export logs reachable; source/model/settings identities saved; interruption/resume and parallel-version tests pass | pending |
| Q15 | Opt-in Eleven v4/Qwen cloud review with cost/privacy controls | No upload absent opt-in; API credentials redacted; configured cloud provider produces validated output | pending |
| Q16 | Optional IndexTTS2.5 clone/duration path, alternative model policy | Reference consent/capability explicit; actual Mac synthesis/combined-speed tests pass | pending |
| Q17 | Dependency locks, installation checks, documentation | Isolated ARM64 installs pass dependency checks; unsafe custom code not auto executed; license provenance explicit | pending |
| Q18 | Regression, backend E2E, Swift build, real-model/hardware/bilingual/soak evaluation | All available tests pass; unavailable hardware/media/model gates documented separately, never claimed passed | pending |

Environment blockers to verify: this workspace may be Linux rather than macOS; MPS/MLX/SwiftUI and 48 GB physical memory cannot be proven there. Real anime accuracy requires licensed clips plus bilingual reference annotations. Downloading model metadata is not inference. Custom Hy adapter and model licensing provenance must be audited before automatic distribution.

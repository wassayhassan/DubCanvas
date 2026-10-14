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

## Implementation status and acceptance evidence

Updated 2026-10-14. The table above is the pre-edit checklist. This table records the outcome; **implemented** does not mean a model passed real-world validation. No model was benchmarked on an M5 Max.

| ID | Software delivered and observed evidence | Remaining blocker / final status |
|---|---|---|
| Q01 | Actual project/analysis/dub dispatch, persisted experimental profile, GUI setup/model/voice controls, CLI, migration tests. Initial macOS CI Swift build/tests and universal bundle passed. | UI interaction testing on the target Mac outstanding; software integration tested, release acceptance hardware-blocked |
| Q02 | Separate audio/translation/review/separator/diarization environments; process-owned models; cross-backend execution lock; deadlines, cancellation, parent-death monitor, RSS/Metal budgets. Real RPC/timeout test passed. | Actual unified-memory footprint, memory-pressure behavior and full model unload on M5 need measurement |
| Q03 | Audited-checksum BandIt adapter, bounded 8-second stereo model windows, overlap-add and contextual parent chunks, three validated float stems, MPS→CPU operator fallback. | No real checkpoint inference on target hardware; separator artifacts/outer chunk seams require real-clip evaluation |
| Q04 | Original+dialogue FireRed/Qwen hypotheses, independent agreement gate, actual Whisper fallback route, provisional subtitles/checkpoints. | No Chinese corpus/gold audio available; disagreement thresholds, short-turn recall and name accuracy need calibration |
| Q05 | Chinese and generated-English Qwen aligner connected; timestamps used for cue bounds, trim and timing; missing alignment blocks output. | Real aligner port/content parity and CJK lexical segmentation need model testing; spans are honestly labeled character/subword |
| Q06 | Overlap tracks, boundary-based cue splitting, stable reused acoustic IDs, explicit ambiguous-overlap failure, per-project cast plus independent per-version snapshots. Version-leak regression test passed. | Separate-speaker ASR for simultaneous dialogue NOT implemented. Semantic character identity across episodes and automatic age/sex casting need an evaluated identity/classification method; current acoustic IDs cannot certify them |
| Q07 | Hy translator adapter, bounded neighboring context, glossary evidence/aliases/pronunciation/rank ledger, prior-episode evidence retrieval, revision-based caches and locked conflict-safe persistence. UTF-8 Windows regression covered. Static audit now pins the reviewed adapter hash and verifies config.model_file; tokenizer remote code disabled. | Conversion has an older license than current upstream; redistribution clearance and actual translation/quantization fidelity remain blocked |
| Q08 | Every cue receives independent Chinese/English proposition review; strict evidence schema; bounded repairs and terminology-conflict repair; faithful text separated from spoken adaptations. Gates tested with contract fixtures. | Real reviewer recall/fidelity and domain MQM evaluation require bilingual references; no claim that an LLM pass proves correctness |
| Q09 | Durable source and partial translations; complete source/translated SRT/VTT before synthesis; failures never start speech; partial/provisional exports visible in UI with explicit status. Local integration/gate tests passed. | Software acceptance met under tested provider contracts; successful real-model production run still blocked by Q03–Q08 |
| Q10 | Actual Qwen CustomVoice fixed IDs/instructions, Kokoro fallback per identified female role, voice preview/config, immutable casts, optional cloud/clone engines. | No blind voice/accent/emotion/character tests. Native-American female Qwen casting is limited; automatic semantic casting is not certified. Default promotion blocked |
| Q11 | Legal silence borrowing, cut/next-speaker constraints, source-aware reviewed concise alternatives, style-specific stretch limits, combined ceiling, English post-stretch QA, explicit no-fit checkpoint, legacy overlap fallback removed. Real Rubber Band 4.0 R3 CPU test passed. | Native duration/rate search and expanded-video retiming are NOT implemented; bounded, hard-constraint-checked visual onset hints ARE integrated; normal native generation plus DSP is the selected safe execution policy. Intelligibility/lip timing requires listening/visual tests |
| Q12 | Independent English ASR/alignment before and after fitting; numeric/negation safeguards; mismatch blocks mix. Regression tests cover lost words/protected tokens. | Real speech QA recall, name pronunciation and nonverbal noise detection need clips/listening; ASR WER cannot guarantee no noise |
| Q13 | 48 kHz float stereo streamed background, original samples outside dialogue fades, three-stem bed, two-pass loudness, AAC headroom, delivered true-peak/duration checks and valid mux. Real-media integration and exact-outside-window bed tests passed. | Real source leakage/music/SFX damage not evaluated; target FFmpeg 9.0.2 and Mac audio path need validation (local FFmpeg 6.1.1) |
| Q14 | Source/model/context/glossary signatures; atomic stage/cue checkpoints; per-stage logs/runtime/memory metadata; existing Copy/Export controls retained; actual quality progress events; multi-version cast isolation and resume tested. | Long-episode interruption/disk-pressure/parallel-window soak on Mac outstanding |
| Q15 | Explicit boolean consent, HTTPS Qwen review endpoint/key environment, Eleven v4 with style tags/existing credential controls, preview consent gate, cost/privacy explanation, credential redaction. Consent tests passed. | No provider credentials supplied; actual billing/retention/region/model availability and output quality not verified |
| Q16 | IndexTTS2.5 local adapter/config/consent/reference fields and preview connected; native factor 1.0 avoids unmeasured acceleration. Reference/config fingerprints invalidate caches. | Real model/API/MPS clone execution not tested; optimized native duration control not enabled before validation |
| Q17 | Five isolated requirement files, ARM64 installer with pip check/resolved lock export, settings exporter/setup checker, model manifests/checksums, docs; new ARM64 dependency-resolution and native API smoke CI workflow. | All five isolated environments installed and passed pip/API smoke checks on native ARM64 CI. Full-weight inference and M5 physical-memory gates remain unverified; Hy redistribution audit remains unresolved |
| Q18 | Full Python regression, real subprocess RPC, real synthetic media E2E/mux/resume, real R3 stretch; Swift tests/build and universal signed bundle tested in initial macOS CI. Evaluation harness supplied. | M5 Max 48 GB, licensed Chinese clips, bilingual/listening raters and full-episode soak unavailable. These gates are BLOCKED, not passed |

## Observed validation

- Linux x86_64, Python 3.12.14: 200 tests run successfully locally with 4 environment skips after installing small test dependencies; real Rubber Band CLI 4.0.0 R3 route exercised. Model outputs in E2E tests are fixtures, not generated speech quality evidence.
- Source preflight and `git diff --check` passed. Base environment `pip check` passed.
- Initial PR commit `35f5229`: GitHub macOS backend, Ubuntu backend, SwiftUI tests/build, universal bundle/signing/validation passed. Windows initially exposed UTF-8/default-codepage glossary loading; fixed and regression-tested. Updated CI must pass before this draft is eligible for review.
- Unavailable locally: SwiftUI SDK/MPS/MLX execution, 48 GB unified-memory measurements, actual cloud accounts, bilingual gold media and character/voice listening panel. No GPU/ASR/TTS/translation quality results fabricated.

## Release decision

Remain an experimental, opt-in profile and draft PR. Existing working providers/casts/data remain available. Do not merge/promote based only on mocked model contracts or a successful Swift build. The code is connected and has explicit repair/block behavior; the real-model, licensing, overlap/identity and domain-quality limitations above are substantive unfinished acceptance gates.

Native ARM64 resolution evidence: workflow 38490298067 passed all five isolated environments, including Python 3.12 for Kokoro/diarization. This is dependency resolution, not inference. Native installation/API smoke checks are included in the updated workflow.

Native installation/API evidence at commit 22fb0b7: translation, review, separator and diarization/Kokoro passed actual ARM64 installs, pip check and API smoke. Audio smoke exposed an incorrect class-level assertion on Qwen's delegating wrapper; the test now checks its actual implementation/delegation API. Final audio/native CI at `c3c06c2` passed all five runtime installs, dependency checks and API smoke probes.

The local execution environment disconnected during final publication. Remaining edits were recovered through checked GitHub API replacements; final CI, rather than an unavailable local rerun, is the acceptance evidence for that published revision.

The final published code suite contains 202 tests. At `c3c06c2`, backend CI passed on Ubuntu (3 skips), macOS (27 skips) and Windows (30 skips). Ubuntu exercised actual Rubber Band duration/pitch and real-media integration checks. SwiftUI build/tests and all five native ARM64 runtime install/API jobs passed. These are software/runtime checks, not model-quality benchmarks. Real model weights/inference and M5 Max 48 GB performance remain unverified.

Additional acceptance limit: automatic speech-versus-song/reaction classification is not implemented or calibrated; ambiguous non-dialogue/overlap material can block source analysis. A validated event-classification/separation policy and representative clips are required before claiming universal one-click coverage.

Final universal bundle evidence at `c3c06c2`: signed universal build, bundle validation, update verification/staging tests and artifact upload passed. SwiftUI suite: 15 tests, zero failures. Automatic public update publication was correctly skipped for the pull request. The following documentation-only commit records these results; it does not change the verified code.

Final code CI links: [backend](https://github.com/wassayhassan/DubCanvas/actions/runs/38495989395), [native runtimes](https://github.com/wassayhassan/DubCanvas/actions/runs/38495989347), [SwiftUI](https://github.com/wassayhassan/DubCanvas/actions/runs/38495989353), [universal bundle](https://github.com/wassayhassan/DubCanvas/actions/runs/38495989527).

## Continuation: remaining independent work

- Q02: inference deadlines now also cover blocked worker pipe delivery; regression deliberately fills an unread pipe and verifies timely failure, worker termination and writer-thread cleanup.
- Q07/Q17: completed static source/loader audit for pinned Hy adapter; audited hash is supplied by default, configured code-file selection is checked, tokenizer remote code disabled. [Audit record](hy-adapter-audit.md) identifies the exact upstream/conversion license discrepancy. No redistribution clearance or model parity is claimed.
- Q17: installer runs the native runtime/API probes after pip check, imposes per-step deadlines, saves readable setup logs and saves successful worker paths incrementally.
- Q18: new real-model probe command uses the production workers and saves each result/failure; corpus evaluation preserves partial artifacts and continues other clips after `run_sync` raises. Empty corpora cannot pass. Stage timings, sampled worker RSS/system memory/swap and MLX peak allocator metrics are recorded separately from human scores and physical footprint.
- Local regression at this continuation: 209 tests run successfully, 4 environment skips (including unavailable Rubber Band CLI). Source preflight, byte compilation, whitespace checks and isolated test-environment dependency checks pass. Model-probe tests use fixtures solely to verify reporting contracts. The real-media fixture/mux/resume test passed; no real ML weights or human-quality scores were generated here.

Unfinished independent implementation limits remain explicit: source-specific separation/ASR for simultaneous speakers, semantic cross-episode identity/age-sex casting, speech/song/reaction classification and optimized native-duration search lack evaluated model/data support. They are not marked complete. Expanded-video retiming is not enabled; saved no-fit failure is the implemented fallback. Physical M5 Max 48 GB testing, full-episode soak and bilingual/listening quality gates require that hardware and annotated clips. Cloud validation requires credentials. Every other previously recorded blocker remains in force.

Q05/Q11 follow-up: delivered English captions now end at measured fitted-speech duration, including permitted borrowed silence. Original Chinese timestamps and background replacement windows remain intact. The real-media regression asserts this timing contract. Probe logs use unique run IDs so repeated evaluation does not aggregate earlier runs.

Final continuation code revision: `0c34798a26deafd927d6a935820b6630c7b38eff`.

- [Backend CI](https://github.com/wassayhassan/DubCanvas/actions/runs/38500995969): 209 tests run on each platform, no failures; Ubuntu 3 skips, macOS 27, Windows 30. Ubuntu exercised the real Rubber Band R3 duration/pitch and media integration tests. Windows path-normalization assertion was corrected before this final run.
- [SwiftUI CI](https://github.com/wassayhassan/DubCanvas/actions/runs/38500995962): build and all 15 tests passed.
- [Native ARM64 runtimes](https://github.com/wassayhassan/DubCanvas/actions/runs/38500996034): all five installations, dependency checks and API/Metal probes passed. No weights were loaded.
- [Universal bundle](https://github.com/wassayhassan/DubCanvas/actions/runs/38500995984): package workflow passed; public update publication remains skipped for this draft PR.
- Local and published source trees were compared and matched exactly. Documentation-only evidence updates do not change the verified code.

These software checks are complete. Model inference/quality, physical M5 Max 48 GB measurements, annotated-media/listening/soak, redistribution and credentialed cloud gates remain BLOCKED. PR remains experimental/draft; public update is not published.

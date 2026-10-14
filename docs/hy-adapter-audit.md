# Pinned Hy adapter and licensing audit

Inspected 2026-10-14; static source inspection, without executing model weights.

- Conversion: `QwQbb/Hy-MT2-30B-A3B-MLX-8bit`, revision `b12a2cb66e79411849a0dfdd044e60cf3e5e4c80`.
- Reviewed file: all 239 lines of `hy_v3.py`.
- SHA-256: `0a193c9114f57edd1acc513edd20f86125ec23482a449df59d2987976a69187b`.
- Loading implementation inspected: published `mlx-lm==0.31.3` wheel, `mlx_lm/utils.py`.

The adapter imports dataclasses, typing, MLX and MLX-LM tensor/model helpers. It defines model arguments, attention, MoE routing, decoder layers, weight sanitization and quantization policy. No network calls, subprocesses, filesystem writes or dynamic source execution were found in this file. Its imports still depend on trusted installed packages. This inspection does not certify mathematical equivalence, checkpoint fidelity, long-context behavior or memory safety of native dependencies.

MLX-LM's loader executes the filename in `config.json`'s `model_file` field. Checking only the hash of a file named `hy_v3.py` would not constrain that path. DubCanvas now requires the configured filename to be exactly `hy_v3.py`, then verifies the adapter hash immediately before loading. Tokenizer remote-code execution is disabled. Changed adapters need their own explicit checksum and source audit; no downloaded Python adapter is silently trusted.

The pinned conversion's `conversion_info.json` identifies `tencent/Hy-MT2-30B-A3B` as its base and records the converter's MLX 0.31.2/MLX-LM 0.31.3 smoke test on an M5 Max with 128 GB. That is third-party evidence, not a DubCanvas benchmark or a 48 GB result.

Licensing provenance is now specific. The conversion revision carries `license: other`, a Tencent HY Community `LICENSE.txt` and corresponding `NOTICE`. Its license-file SHA-256 is `34fd2a05c1407b3b4ef82d292a1c045969b04c68ea2837ad73db3f50d6fdd6e2`. Upstream subsequently changed its license to Apache-2.0 at commit `6091895bbfa37c7846ff265a0829d409f771402a`. The upstream file fetched during this audit has SHA-256 `c739a55bdf480d9fc8a2fe3f60e84363cc9a8a8d7fbb7c7ac6aa715c127ead47`.

Do not label the pinned conversion as an Apache-2.0 artifact or bundle it on the strength of the upstream model-card badge. Resolve the older conversion terms and adapter permission with its publisher, or produce a separately audited conversion from an identified Apache-licensed upstream revision. DubCanvas ships neither this adapter nor the weights; local configuration remains available. Redistribution is still an acceptance blocker. This records source provenance, not a legal clearance.

Primary evidence:

- [Pinned adapter](https://huggingface.co/QwQbb/Hy-MT2-30B-A3B-MLX-8bit/blob/b12a2cb66e79411849a0dfdd044e60cf3e5e4c80/hy_v3.py)
- [Pinned conversion metadata](https://huggingface.co/QwQbb/Hy-MT2-30B-A3B-MLX-8bit/blob/b12a2cb66e79411849a0dfdd044e60cf3e5e4c80/conversion_info.json)
- [Pinned conversion license](https://huggingface.co/QwQbb/Hy-MT2-30B-A3B-MLX-8bit/blob/b12a2cb66e79411849a0dfdd044e60cf3e5e4c80/LICENSE.txt)
- [Upstream license update](https://huggingface.co/tencent/Hy-MT2-30B-A3B/commit/6091895bbfa37c7846ff265a0829d409f771402a)
- [MLX-LM 0.31.3 source](https://github.com/ml-explore/mlx-lm/blob/v0.31.3/mlx_lm/utils.py)

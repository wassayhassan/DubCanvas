"""Bounded overlap-add inference using the audited MSST BandIt-v2 forward API.

Only the parent's contextual window is read. GPU input is at most one 8-second
stereo window, regardless of episode length. Whole-track buffers never live on GPU.
"""
from pathlib import Path

def separate_window(model, config, device, input_directory, output_directory):
    import numpy as np
    import soundfile as sf
    import torch
    source = Path(input_directory) / "audio.wav"
    x, sr = sf.read(source, dtype="float32", always_2d=True)
    if sr != int(config.audio.sample_rate) or x.shape[1] != 2:
        raise ValueError("BandIt input must match audited configuration sample rate and stereo channels")
    size = int(config.audio.chunk_size)
    hop = size // 2
    stems = list(config.kwargs.stems)
    estimated = np.zeros((len(stems), 2, len(x)), dtype=np.float32)
    accumulated = np.zeros(len(x), dtype=np.float32)
    weights = np.maximum(np.hanning(size).astype(np.float32), 1e-3)
    for start in range(0, len(x), hop):
        stop = min(len(x), start + size)
        waveform = np.pad(x[start:stop].T, ((0, 0), (0, size - (stop - start))))
        tensor = torch.from_numpy(waveform).unsqueeze(0).to(device)
        try:
            with torch.inference_mode():
                prediction = model(tensor).detach().cpu().numpy()[0]
        except (RuntimeError, NotImplementedError) as exc:
            if device != "mps":
                raise
            print(f"Warning: BandIt MPS operation unavailable ({exc}); using CPU for this worker")
            model.to("cpu")
            device = "cpu"
            with torch.inference_mode():
                prediction = model(tensor.cpu()).detach().cpu().numpy()[0]
        length = stop - start
        if prediction.shape != (len(stems), 2, size):
            raise ValueError(f"BandIt output shape mismatch: {prediction.shape}")
        estimated[:, :, start:stop] += prediction[:, :, :length] * weights[:length]
        accumulated[start:stop] += weights[:length]
        del tensor, prediction
    estimated /= accumulated[None, None, :]
    if not np.isfinite(estimated).all():
        raise ValueError("BandIt returned non-finite audio samples")
    directory = Path(output_directory) / "audio"
    directory.mkdir(parents=True, exist_ok=True)
    for i, stem in enumerate(stems):
        sf.write(directory / f"{stem}.wav", estimated[i].T, sr, subtype="FLOAT")
    return {"output": str(directory)}

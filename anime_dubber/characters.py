from __future__ import annotations

import json
import math
import platform
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

ANALYSIS_SR = 16000


@dataclass
class AudioFeatures:
    rms_db: float = -90.0
    f0_median: float = 0.0
    voiced_ratio: float = 0.0
    zcr: float = 0.0
    flatness: float = 0.0
    centroid: float = 0.0
    embedding: List[float] = field(default_factory=list)


@dataclass
class CharacterProfile:
    id: str
    display_name: str
    role: str = "minor"
    voice_class: str = "neutral"  # male|female|neutral
    voice_confidence: float = 0.0
    age_group: str = "adult"  # child|adult|older
    age_confidence: float = 0.0
    line_count: int = 0
    speaking_seconds: float = 0.0
    speaking_share: float = 0.0
    f0_median: float = 0.0
    rms_db_median: float = -90.0
    roughness: float = 0.0
    tts_provider: str = "inherit"  # inherit|auto|chatterbox|kokoro|macos|piper|elevenlabs
    macos_voice: str = ""
    kokoro_voice: str = "auto"
    reference_audio: str = ""
    suggested_reference_audio: str = ""
    auto_reference_enabled: bool = True
    reference_quality: float = 0.0
    reference_timing: List[List[float]] = field(default_factory=list)
    expressiveness: float = 0.5
    elevenlabs_voice_id: str = ""
    tts_rate: int = 205
    pitch_semitones: float = 0.0
    voice_gain: float = 1.0
    style_counts: Dict[str, int] = field(default_factory=dict)
    embedding: List[float] = field(default_factory=list)
    embedding_backend: str = "acoustic"
    manual: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterProfile":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        payload = {k: v for k, v in d.items() if k in allowed}
        return cls(**payload)


KNOWN_MAC_VOICES = {
    # Common English voices. macOS availability varies by version and downloaded voices.
    "Alex": ("male", "adult"),
    "Daniel": ("male", "adult"),
    "Aaron": ("male", "adult"),
    "Evan": ("male", "adult"),
    "Nathan": ("male", "adult"),
    "Tom": ("male", "adult"),
    "Fred": ("male", "older"),
    "Ralph": ("male", "older"),
    "Albert": ("male", "older"),
    "Junior": ("male", "child"),
    "Samantha": ("female", "adult"),
    "Ava": ("female", "adult"),
    "Allison": ("female", "adult"),
    "Susan": ("female", "adult"),
    "Zoe": ("female", "adult"),
    "Victoria": ("female", "older"),
    "Karen": ("female", "adult"),
    "Moira": ("female", "adult"),
    "Tessa": ("female", "adult"),
    "Fiona": ("female", "adult"),
}

VOICE_PREFS = {
    ("male", "adult"): ["Alex", "Daniel", "Aaron", "Evan", "Nathan", "Tom", "Fred"],
    ("female", "adult"): ["Samantha", "Ava", "Allison", "Susan", "Zoe", "Karen", "Moira", "Tessa", "Fiona", "Victoria"],
    ("male", "older"): ["Daniel", "Alex", "Fred", "Ralph", "Albert"],
    ("female", "older"): ["Victoria", "Samantha", "Ava", "Susan"],
    ("male", "child"): ["Alex", "Evan", "Junior"],
    ("female", "child"): ["Samantha", "Ava", "Zoe"],
    ("neutral", "adult"): ["Alex", "Samantha", "Ava", "Daniel"],
    ("neutral", "older"): ["Fred", "Victoria", "Alex", "Samantha"],
    ("neutral", "child"): ["Junior", "Samantha", "Alex"],
}


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    av = np.asarray(a, dtype=np.float32)
    bv = np.asarray(b, dtype=np.float32)
    an = float(np.linalg.norm(av))
    bn = float(np.linalg.norm(bv))
    if an <= 1e-8 or bn <= 1e-8:
        return -1.0
    return float(np.dot(av, bv) / (an * bn))


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-8 else v


def ensure_analysis_wav(vocals: Path, work_dir: Path, runner, resume: bool = True, force: bool = False) -> Path:
    from .core import _atomic_media_run, _complete_wav, ffprobe_duration

    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "speaker_analysis_16k_mono.wav"
    minimum = ffprobe_duration(vocals, runner) * .98
    if resume and _complete_wav(out, minimum_seconds=minimum) and not force:
        return out
    _atomic_media_run(runner, [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(vocals), "-vn", "-ac", "1", "-ar", str(ANALYSIS_SR),
        "-c:a", "pcm_s16le", str(out),
    ], out)
    return out


def _load_segment(sf_handle, start: float, end: float) -> np.ndarray:
    sr = int(sf_handle.samplerate)
    start_frame = max(0, int(start * sr))
    # Cap analysis excerpts so very long narration lines do not dominate runtime/memory.
    frames = max(1, min(int(max(0.08, end - start) * sr), int(8.0 * sr)))
    sf_handle.seek(min(start_frame, max(0, len(sf_handle) - 1)))
    x = sf_handle.read(frames=frames, dtype="float32", always_2d=False)
    if isinstance(x, np.ndarray) and x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < int(0.08 * sr):
        x = np.pad(x, (0, int(0.08 * sr) - len(x)))
    return np.asarray(x, dtype=np.float32)


def _frame_signal(x: np.ndarray, frame: int = 640, hop: int = 320, max_frames: int = 48) -> List[np.ndarray]:
    if len(x) < frame:
        x = np.pad(x, (0, frame - len(x)))
    starts = list(range(0, max(1, len(x) - frame + 1), hop))
    if not starts:
        starts = [0]
    if len(starts) > max_frames:
        idx = np.linspace(0, len(starts) - 1, max_frames).astype(int)
        starts = [starts[i] for i in idx]
    win = np.hanning(frame).astype(np.float32)
    return [(x[s:s + frame] * win) for s in starts if len(x[s:s + frame]) == frame]


def _estimate_pitch(frames: Sequence[np.ndarray], sr: int) -> Tuple[float, float]:
    vals: List[float] = []
    min_lag = max(1, int(sr / 350.0))
    max_lag = max(min_lag + 1, int(sr / 65.0))
    for fr in frames:
        y = fr - float(np.mean(fr))
        energy = float(np.dot(y, y))
        if energy < 1e-6:
            continue
        # FFT autocorrelation is faster than np.correlate for long runs.
        n = 1
        while n < len(y) * 2:
            n <<= 1
        spec = np.fft.rfft(y, n=n)
        ac = np.fft.irfft(spec * np.conj(spec), n=n)[:len(y)]
        if ac[0] <= 1e-9:
            continue
        hi = min(max_lag, len(ac) - 1)
        if hi <= min_lag:
            continue
        region = ac[min_lag:hi + 1] / ac[0]
        lag = int(np.argmax(region)) + min_lag
        strength = float(ac[lag] / ac[0])
        if strength >= 0.32:
            vals.append(float(sr / lag))
    if not frames:
        return 0.0, 0.0
    return (float(np.median(vals)) if vals else 0.0, len(vals) / max(1, len(frames)))


def _acoustic_features(x: np.ndarray, sr: int = ANALYSIS_SR) -> AudioFeatures:
    if x.size == 0:
        return AudioFeatures()
    x = x.astype(np.float32)
    x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x)))
    if peak > 1.2:
        x = x / peak
    rms = float(np.sqrt(np.mean(x * x) + 1e-12))
    rms_db = 20.0 * math.log10(max(rms, 1e-8))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(x)).astype(np.float32)))) if len(x) > 2 else 0.0
    frames = _frame_signal(x)
    f0, voiced = _estimate_pitch(frames, sr)

    bands = []
    centroids = []
    flats = []
    for fr in frames:
        mag = np.abs(np.fft.rfft(fr, n=1024)).astype(np.float64) + 1e-10
        freqs = np.fft.rfftfreq(1024, 1.0 / sr)
        valid = (freqs >= 80) & (freqs <= min(7600, sr / 2 - 1))
        m = mag[valid]
        f = freqs[valid]
        if not len(m):
            continue
        centroids.append(float(np.sum(f * m) / max(np.sum(m), 1e-10)))
        flats.append(float(np.exp(np.mean(np.log(m))) / max(np.mean(m), 1e-10)))
        # Content-robust coarse log spectral envelope.
        edges = np.geomspace(80, min(7600, sr / 2 - 1), 25)
        row = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            mask = (f >= lo) & (f < hi)
            row.append(float(np.log1p(np.mean(m[mask]) if np.any(mask) else 0.0)))
        bands.append(row)

    if bands:
        b = np.asarray(bands, dtype=np.float32)
        # Per-frame level normalization reduces loudness/content dependence.
        b = b - b.mean(axis=1, keepdims=True)
        emb = np.concatenate([b.mean(axis=0), b.std(axis=0)], axis=0)
    else:
        emb = np.zeros(48, dtype=np.float32)
    # Include only a small amount of pitch information in identity embedding.
    extra = np.asarray([
        min(f0, 400.0) / 400.0,
        voiced,
        min(zcr, 0.5) * 2.0,
        min(float(np.median(centroids)) if centroids else 0.0, 8000.0) / 8000.0,
    ], dtype=np.float32)
    emb = _normalize(np.concatenate([emb, extra]))
    return AudioFeatures(
        rms_db=rms_db,
        f0_median=f0,
        voiced_ratio=float(voiced),
        zcr=zcr,
        flatness=float(np.median(flats)) if flats else 0.0,
        centroid=float(np.median(centroids)) if centroids else 0.0,
        embedding=emb.astype(float).tolist(),
    )


class SpeakerEmbedder:
    def __init__(self, work_dir: Path, progress, mode: str = "auto"):
        self.backend = "acoustic"
        self.model = None
        self.torch = None
        if mode == "acoustic":
            progress("Using built-in acoustic speaker clustering.")
            return
        try:
            from speechbrain.inference.speaker import EncoderClassifier
            import torch
            progress("Loading ECAPA speaker model for multi-character diarization…")
            self.model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(work_dir / "speechbrain_ecapa"),
                run_opts={"device": "cpu"},
            )
            self.torch = torch
            self.backend = "speechbrain-ecapa"
        except Exception as e:
            progress(f"Speaker model unavailable ({e}); using built-in acoustic clustering fallback.")

    def embed_many(self, waves: Sequence[np.ndarray], acoustics: Sequence[AudioFeatures]) -> List[List[float]]:
        if self.model is None or self.torch is None:
            return [a.embedding for a in acoustics]
        try:
            min_len = int(0.35 * ANALYSIS_SR)
            max_len = max(min_len, max(len(x) for x in waves))
            batch = np.zeros((len(waves), max_len), dtype=np.float32)
            lens = []
            for i, x in enumerate(waves):
                n = min(len(x), max_len); batch[i, :n] = x[:n]; lens.append(max(n, min_len) / max_len)
            wav = self.torch.tensor(batch, dtype=self.torch.float32)
            wav_lens = self.torch.tensor(lens, dtype=self.torch.float32)
            with self.torch.no_grad():
                e = self.model.encode_batch(wav, wav_lens=wav_lens).detach().cpu().numpy().astype(np.float32)
            e = e.reshape(len(waves), -1)
            return [_normalize(row).astype(float).tolist() for row in e]
        except Exception:
            return [a.embedding for a in acoustics]

    def embed(self, x: np.ndarray, acoustic: AudioFeatures) -> List[float]:
        return self.embed_many([x], [acoustic])[0]


def _confident_voice_class(feature: AudioFeatures) -> str:
    """Use pitch only when voiced evidence clearly separates low and high turns."""
    if feature.voiced_ratio < 0.35:
        return ""
    if 65 <= feature.f0_median <= 155:
        return "male"
    if feature.f0_median >= 215:
        return "female"
    return ""


def _greedy_cluster(embeddings: Sequence[Sequence[float]], max_speakers: int, threshold: float,
                    features: Optional[Sequence[AudioFeatures]] = None) -> List[int]:
    centroids: List[np.ndarray] = []
    counts: List[int] = []
    labels: List[int] = []
    classes: List[str] = []
    for index, e in enumerate(embeddings):
        v = _normalize(np.asarray(e, dtype=np.float32))
        voice_class = (_confident_voice_class(features[index])
                       if features is not None and index < len(features) else "")
        if not centroids:
            centroids.append(v); counts.append(1); labels.append(0); classes.append(voice_class); continue
        sims = [float(np.dot(v, c)) for c in centroids]
        compatible = [i for i, existing in enumerate(classes)
                      if not voice_class or not existing or voice_class == existing]
        best = max(compatible, key=lambda i: sims[i]) if compatible else None
        if best is not None and (sims[best] >= threshold or len(centroids) >= max_speakers):
            labels.append(best)
            counts[best] += 1
            centroids[best] = _normalize(centroids[best] * (counts[best] - 1) + v)
            if not classes[best]:
                classes[best] = voice_class
        elif len(centroids) < max_speakers:
            labels.append(len(centroids)); centroids.append(v); counts.append(1); classes.append(voice_class)
        else:
            # At the configured speaker cap, keep the nearest identity rather
            # than silently creating more characters than the user requested.
            best = int(np.argmax(sims))
            labels.append(best); counts[best] += 1
            centroids[best] = _normalize(centroids[best] * (counts[best] - 1) + v)
    # Merge obvious duplicate clusters.
    changed = True
    while changed and len(centroids) > 1:
        changed = False
        best_pair = None; best_sim = threshold + 0.06
        for i in range(len(centroids)):
            for j in range(i + 1, len(centroids)):
                if classes[i] and classes[j] and classes[i] != classes[j]:
                    continue
                sim = float(np.dot(centroids[i], centroids[j]))
                if sim > best_sim:
                    best_sim = sim; best_pair = (i, j)
        if best_pair:
            a, b = best_pair
            for k, lab in enumerate(labels):
                if lab == b:
                    labels[k] = a
                elif lab > b:
                    labels[k] = lab - 1
            na, nb = counts[a], counts[b]
            centroids[a] = _normalize(centroids[a] * na + centroids[b] * nb)
            counts[a] += counts[b]
            classes[a] = classes[a] or classes[b]
            del centroids[b]; del counts[b]; del classes[b]
            changed = True
    return labels


def _centroid(vectors: Sequence[Sequence[float]]) -> List[float]:
    if not vectors:
        return []
    arr = np.asarray(vectors, dtype=np.float32)
    return _normalize(arr.mean(axis=0)).astype(float).tolist()


def _infer_voice_class(f0: float) -> Tuple[str, float]:
    # This is acoustic voice presentation, not a claim about a person's identity.
    if f0 <= 0:
        return "neutral", 0.2
    if f0 < 170:
        return "male", min(0.95, 0.60 + (170 - f0) / 180)
    if f0 > 190:
        return "female", min(0.95, 0.60 + (f0 - 190) / 220)
    return "neutral", 0.4


def _infer_age_group(f0: float, flatness: float, global_flatness: float, voice_class: str) -> Tuple[str, float]:
    if f0 <= 0:
        return "adult", 0.25
    # Conservative: only call child/older when the acoustic evidence is fairly strong.
    if f0 >= 300:
        return "child", min(0.88, 0.58 + (f0 - 300) / 280)
    rough_ratio = flatness / max(global_flatness, 0.005)
    old_cut = 145 if voice_class == "male" else 175
    if f0 < old_cut and rough_ratio > 1.35:
        return "older", min(0.82, 0.55 + (rough_ratio - 1.35) / 2.5)
    return "adult", 0.62


def _role_for(rank: int, share: float, n: int) -> str:
    if rank == 0:
        return "lead"
    if share >= 0.16 or (rank == 1 and n <= 5):
        return "major"
    if share >= 0.055:
        return "supporting"
    return "minor"


def _pick_voice(profile: CharacterProfile, available: Sequence[str], used: set) -> Tuple[str, int, float, float]:
    prefs = VOICE_PREFS.get((profile.voice_class, profile.age_group), VOICE_PREFS[("neutral", "adult")])
    voice = ""
    def matches(pref: str):
        return [a for a in available if a == pref or a.startswith(pref + " ") or a.startswith(pref + "(")]
    for name in prefs:
        for candidate in matches(name):
            if candidate not in used:
                voice = candidate; break
        if voice:
            break
    if not voice:
        for name in prefs:
            found = matches(name)
            if found:
                voice = found[0]; break
    if not voice and available:
        voice = available[0]
    if voice:
        used.add(voice)

    rate = 205
    pitch = 0.0
    gain = 1.0
    if profile.age_group == "child":
        rate = 225; pitch += 3.2; gain = 0.95
    elif profile.age_group == "older":
        rate = 185; pitch -= 1.5; gain = 1.02
    if profile.voice_class == "female" and voice and KNOWN_MAC_VOICES.get(voice, ("neutral", "adult"))[0] != "female":
        pitch += 2.0
    elif profile.voice_class == "male" and voice and KNOWN_MAC_VOICES.get(voice, ("neutral", "adult"))[0] != "male":
        pitch -= 2.0
    # Lead voices should be a little more measured and prominent; minor voices can be slightly quicker.
    if profile.role == "lead":
        rate -= 5; gain *= 1.05
    elif profile.role == "minor":
        rate += 8; gain *= 0.95
    return voice, max(120, min(320, rate)), pitch, gain


def _style_for_segment(feat: AudioFeatures, baseline: dict, text: str) -> Tuple[str, float]:
    rms_delta = feat.rms_db - baseline["rms"]
    f0_ratio = feat.f0_median / baseline["f0"] if feat.f0_median > 0 and baseline["f0"] > 0 else 1.0
    flat_ratio = feat.flatness / max(baseline["flat"], 0.005)
    shout = max(0.0, (rms_delta - 4.5) / 7.0) + max(0.0, (f0_ratio - 1.10) / 0.30)
    if "!" in text or "！" in text:
        shout += 0.35
    whisper = max(0.0, (-rms_delta - 5.5) / 8.0) + max(0.0, (0.55 - feat.voiced_ratio) / 0.35)
    if flat_ratio > 1.3:
        whisper += min(0.6, (flat_ratio - 1.3) / 1.5)
    if shout >= 0.75 and shout > whisper:
        return "shouting", min(0.97, 0.58 + shout * 0.22)
    if whisper >= 0.85 and whisper > shout:
        return "whispering", min(0.95, 0.55 + whisper * 0.20)
    return "normal", max(0.55, 0.82 - max(shout, whisper) * 0.12)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _apply_manual_overrides(profiles: List[CharacterProfile], override_path: Optional[Path]) -> None:
    if not override_path or not override_path.exists():
        return
    data = _load_json(override_path, {})
    items = data.get("characters", []) if isinstance(data, dict) else []
    by_id = {str(x.get("id")): x for x in items if isinstance(x, dict)}
    editable = {"display_name", "role", "voice_class", "age_group", "tts_provider", "macos_voice",
                "kokoro_voice", "reference_audio", "auto_reference_enabled", "expressiveness",
                "elevenlabs_voice_id", "tts_rate", "pitch_semitones", "voice_gain", "notes"}
    for p in profiles:
        old = by_id.get(p.id)
        if not old or not bool(old.get("manual")):
            continue
        for k in editable:
            if k in old:
                setattr(p, k, old[k])
        p.manual = True


def _series_db_path(output_dir: Path, series_id: str) -> Optional[Path]:
    if not series_id.strip():
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", series_id.strip()).strip("._-") or "series"
    p = output_dir / ".anime_dubber_series" / f"{safe}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _match_series_profiles(profiles: List[CharacterProfile], db_path: Optional[Path], backend: str, progress) -> None:
    if not db_path:
        return
    data = _load_json(db_path, {"characters": []})
    old = [CharacterProfile.from_dict(x) for x in data.get("characters", []) if isinstance(x, dict)]
    next_num = 1
    for o in old:
        m = re.search(r"(\d+)$", o.id)
        if m:
            next_num = max(next_num, int(m.group(1)) + 1)
    used_old = set()
    for p in profiles:
        best = None; best_sim = 0.0
        for o in old:
            if o.id in used_old or o.embedding_backend != backend:
                continue
            if (p.voice_confidence >= .7 and o.voice_confidence >= .7
                    and p.voice_class in {"male", "female"} and o.voice_class in {"male", "female"}
                    and p.voice_class != o.voice_class):
                continue
            sim = cosine(p.embedding, o.embedding)
            if sim > best_sim:
                best_sim = sim; best = o
        threshold = 0.73 if backend == "speechbrain-ecapa" else 0.94
        if best is not None and best_sim >= threshold:
            used_old.add(best.id)
            p.id = best.id
            p.display_name = best.display_name
            # Persistent voice identity wins; current-run acoustics/role remain descriptive.
            p.macos_voice = best.macos_voice
            p.tts_rate = best.tts_rate
            p.pitch_semitones = best.pitch_semitones
            p.voice_gain = best.voice_gain
            if best.manual:
                p.voice_class = best.voice_class
                p.age_group = best.age_group
                p.role = best.role
                for key in ("tts_provider", "kokoro_voice", "reference_audio", "auto_reference_enabled",
                            "expressiveness", "elevenlabs_voice_id", "notes"):
                    setattr(p, key, getattr(best, key))
                p.manual = True
            progress(f"Matched {p.id} to an existing series voice ({best_sim:.2f} similarity).")
        else:
            p.id = f"CHAR_{next_num:03d}"; p.display_name = f"Character {next_num}"; next_num += 1


def save_series_profiles(profiles: Sequence[CharacterProfile], db_path: Optional[Path]) -> None:
    if not db_path:
        return
    existing = _load_json(db_path, {"characters": []})
    by_id = {str(x.get("id")): x for x in existing.get("characters", []) if isinstance(x, dict)}
    for p in profiles:
        old = by_id.get(p.id, {})
        d = p.to_dict()
        d["cumulative_lines"] = int(old.get("cumulative_lines", 0)) + p.line_count
        d["cumulative_seconds"] = float(old.get("cumulative_seconds", 0.0)) + p.speaking_seconds
        by_id[p.id] = d
    payload = {"version": 1, "characters": list(by_id.values())}
    _write_json(db_path, payload)


def _choose_voice_references(
    analysis_wav: Path,
    segments: Sequence,
    profiles: Sequence[CharacterProfile],
    work_dir: Path,
    runner,
    progress,
    *,
    features: Optional[Sequence[AudioFeatures]] = None,
    embeddings: Optional[Sequence[Sequence[float]]] = None,
) -> None:
    """Make short speaker-specific Chatterbox prompts from isolated dialogue.

    A questionable speaker or a video without clean speech stays on the default
    voice. No clip is accepted solely because a speaker label exists.
    """
    import soundfile as sf
    from .providers.tts import MIN_CHATTERBOX_REFERENCE_SECONDS

    folder = work_dir / "voice_references"
    folder.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(analysis_wav), "r") as source:
        rate = int(source.samplerate)
        for profile in profiles:
            runner.check_cancel()
            candidates = []
            for index, seg in enumerate(segments):
                if seg.speaker_id != profile.id or seg.style not in {"normal", ""}:
                    continue
                start, end = float(seg.start), float(seg.end)
                duration = end - start
                if duration < 1.2 or duration > 12.0 or start < 0 or end > len(source) / rate + .02:
                    continue
                # Do not clone the voice from the edge of a neighboring turn.
                # The interior duration is checked again before using a reference.
                start += 0.15
                end -= 0.15
                # Adjacent or overlapping lines can contain a second speaker.
                if any(other.speaker_id != profile.id and
                       min(float(other.end) + .08, end) - max(float(other.start) - .08, start) > .05
                       for other in segments[max(0, index - 2):index] + segments[index + 1:index + 3]):
                    continue
                feat = features[index] if features is not None else _acoustic_features(_load_segment(source, start, min(end, start + 8)))
                if feat.voiced_ratio < .38 or not (-43 < feat.rms_db < -10):
                    continue
                pitch_match = (not profile.f0_median or not feat.f0_median or
                               .68 <= feat.f0_median / profile.f0_median <= 1.45)
                if not pitch_match:
                    continue
                similarity = (cosine(embeddings[index], profile.embedding)
                              if embeddings is not None and profile.embedding else 1.0)
                if embeddings is not None and similarity < (.55 if profile.embedding_backend == "speechbrain-ecapa" else .80):
                    continue
                score = (min(duration, 5.0) / 5.0 * .3 + min(feat.voiced_ratio, 1.0) * .4
                         + min(max((feat.rms_db + 43) / 33, 0), 1) * .1
                         + min(max(similarity, 0), 1) * .2)
                candidates.append((score, index, start, end))

            candidates.sort(reverse=True)
            chosen = []
            total = 0.0
            for score, _, start, end in candidates:
                length = min(end - start, 6.0, 10.0 - total)
                if length < 1.0:
                    break
                chosen.append((score, start, start + length))
                total += length
                if total >= 8.0 or len(chosen) >= 6:
                    break

            profile.suggested_reference_audio = ""
            profile.reference_quality = 0.0
            profile.reference_timing = []
            if total < MIN_CHATTERBOX_REFERENCE_SECONDS or not chosen:
                progress(f"Less than {MIN_CHATTERBOX_REFERENCE_SECONDS:.2f}s of clean speech for "
                         f"{profile.display_name}; using its selected voice")
                continue

            parts = []
            for _, start, end in sorted(chosen, key=lambda c: c[1]):
                runner.check_cancel()
                signal = _load_segment(source, start, end)[:int((end - start) * rate)]
                edge = min(len(signal) // 2, int(rate * .012))
                if edge:
                    signal[:edge] *= np.linspace(0, 1, edge)
                    signal[-edge:] *= np.linspace(1, 0, edge)
                parts.extend([signal, np.zeros(int(rate * .08), dtype=np.float32)])
            audio = np.concatenate(parts)
            peak = max(float(np.max(np.abs(audio))), .001)
            audio = np.clip(audio * min(.85 / peak, 2.0), -.98, .98)
            path = folder / f"{profile.id}.wav"
            temp = path.with_name(path.stem + ".partial.wav")
            try:
                sf.write(str(temp), audio, rate, subtype="PCM_16")
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
            profile.suggested_reference_audio = str(path.resolve())
            profile.reference_quality = round(float(np.mean([item[0] for item in chosen])), 2)
            profile.reference_timing = [[round(a, 2), round(b, 2)] for _, a, b in chosen]
            progress(f"Selected {total:.1f}s of source speech for {profile.display_name}; review its reference in Characters")


def analyze_characters(
    vocals: Path,
    segments: Sequence,
    work_dir: Path,
    output_dir: Path,
    runner,
    progress,
    *,
    resume: bool = True,
    force: bool = False,
    max_speakers: int = 12,
    speaker_threshold: float = 0.0,
    series_id: str = "",
    available_voices: Sequence[str] = (),
    override_path: Optional[Path] = None,
    speaker_backend: str = "auto",
    make_voice_references: bool = True,
) -> Tuple[List[CharacterProfile], dict]:
    """Assign speaker, acoustic profile, and speaking style to each transcript segment.

    The age/gender-style labels are conservative acoustic categories used only for TTS voice selection.
    They are not assertions about a real person's identity.
    """
    getattr(runner, "set_stage", lambda *_: None)("analyzing_characters", "Analyzing speakers")
    cache = work_dir / "character_analysis.json"
    signature = {
        "analysis_version": 5,
        "segments": [[round(float(s.start), 3), round(float(s.end), 3), str(s.text)] for s in segments],
        "max_speakers": int(max_speakers),
        "threshold": float(speaker_threshold),
        "series_id": series_id,
        "speaker_backend": speaker_backend,
    }
    if resume and cache.exists() and not force:
        old = _load_json(cache, {})
        if old.get("signature") == signature:
            profiles = [CharacterProfile.from_dict(x) for x in old.get("characters", [])]
            labels = old.get("segments", [])
            if len(labels) == len(segments):
                for s, lab in zip(segments, labels):
                    s.speaker_id = lab.get("speaker_id", "")
                    s.style = lab.get("style", "normal")
                    s.style_confidence = float(lab.get("style_confidence", 0.0))
                _apply_manual_overrides(profiles, override_path)
                if make_voice_references and (old.get("reference_version") != 2 or any(
                    p.suggested_reference_audio and not Path(p.suggested_reference_audio).is_file()
                    for p in profiles
                )):
                    analysis_wav = ensure_analysis_wav(vocals, work_dir, runner, resume=resume, force=force)
                    _choose_voice_references(analysis_wav, segments, profiles, work_dir, runner, progress)
                    old["reference_version"] = 2
                    old["characters"] = [p.to_dict() for p in profiles]
                    _write_json(cache, old)
                else:
                    old["characters"] = [p.to_dict() for p in profiles]
                return profiles, old

    try:
        import soundfile as sf
    except Exception as e:
        raise RuntimeError("soundfile is required for character analysis. Re-run setup.sh") from e

    analysis_wav = ensure_analysis_wav(vocals, work_dir, runner, resume=resume, force=force)
    embedder = SpeakerEmbedder(work_dir, progress, mode=speaker_backend)
    feats: List[AudioFeatures] = []
    embeds: List[List[float]] = []
    progress("Analyzing character voices, pitch, age cues, and speaking style…")
    batch_size = 16 if embedder.backend == "speechbrain-ecapa" else 1
    with sf.SoundFile(str(analysis_wav), "r") as f:
        for off in range(0, len(segments), batch_size):
            runner.check_cancel()
            batch_segments = segments[off:off + batch_size]
            waves = []
            batch_feats = []
            for seg in batch_segments:
                # Whisper boundaries often include a fragment of the previous
                # or next speaker. Embed the interior of sufficiently long cues.
                start, end = float(seg.start), float(seg.end)
                edge = min(0.15, max(0.0, (end - start - 0.35) / 2))
                x = _load_segment(f, start + edge, end - edge)
                waves.append(x)
                batch_feats.append(_acoustic_features(x, int(f.samplerate)))
            feats.extend(batch_feats)
            embeds.extend(embedder.embed_many(waves, batch_feats))
            done = off + len(batch_segments)
            if done % 25 < batch_size or done == len(segments):
                progress(f"Voice analysis: {done}/{len(segments)} lines")

    threshold = float(speaker_threshold)
    if threshold <= 0:
        threshold = 0.60 if embedder.backend == "speechbrain-ecapa" else 0.91
    labels = _greedy_cluster(embeds, max(2, int(max_speakers)), threshold, feats)
    cluster_ids = sorted(set(labels))
    total_speech = sum(max(0.01, float(s.end) - float(s.start)) for s in segments)
    global_flat = float(np.median([x.flatness for x in feats])) if feats else 0.01

    profiles: List[CharacterProfile] = []
    cluster_to_profile: Dict[int, CharacterProfile] = {}
    raw_stats = []
    for c in cluster_ids:
        idx = [i for i, lab in enumerate(labels) if lab == c]
        dur = sum(max(0.01, float(segments[i].end) - float(segments[i].start)) for i in idx)
        f0s = [feats[i].f0_median for i in idx if feats[i].f0_median > 0]
        f0 = float(np.median(f0s)) if f0s else 0.0
        rms = float(np.median([feats[i].rms_db for i in idx])) if idx else -90.0
        flat = float(np.median([feats[i].flatness for i in idx])) if idx else 0.0
        emb = _centroid([embeds[i] for i in idx])
        raw_stats.append((c, idx, dur, f0, rms, flat, emb))
    raw_stats.sort(key=lambda x: x[2], reverse=True)

    used_voices = set()
    for rank, (c, idx, dur, f0, rms, flat, emb) in enumerate(raw_stats):
        vc, vc_conf = _infer_voice_class(f0)
        age, age_conf = _infer_age_group(f0, flat, global_flat, vc)
        share = dur / max(total_speech, 0.01)
        p = CharacterProfile(
            id=f"TMP_{c:03d}", display_name=f"Character {c + 1}",
            role=_role_for(rank, share, len(raw_stats)),
            voice_class=vc, voice_confidence=vc_conf,
            age_group=age, age_confidence=age_conf,
            line_count=len(idx), speaking_seconds=dur, speaking_share=share,
            f0_median=f0, rms_db_median=rms, roughness=flat,
            embedding=emb, embedding_backend=embedder.backend,
        )
        profiles.append(p); cluster_to_profile[c] = p

    db_path = _series_db_path(output_dir, series_id)
    _match_series_profiles(profiles, db_path, embedder.backend, progress)

    # Assign voices to new characters. Existing series matches keep their previous choice.
    available_set = set(available_voices)
    for p in profiles:
        if p.macos_voice and p.macos_voice not in available_set:
            progress(f"Saved voice '{p.macos_voice}' is not installed; choosing a replacement for {p.id}.")
            p.macos_voice = ""
        if p.macos_voice:
            used_voices.add(p.macos_voice)
    for p in profiles:
        if not p.macos_voice:
            p.macos_voice, p.tts_rate, p.pitch_semitones, p.voice_gain = _pick_voice(p, available_voices, used_voices)

    # cluster_to_profile references still point to the same objects whose ids may have changed.
    baselines = {}
    for c in cluster_ids:
        idx = [i for i, lab in enumerate(labels) if lab == c]
        f0s = [feats[i].f0_median for i in idx if feats[i].f0_median > 0]
        baselines[c] = {
            "rms": float(np.median([feats[i].rms_db for i in idx])) if idx else -30.0,
            "f0": float(np.median(f0s)) if f0s else 0.0,
            "flat": float(np.median([feats[i].flatness for i in idx])) if idx else 0.01,
        }

    segment_rows = []
    for i, (seg, c) in enumerate(zip(segments, labels)):
        style, conf = _style_for_segment(feats[i], baselines[c], str(seg.text))
        prof = cluster_to_profile[c]
        seg.speaker_id = prof.id
        seg.style = style
        seg.style_confidence = conf
        prof.style_counts[style] = prof.style_counts.get(style, 0) + 1
        segment_rows.append({
            "index": i,
            "speaker_id": prof.id,
            "style": style,
            "style_confidence": conf,
            "rms_db": feats[i].rms_db,
            "f0_median": feats[i].f0_median,
            "voiced_ratio": feats[i].voiced_ratio,
        })

    _apply_manual_overrides(profiles, override_path)
    payload = {
        "version": 4,
        "reference_version": 0,
        "signature": signature,
        "speaker_backend": embedder.backend,
        "speaker_threshold": threshold,
        "series_id": series_id,
        "characters": [p.to_dict() for p in profiles],
        "segments": segment_rows,
        "notes": {
            "voice_class": "Acoustic voice-presentation estimate for TTS selection; not identity inference.",
            "age_group": "Conservative acoustic estimate. Child/older labels are only assigned when evidence is stronger; manual review is available.",
            "role": "Lead/major/supporting/minor is inferred from speaking share, not plot knowledge.",
        },
    }
    # Speaker clustering is a durable checkpoint; extracting reference clips
    # can be repeated later without recomputing embeddings if paused here.
    _write_json(cache, payload)
    if make_voice_references:
        _choose_voice_references(analysis_wav, segments, profiles, work_dir, runner, progress,
                                 features=feats, embeddings=embeds)
        payload["reference_version"] = 2
        payload["characters"] = [p.to_dict() for p in profiles]
        _write_json(cache, payload)
    save_series_profiles(profiles, db_path)
    return profiles, payload


def write_character_map(payload: dict, path: Path) -> None:
    _write_json(path, payload)


def load_character_map(path: Path) -> dict:
    return _load_json(path, {})


EDITABLE_CHARACTER_FIELDS = {
    "display_name",
    "role",
    "voice_class",
    "age_group",
    "tts_provider",
    "macos_voice",
    "kokoro_voice",
    "reference_audio",
    "auto_reference_enabled",
    "expressiveness",
    "elevenlabs_voice_id",
    "tts_rate",
    "pitch_semitones",
    "voice_gain",
    "notes",
}


def list_character_maps(output_dir: Path) -> List[dict]:
    output = Path(output_dir).expanduser().resolve()
    rows: List[dict] = []
    for path in sorted(output.glob("*_characters.json")):
        data = load_character_map(path)
        if not isinstance(data, dict):
            continue
        chars = [x for x in data.get("characters", []) if isinstance(x, dict)]
        rows.append({
            "path": str(path),
            "source_key": path.name.removesuffix("_characters.json"),
            "series_id": str(data.get("series_id", "")),
            "speaker_backend": str(data.get("speaker_backend", "")),
            "character_count": len(chars),
            "modified_at": path.stat().st_mtime,
        })
    rows.sort(key=lambda x: float(x.get("modified_at", 0.0)), reverse=True)
    return rows


def character_map_for_ui(path: Path) -> dict:
    p = Path(path).expanduser().resolve()
    data = load_character_map(p)
    if not isinstance(data, dict) or not data:
        raise FileNotFoundError(f"Character map not found or invalid: {p}")
    return {
        "path": str(p),
        "version": data.get("version"),
        "series_id": str(data.get("series_id", "")),
        "speaker_backend": str(data.get("speaker_backend", "")),
        "speaker_threshold": data.get("speaker_threshold"),
        "characters": [x for x in data.get("characters", []) if isinstance(x, dict)],
        "notes": dict(data.get("notes") or {}),
    }


def update_character_override(path: Path, character_id: str, updates: dict) -> dict:
    """Persist one manually edited character and sync it to the series database."""
    p = Path(path).expanduser().resolve()
    data = load_character_map(p)
    if not isinstance(data, dict) or not data:
        raise FileNotFoundError(f"Character map not found or invalid: {p}")

    items = [x for x in data.get("characters", []) if isinstance(x, dict)]
    target = next((x for x in items if str(x.get("id")) == str(character_id)), None)
    if target is None:
        raise KeyError(f"Character not found: {character_id}")

    for key, value in dict(updates or {}).items():
        if key not in EDITABLE_CHARACTER_FIELDS:
            continue
        if key == "tts_rate":
            value = max(80, min(450, int(value)))
        elif key in {"pitch_semitones", "voice_gain"}:
            value = float(value)
        elif key == "expressiveness":
            value = max(0.0, min(1.5, float(value)))
        elif key == "auto_reference_enabled":
            if not isinstance(value, bool):
                raise ValueError("Automatic voice reference must be true or false")
        else:
            value = str(value)
        target[key] = value
    target["manual"] = True
    data["characters"] = items
    write_character_map(data, p)

    series_id = str(data.get("series_id", "")).strip()
    db_path = _series_db_path(p.parent, series_id)
    if db_path:
        existing = _load_json(db_path, {"version": 1, "characters": []})
        rows = [x for x in existing.get("characters", []) if isinstance(x, dict)]
        by_id = {str(x.get("id")): x for x in rows}
        saved = by_id.get(str(character_id))
        if saved is None:
            saved = dict(target)
            by_id[str(character_id)] = saved
        for key in EDITABLE_CHARACTER_FIELDS | {"manual"}:
            if key in target:
                saved[key] = target[key]
        existing["version"] = int(existing.get("version", 1) or 1)
        existing["characters"] = list(by_id.values())
        _write_json(db_path, existing)

    return dict(target)

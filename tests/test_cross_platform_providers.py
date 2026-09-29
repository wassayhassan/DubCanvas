from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from anime_dubber.application.service import config_from_dict
from anime_dubber.cli import build_parser
from anime_dubber.core import CommandRunner, Config, transcribe_audio, transcribe_source_audio
from anime_dubber.providers.asr import resolve_asr_provider
from anime_dubber.providers.translation import ollama_generate, translate_with_ollama
from anime_dubber.providers.tts import (
    _prepare_chatterbox_watermarker,
    automatic_kokoro_voice,
    synthesize_chatterbox,
    synthesize_multilingual_chatterbox,
    synthesize_piper,
)


class CrossPlatformProviderTests(unittest.TestCase):
    def test_empty_dialogue_stem_retries_original_without_reusing_its_language_guess(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Config(source="video.mp4", output_dir=root, source_language="auto",
                         asr_provider="faster-whisper")
            calls = []
            def recognize(audio, **kwargs):
                calls.append(audio)
                self.assertIsNone(kwargs["language"])
                if audio.name == "vocals.wav":
                    return []
                kwargs["language_sink"]("zh")
                return [{"start": 0, "end": 1, "text": "你好"}]
            with patch("anime_dubber.providers.asr.faster_whisper_segments", side_effect=recognize):
                segments, selected = transcribe_source_audio(root / "vocals.wav", root / "original.wav",
                    cfg, root, CommandRunner(), lambda _: None)
            self.assertEqual([p.name for p in calls], ["vocals.wav", "original.wav"])
            self.assertEqual((segments[0].text, cfg.source_language, selected.name), ("你好", "zh", "original.wav"))
            self.assertTrue((root / "original_soundtrack_asr" / "detected_source_language.json").exists())

    def test_empty_original_track_stays_empty_without_inventing_subtitles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Config(source="video.mp4", output_dir=root, source_language="auto",
                         asr_provider="faster-whisper")
            with patch("anime_dubber.providers.asr.faster_whisper_segments", return_value=[]) as recognize:
                segments, selected = transcribe_source_audio(root / "vocals.wav", root / "original.wav",
                    cfg, root, CommandRunner(), lambda _: None)
            self.assertEqual(segments, [])
            self.assertEqual(recognize.call_count, 2)
            self.assertEqual(selected.name, "original.wav")

    def test_auto_source_detection_uses_provider_language(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = config_from_dict({"source": "input.mp4", "output_dir": td,
                                    "asr": {"provider": "faster-whisper"}})
            self.assertEqual(cfg.source_language, "auto")

            def recognize(_audio, **kwargs):
                self.assertIsNone(kwargs["language"])
                kwargs["language_sink"]("es")
                return [{"start": 0, "end": 1, "text": "Hola"}]

            with patch("anime_dubber.providers.asr.faster_whisper_segments", side_effect=recognize):
                segments = transcribe_audio(Path(td) / "voice.wav", cfg, Path(td), CommandRunner(), lambda _: None)
            self.assertEqual(cfg.source_language, "es")
            self.assertEqual(segments[0].text, "Hola")
            self.assertEqual(json.loads((Path(td) / "detected_source_language.json").read_text())["language"], "es")
            resumed = config_from_dict({"source": "input.mp4", "output_dir": td,
                                        "asr": {"provider": "faster-whisper"}})
            with patch("anime_dubber.providers.asr.faster_whisper_segments", side_effect=AssertionError("recached")):
                cached = transcribe_audio(Path(td) / "voice.wav", resumed, Path(td), CommandRunner(), lambda _: None)
            self.assertEqual((resumed.source_language, cached[0].text), ("es", "Hola"))

    def test_auto_language_does_not_assume_chinese_from_legacy_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "transcript_zh_faster_whisper_v5_precise.json").write_text(
                json.dumps([{"start": 0, "end": 1, "text": "你好"}]), encoding="utf-8")
            cfg = Config(source="video.mp4", output_dir=root, source_language="auto",
                         asr_provider="faster_whisper")
            def recognize(_audio, **kwargs):
                self.assertIsNone(kwargs["language"])
                kwargs["language_sink"]("es")
                return [{"start": 0, "end": 1, "text": "Hola"}]
            with patch("anime_dubber.providers.asr.faster_whisper_segments", side_effect=recognize) as asr:
                rows = transcribe_audio(root / "speech.wav", cfg, root, CommandRunner(), lambda _: None)
            asr.assert_called_once()
            self.assertEqual((cfg.source_language, rows[0].text), ("es", "Hola"))

    def test_partial_ollama_batches_keep_valid_lines_across_retries(self):
        with patch("anime_dubber.providers.translation.ollama_generate",
                   side_effect=['[{"id": 0, "text": "Hello"}]',
                                '[{"id": 1, "text": "World"}]']) as generate:
            translated = translate_with_ollama(
                batches=[([0, 1], "translate")], base_url="http://localhost:11434", model="test",
                parse_batch=lambda raw, ids: {int(row["id"]): row["text"] for row in json.loads(raw)},
                single_prompt=lambda idx: f"single {idx}", cancel_check=lambda: None,
                progress=lambda _: None)
        self.assertEqual(translated, {0: "Hello", 1: "World"})
        self.assertEqual(generate.call_count, 2)

    def test_multilingual_voice_sends_target_language_and_reference(self):
        class FakeAudio:
            def detach(self): return self
            def cpu(self): return self

        with tempfile.TemporaryDirectory() as td:
            ref = Path(td) / "ref.wav"; ref.write_bytes(b"reference")
            output = Path(td) / "dub.wav"
            model = types.SimpleNamespace(sr=24000, generate=Mock(return_value=FakeAudio()))
            fake_audio = types.SimpleNamespace(save=lambda path, _audio, _rate: Path(path).write_bytes(b"w" * 60))
            fake_model = types.SimpleNamespace(ChatterboxMultilingualTTS=types.SimpleNamespace(
                from_pretrained=Mock(return_value=model)))
            with patch.dict("sys.modules", {"torchaudio": fake_audio, "chatterbox.mtl_tts": fake_model}), \
                 patch("anime_dubber.providers.tts.multilingual_chatterbox_available", return_value=True), \
                 patch("anime_dubber.providers.tts.reference_audio_duration", return_value=7.0), \
                 patch("anime_dubber.providers.tts._best_torch_device", return_value="cpu"), \
                 patch("anime_dubber.providers.tts._prepare_chatterbox_watermarker"), \
                 patch.dict("anime_dubber.providers.tts._MULTILINGUAL_MODELS", {}, clear=True):
                synthesize_multilingual_chatterbox("Hola", output, language="es", reference_audio=str(ref))
            self.assertEqual(model.generate.call_args.kwargs["language_id"], "es")
            self.assertEqual(model.generate.call_args.kwargs["cfg_weight"], 0.0)
            self.assertEqual(fake_model.ChatterboxMultilingualTTS.from_pretrained.call_args.kwargs["t3_model"], "v3")
            self.assertTrue(output.is_file())

    def test_chinese_reference_prefers_standard_model_for_american_english(self):
        class FakeAudio:
            def detach(self): return self
            def cpu(self): return self

        with tempfile.TemporaryDirectory() as td:
            reference = Path(td) / "reference.wav"
            reference.write_bytes(b"reference")
            output = Path(td) / "dub.wav"
            model = types.SimpleNamespace(sr=24000, generate=Mock(return_value=FakeAudio()))
            fake_audio = types.SimpleNamespace(save=lambda path, _audio, _rate: Path(path).write_bytes(b"w" * 60))
            with patch.dict("sys.modules", {"torchaudio": fake_audio}), \
                 patch("anime_dubber.providers.tts.chatterbox_available", return_value=True), \
                 patch("anime_dubber.providers.tts.reference_audio_duration", return_value=7.0), \
                 patch("anime_dubber.providers.tts._best_torch_device", return_value="cpu"), \
                 patch("anime_dubber.providers.tts._load_chatterbox", return_value=model) as load:
                synthesize_chatterbox("Hello", output, reference_audio=str(reference),
                                      turbo=True, american_english=True)
                load.assert_called_once_with("cpu", False)
                self.assertEqual(model.generate.call_args.kwargs["cfg_weight"], 0.0)
                self.assertEqual(model.generate.call_args.kwargs["audio_prompt_path"], str(reference.resolve()))
                self.assertTrue(output.is_file())

                load.reset_mock()
                model.generate.reset_mock()
                synthesize_chatterbox("Hello", output, reference_audio=str(reference),
                                      turbo=True, american_english=False)
                load.assert_called_once_with("cpu", True)
                self.assertNotIn("cfg_weight", model.generate.call_args.kwargs)

    def test_mlx_model_selections_are_regular_job_settings(self):
        cfg = config_from_dict({
            "source": "input.mp4", "output_dir": "./out",
            "asr": {"provider": "mlx_whisper", "mlx_model": "mlx-community/whisper-large-v3-mlx"},
            "translation": {"provider": "llm", "llm_model": "mlx-community/Qwen3-8B-4bit"},
            "review_model": "mlx-community/Qwen3-14B-4bit",
        })
        self.assertEqual(cfg.mlx_whisper_model, "mlx-community/whisper-large-v3-mlx")
        self.assertEqual(cfg.llm_model, "mlx-community/Qwen3-8B-4bit")
        self.assertEqual(cfg.review_model, "mlx-community/Qwen3-14B-4bit")

    def test_nested_provider_config(self):
        cfg = config_from_dict({
            "source": "input.mp4",
            "output_dir": "./out",
            "asr": {
                "provider": "faster-whisper",
                "model": "large-v3",
                "device": "cuda",
                "compute_type": "float16",
            },
            "translation": {
                "provider": "ollama",
                "model": "qwen3:8b",
                "ollama_url": "http://localhost:11434",
            },
            "tts": {
                "provider": "piper",
                "piper_model": "./voice.onnx",
                "piper_speaker": 2,
                "rate": 215,
            },
        })
        self.assertEqual(cfg.asr_provider, "faster-whisper")
        self.assertEqual(cfg.faster_whisper_device, "cuda")
        self.assertEqual(cfg.translation, "ollama")
        self.assertEqual(cfg.ollama_model, "qwen3:8b")
        self.assertEqual(cfg.tts_engine, "piper")
        self.assertEqual(cfg.piper_speaker, 2)

    def test_premium_voice_config(self):
        cfg = config_from_dict({
            "source": "input.mp4",
            "output_dir": "./out",
            "tts": {
                "provider": "chatterbox",
                "chatterbox_reference_audio": "./ref.wav",
                "chatterbox_expressiveness": 0.8,
                "chatterbox_device": "mps",
                "chatterbox_turbo": True,
                "kokoro_voice": "af_bella",
            },
        })
        self.assertEqual(cfg.tts_engine, "chatterbox")
        self.assertEqual(cfg.chatterbox_reference_audio, "./ref.wav")
        self.assertAlmostEqual(cfg.chatterbox_expressiveness, 0.8)
        self.assertEqual(cfg.chatterbox_device, "mps")
        self.assertTrue(cfg.chatterbox_turbo)
        self.assertTrue(cfg.prefer_american_accent)
        self.assertEqual(cfg.kokoro_voice, "af_bella")

        original = config_from_dict({
            "source": "input.mp4", "output_dir": "./out",
            "tts": {"prefer_american_accent": False},
        })
        self.assertFalse(original.prefer_american_accent)

    def test_chatterbox_perth_none_falls_back_to_dummy_watermarker(self):
        class DummyWatermarker:
            pass

        fake_perth = types.SimpleNamespace(
            PerthImplicitWatermarker=None,
            DummyWatermarker=DummyWatermarker,
        )
        with patch.dict("sys.modules", {"perth": fake_perth}):
            mode = _prepare_chatterbox_watermarker()

        self.assertEqual(mode, "dummy")
        self.assertIs(fake_perth.PerthImplicitWatermarker, DummyWatermarker)

    def test_chatterbox_keeps_real_perth_watermarker_when_available(self):
        class RealWatermarker:
            pass

        class DummyWatermarker:
            pass

        fake_perth = types.SimpleNamespace(
            PerthImplicitWatermarker=RealWatermarker,
            DummyWatermarker=DummyWatermarker,
        )
        with patch.dict("sys.modules", {"perth": fake_perth}):
            mode = _prepare_chatterbox_watermarker()

        self.assertEqual(mode, "implicit")
        self.assertIs(fake_perth.PerthImplicitWatermarker, RealWatermarker)

    def test_automatic_kokoro_character_voice(self):
        self.assertEqual(
            automatic_kokoro_voice({"voice_class": "female", "age_group": "adult"}),
            "af_bella",
        )
        self.assertEqual(
            automatic_kokoro_voice({"voice_class": "male", "age_group": "older"}),
            "am_michael",
        )

    def test_cli_cross_platform_flags(self):
        args = build_parser().parse_args([
            "run", "input.mp4",
            "--asr", "faster-whisper",
            "--faster-whisper-device", "cuda",
            "--translation", "ollama",
            "--ollama-model", "qwen3:8b",
            "--tts", "piper",
            "--piper-model", "voice.onnx",
        ])
        self.assertEqual(args.asr, "faster-whisper")
        self.assertEqual(args.translation, "ollama")
        self.assertEqual(args.tts, "piper")
        self.assertEqual(args.piper_model, "voice.onnx")

        premium = build_parser().parse_args([
            "run", "input.mp4",
            "--tts", "chatterbox",
            "--chatterbox-reference", "hero.wav",
            "--chatterbox-expressiveness", "0.7",
            "--chatterbox-device", "mps",
        ])
        self.assertEqual(premium.tts, "chatterbox")
        self.assertEqual(premium.chatterbox_reference, "hero.wav")
        self.assertAlmostEqual(premium.chatterbox_expressiveness, 0.7)
        self.assertEqual(premium.chatterbox_device, "mps")
        self.assertFalse(premium.preserve_source_accent)
        preserve = build_parser().parse_args(["run", "input.mp4", "--preserve-source-accent"])
        self.assertTrue(preserve.preserve_source_accent)

        kokoro = build_parser().parse_args([
            "run", "input.mp4",
            "--tts", "kokoro",
            "--kokoro-voice", "am_adam",
        ])
        self.assertEqual(kokoro.tts, "kokoro")
        self.assertEqual(kokoro.kokoro_voice, "am_adam")

    def test_explicit_asr_aliases(self):
        self.assertEqual(resolve_asr_provider("faster-whisper"), "faster_whisper")
        self.assertEqual(resolve_asr_provider("mlx-whisper"), "mlx_whisper")

    def test_transcribe_routes_to_faster_whisper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "a.wav"
            audio.write_bytes(b"fake")
            cfg = Config(
                source="input.mp4",
                output_dir=root,
                asr_provider="faster-whisper",
                multi_character=True,
            )
            rows = [
                {"start": 0.0, "end": 1.0, "text": "你好"},
                {"start": 1.0, "end": 2.0, "text": "世界"},
            ]
            with patch("anime_dubber.providers.asr.faster_whisper_segments", return_value=rows) as mocked:
                out = transcribe_audio(audio, cfg, root, CommandRunner(), lambda _m: None)
            self.assertEqual([x.text for x in out], ["你好", "世界"])
            mocked.assert_called_once()

    def test_mlx_legacy_cache_is_upgraded_to_precise_word_timing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            audio = root / "a.wav"
            audio.write_bytes(b"fake")
            legacy = root / "transcript_zh_v3.json"
            legacy.write_text(json.dumps([
                {"start": 0.0, "end": 1.0, "text": "你好"},
            ]), encoding="utf-8")
            cfg = Config(
                source="input.mp4",
                output_dir=root,
                asr_provider="mlx-whisper",
                resume=True,
            )
            fake_mlx = types.SimpleNamespace(
                transcribe=lambda *_args, **_kwargs: {
                    "segments": [{
                        "start": 0.0,
                        "end": 1.0,
                        "text": "你好",
                        "words": [
                            {"start": 0.18, "end": 0.42, "word": "你"},
                            {"start": 0.46, "end": 0.82, "word": "好"},
                        ],
                    }]
                }
            )
            with patch("anime_dubber.providers.asr.resolve_asr_provider", return_value="mlx_whisper"), patch.dict(
                "sys.modules", {"mlx_whisper": fake_mlx}
            ):
                out = transcribe_audio(audio, cfg, root, CommandRunner(), lambda _m: None)
            self.assertEqual(out[0].text, "你好")
            self.assertAlmostEqual(out[0].start, 0.18, places=3)
            self.assertAlmostEqual(out[0].end, 0.82, places=3)
            self.assertTrue((root / "transcript_zh_mlx_whisper_v5_precise.json").exists())

    def test_ollama_generate_parses_response(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"response": "Hello"}).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=Response()):
            text = ollama_generate(
                base_url="http://127.0.0.1:11434",
                model="qwen3:4b",
                prompt="translate",
            )
        self.assertEqual(text, "Hello")

    def test_piper_cli_writes_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = root / "voice.onnx"
            model.write_bytes(b"model")
            output = root / "out.wav"

            class FakeProcess:
                returncode = 0
                def __init__(self, cmd, **_kwargs):
                    self.cmd = cmd
                def communicate(self, _text):
                    path = Path(self.cmd[self.cmd.index("--output_file") + 1])
                    path.write_bytes(b"RIFF" + b"x" * 100)
                    return "", ""

            with patch("anime_dubber.providers.tts.piper_executable", return_value="piper"), patch(
                "anime_dubber.providers.tts.subprocess.Popen", FakeProcess
            ):
                synthesize_piper("hello", output, model_path=str(model), rate=205)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 44)


if __name__ == "__main__":
    unittest.main()

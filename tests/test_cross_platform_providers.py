from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from anime_dubber.providers.translation import translate_with_ollama
from anime_dubber.providers.tts import (
    _prepare_chatterbox_watermarker,
    synthesize_multilingual_chatterbox,
    synthesize_piper,
)


class CrossPlatformProviderTests(unittest.TestCase):


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

"""Voice provider dispatch; rendering and timing belong to the caller."""

from dataclasses import replace


def chatterbox(text, output, config, runner, voice):
    from .tts import synthesize_chatterbox

    synthesize_chatterbox(
        text,
        output,
        reference_audio=voice["reference"],
        expressiveness=voice["expressiveness"],
        device=config.chatterbox_device,
        turbo=config.chatterbox_turbo,
        american_english=voice["american_english"],
        cancel_check=runner.check_cancel,
    )


def multilingual(text, output, config, runner, voice):
    from .tts import synthesize_multilingual_chatterbox

    synthesize_multilingual_chatterbox(
        text,
        output,
        language=config.target_language,
        reference_audio=voice["reference"],
        expressiveness=voice["expressiveness"],
        device=config.chatterbox_device,
        cancel_check=runner.check_cancel,
    )


def kokoro(text, output, config, runner, voice):
    from .tts import synthesize_kokoro

    synthesize_kokoro(
        text,
        output,
        voice=voice["kokoro"],
        rate=voice["rate"],
        lang_code=config.kokoro_language,
        cancel_check=runner.check_cancel,
    )


def macos(text, output, config, runner, voice):
    from ..core import synthesize_macos

    synthesize_macos(
        text, output, config, runner, voice=voice["macos"], rate=voice["rate"]
    )


def piper(text, output, config, runner, voice):
    from .tts import synthesize_piper

    synthesize_piper(
        text,
        output,
        model_path=config.piper_model,
        rate=voice["rate"],
        speaker=config.piper_speaker if config.piper_speaker >= 0 else None,
        cancel_check=runner.check_cancel,
    )


def elevenlabs(text, output, config, runner, voice):
    from ..core import synthesize_elevenlabs

    synthesize_elevenlabs(
        text, output, replace(config, elevenlabs_voice_id=voice["elevenlabs"]), runner
    )


PROVIDERS = {
    "chatterbox": chatterbox,
    "chatterbox_multilingual": multilingual,
    "kokoro": kokoro,
    "macos": macos,
    "piper": piper,
    "elevenlabs": elevenlabs,
}


def synthesize(engine, text, output, config, runner, voice):
    from ..core import PipelineError, CancelledError

    if engine not in PROVIDERS:
        raise PipelineError(f"Unsupported TTS engine: {engine}")
    try:
        PROVIDERS[engine](text, output, config, runner, voice)
    except CancelledError:
        raise
    except Exception as exc:
        raise PipelineError(str(exc)) from exc

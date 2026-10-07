"""Short PCM WAV uploads are transcribed in memory, with no batch jobs/S3."""

import asyncio
import io
import wave

from upraise_ai.core.errors import IngestionError
from upraise_ai.services.aws import aws_session

LANGUAGES = {"en": "en-US", "it": "it-IT", "fr": "fr-FR", "de": "de-DE", "es": "es-ES",
             "pt": "pt-PT", "nl": "nl-NL", "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-CN"}


def wav_pcm(data, settings):
    if not data or len(data) > settings.interrupt_max_audio_bytes:
        raise IngestionError("INTERRUPT_AUDIO_TOO_LARGE", status=413)
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE"
                    or not 8000 <= rate <= 48000 or frames <= 0):
                raise ValueError("PCM mono 16-bit WAV required")
            if frames / rate > settings.interrupt_max_audio_seconds:
                raise IngestionError("INTERRUPT_AUDIO_TOO_LONG", status=413)
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("Truncated WAV")
            return pcm, rate
    except (wave.Error, EOFError, ValueError, OSError):
        raise IngestionError("INTERRUPT_AUDIO_FORMAT_UNSUPPORTED", status=422) from None


class InterruptSTT:
    def __init__(self, settings):
        self.settings = settings

    def transcribe(self, pcm, rate, language):
        try:
            return asyncio.run(asyncio.wait_for(self._transcribe(pcm, rate, language),
                timeout=self.settings.interrupt_max_audio_seconds + self.settings.interrupt_provider_timeout_seconds))
        except IngestionError:
            raise
        except Exception:
            raise IngestionError("INTERRUPT_STT_UNAVAILABLE", status=503, retryable=True) from None

    async def _transcribe(self, pcm, rate, language):
        from amazon_transcribe.auth import StaticCredentialResolver
        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.handlers import TranscriptResultStreamHandler

        code = LANGUAGES.get(language.split("-")[0].lower())
        if code is None:
            raise IngestionError("INTERRUPT_STT_LANGUAGE_UNSUPPORTED", status=422)
        credentials = aws_session(self.settings).get_credentials().get_frozen_credentials()
        client = TranscribeStreamingClient(region=self.settings.aws_region, credential_resolver=StaticCredentialResolver(
            credentials.access_key, credentials.secret_key, credentials.token))
        stream = await client.start_stream_transcription(language_code=code, media_sample_rate_hz=rate,
                                                         media_encoding="pcm")
        final = {}

        class Handler(TranscriptResultStreamHandler):
            async def handle_transcript_event(self, event):
                for result in event.transcript.results:
                    if not result.is_partial and result.alternatives:
                        final[result.result_id] = result.alternatives[0].transcript

        async def send():
            size = rate // 10 * 2  # 100 ms, two bytes per sample.
            started = asyncio.get_running_loop().time()
            try:
                for offset in range(0, len(pcm), size):
                    await stream.input_stream.send_audio_event(audio_chunk=pcm[offset:offset+size])
                    delay = started + min(offset+size, len(pcm))/(rate*2) - asyncio.get_running_loop().time()
                    if delay > 0:
                        await asyncio.sleep(delay)
            finally:
                await stream.input_stream.end_stream()

        # Cancel the companion task on failure, so no stream outlives this turn.
        tasks = [asyncio.create_task(send()), asyncio.create_task(Handler(stream.output_stream).handle_events())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        transcript = " ".join(final.values()).strip()
        if not transcript or len(transcript) > 6000:
            raise IngestionError("INTERRUPT_TRANSCRIPT_INVALID", status=422)
        return transcript

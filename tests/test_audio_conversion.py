"""
Unit tests for audio conversion utilities.

These tests verify the critical audio conversion path between Twilio (μ-law 8kHz)
and Gemini (PCM16 16kHz/24kHz). Correct conversion is essential for:
1. Gemini VAD to detect speech
2. Audio quality on the phone call
"""

import base64

import numpy as np
import pytest

from src.voice_ai_system.utils.audio import (
    twilio_to_gemini,
    gemini_to_twilio,
    calculate_audio_duration,
    chunk_audio,
    _ulaw_compress,
    _ulaw_decompress,
)


class TestMulawConversion:
    """Test μ-law compression and decompression."""

    def test_ulaw_roundtrip_preserves_audio(self):
        """Test that μ-law compress -> decompress preserves audio within tolerance."""
        # Generate test PCM samples (sine wave at 440Hz)
        sample_rate = 8000
        duration = 0.1  # 100ms
        t = np.linspace(0, duration, int(sample_rate * duration))
        # Use moderate amplitude (not too quiet, not clipping)
        pcm_original = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)

        # Compress and decompress
        mulaw = _ulaw_compress(pcm_original)
        pcm_recovered = _ulaw_decompress(mulaw)

        # μ-law is lossy, but should preserve signal within reasonable tolerance
        # The correlation should be very high (> 0.99)
        correlation = np.corrcoef(
            pcm_original.astype(np.float32),
            pcm_recovered.astype(np.float32)
        )[0, 1]
        assert correlation > 0.99, f"Correlation too low: {correlation}"

    def test_ulaw_handles_silence(self):
        """Test that μ-law handles silence (zeros) correctly."""
        silence = np.zeros(100, dtype=np.int16)
        mulaw = _ulaw_compress(silence)
        recovered = _ulaw_decompress(mulaw)

        # Recovered silence should be near zero
        assert np.abs(recovered).max() < 100, "Silence not preserved"

    def test_ulaw_handles_full_range(self):
        """Test that μ-law handles full dynamic range without clipping."""
        # Test extreme values
        extremes = np.array([-32768, -16384, 0, 16383, 32767], dtype=np.int16)
        mulaw = _ulaw_compress(extremes)

        # μ-law output should be uint8
        assert mulaw.dtype == np.uint8
        assert mulaw.min() >= 0
        assert mulaw.max() <= 255


class TestTwilioToGemini:
    """Test Twilio -> Gemini audio conversion."""

    def test_converts_mulaw_to_pcm16(self):
        """Test that μ-law base64 is converted to PCM16 bytes."""
        # Create μ-law silence (127-128 range is silence in μ-law)
        mulaw_data = bytes([127, 128, 127, 128] * 20)  # 80 samples
        mulaw_base64 = base64.b64encode(mulaw_data).decode()

        pcm_output = twilio_to_gemini(mulaw_base64)

        # Output should be PCM16 bytes
        assert isinstance(pcm_output, bytes)
        # After 8kHz -> 16kHz upsampling, we should have ~2x samples
        # Each sample is 2 bytes (16-bit)
        assert len(pcm_output) > len(mulaw_data)  # Upsampled

    def test_resamples_8khz_to_16khz(self):
        """Test that audio is correctly upsampled from 8kHz to 16kHz."""
        # Generate 100ms of μ-law audio at 8kHz = 800 samples
        sample_count_8k = 800
        # Use silence-ish μ-law values
        mulaw_data = bytes([127] * sample_count_8k)
        mulaw_base64 = base64.b64encode(mulaw_data).decode()

        pcm_output = twilio_to_gemini(mulaw_base64)

        # At 16kHz, 100ms = 1600 samples = 3200 bytes (16-bit)
        expected_samples_16k = sample_count_8k * 2
        actual_samples = len(pcm_output) // 2  # 2 bytes per sample

        # Allow some tolerance due to resampling edge effects
        assert abs(actual_samples - expected_samples_16k) < 10

    def test_output_has_reasonable_audio_levels(self):
        """Test that converted audio has reasonable amplitude for VAD detection."""
        # Create a simple tone in μ-law
        # First create PCM, compress to μ-law, then convert back
        sample_rate = 8000
        duration = 0.05  # 50ms
        t = np.linspace(0, duration, int(sample_rate * duration))
        pcm = (np.sin(2 * np.pi * 1000 * t) * 10000).astype(np.int16)  # 1kHz tone
        mulaw = _ulaw_compress(pcm)
        mulaw_base64 = base64.b64encode(mulaw.tobytes()).decode()

        pcm_output = twilio_to_gemini(mulaw_base64)

        # Check output audio levels
        pcm_array = np.frombuffer(pcm_output, dtype=np.int16)
        rms = np.sqrt(np.mean(pcm_array.astype(np.float32) ** 2))

        # RMS should be significant (not silence)
        # 10000 amplitude -> RMS ~7000 for sine wave
        assert rms > 1000, f"Audio level too low: RMS={rms}"


class TestGeminiToTwilio:
    """Test Gemini -> Twilio audio conversion."""

    def test_converts_pcm16_to_mulaw_base64(self):
        """Test that PCM16 is converted to μ-law base64."""
        # Create PCM16 silence at 24kHz
        pcm_data = np.zeros(2400, dtype=np.int16).tobytes()  # 100ms at 24kHz

        mulaw_base64 = gemini_to_twilio(pcm_data)

        # Output should be base64 string
        assert isinstance(mulaw_base64, str)
        # Should be valid base64
        decoded = base64.b64decode(mulaw_base64)
        assert len(decoded) > 0

    def test_downsamples_24khz_to_8khz(self):
        """Test that audio is correctly downsampled from 24kHz to 8kHz."""
        # 100ms at 24kHz = 2400 samples
        sample_count_24k = 2400
        pcm_data = np.zeros(sample_count_24k, dtype=np.int16).tobytes()

        mulaw_base64 = gemini_to_twilio(pcm_data)
        mulaw_bytes = base64.b64decode(mulaw_base64)

        # At 8kHz, 100ms = 800 samples (1 byte each for μ-law)
        expected_samples_8k = sample_count_24k // 3
        actual_samples = len(mulaw_bytes)

        # Allow some tolerance due to resampling
        assert abs(actual_samples - expected_samples_8k) < 10

    def test_roundtrip_preserves_audio_quality(self):
        """Test that Twilio -> Gemini -> Twilio preserves audio."""
        # Start with μ-law audio (typical phone call format)
        sample_rate = 8000
        duration = 0.1
        t = np.linspace(0, duration, int(sample_rate * duration))
        pcm_original = (np.sin(2 * np.pi * 500 * t) * 8000).astype(np.int16)
        mulaw_original = _ulaw_compress(pcm_original)
        mulaw_base64_original = base64.b64encode(mulaw_original.tobytes()).decode()

        # Convert: Twilio -> Gemini -> Twilio
        pcm_gemini = twilio_to_gemini(mulaw_base64_original)

        # Simulate Gemini processing: resample 16kHz -> 24kHz
        # (In reality, Gemini outputs at 24kHz)
        pcm_16k = np.frombuffer(pcm_gemini, dtype=np.int16)
        # Simple upsampling by repeating samples (rough approximation)
        pcm_24k = np.repeat(pcm_16k, 3)[::2]  # 1.5x
        pcm_24k_bytes = pcm_24k.astype(np.int16).tobytes()

        mulaw_base64_final = gemini_to_twilio(pcm_24k_bytes)
        mulaw_final = np.frombuffer(base64.b64decode(mulaw_base64_final), dtype=np.uint8)
        pcm_final = _ulaw_decompress(mulaw_final)

        # Check correlation between original and roundtrip
        # Use shorter length due to resampling differences
        min_len = min(len(pcm_original), len(pcm_final))
        correlation = np.corrcoef(
            pcm_original[:min_len].astype(np.float32),
            pcm_final[:min_len].astype(np.float32)
        )[0, 1]

        # Roundtrip through multiple conversions will have some loss
        assert correlation > 0.8, f"Roundtrip correlation too low: {correlation}"


class TestAudioUtilities:
    """Test audio utility functions."""

    def test_calculate_audio_duration(self):
        """Test audio duration calculation."""
        # 16000 samples at 16kHz = 1 second
        audio_data = np.zeros(16000, dtype=np.int16).tobytes()
        duration = calculate_audio_duration(audio_data, sample_rate=16000)
        assert duration == 1.0

        # 8000 samples at 8kHz = 1 second
        audio_data = np.zeros(8000, dtype=np.int16).tobytes()
        duration = calculate_audio_duration(audio_data, sample_rate=8000)
        assert duration == 1.0

    def test_chunk_audio(self):
        """Test audio chunking."""
        # 1 second of audio at 16kHz
        audio_data = np.zeros(16000, dtype=np.int16).tobytes()

        # Chunk into 20ms pieces (typical for real-time streaming)
        chunks = chunk_audio(audio_data, chunk_duration_ms=20, sample_rate=16000)

        # 1000ms / 20ms = 50 chunks
        assert len(chunks) == 50

        # Each chunk should be 320 samples * 2 bytes = 640 bytes
        assert all(len(c) == 640 for c in chunks)

    def test_chunk_audio_handles_partial_chunks(self):
        """Test that chunking handles audio that doesn't divide evenly."""
        # 1000 samples = 62.5ms at 16kHz
        audio_data = np.zeros(1000, dtype=np.int16).tobytes()

        chunks = chunk_audio(audio_data, chunk_duration_ms=20, sample_rate=16000)

        # Should have 3 full chunks + 1 partial
        assert len(chunks) == 4
        # Last chunk should be smaller
        assert len(chunks[-1]) < len(chunks[0])


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_audio_input(self):
        """Test handling of empty audio input."""
        empty_base64 = base64.b64encode(b"").decode()

        # Should handle empty input without crashing
        result = twilio_to_gemini(empty_base64)
        assert len(result) == 0

    def test_very_short_audio(self):
        """Test handling of very short audio (< 1 sample after resampling)."""
        # Just a few samples
        mulaw_data = bytes([127, 128, 127])
        mulaw_base64 = base64.b64encode(mulaw_data).decode()

        result = twilio_to_gemini(mulaw_base64)
        # Should produce some output
        assert isinstance(result, bytes)

    def test_maximum_amplitude_audio(self):
        """Test that maximum amplitude audio doesn't cause overflow."""
        # Maximum amplitude sine wave
        pcm = np.array([32767, -32768] * 400, dtype=np.int16)
        mulaw = _ulaw_compress(pcm)
        mulaw_base64 = base64.b64encode(mulaw.tobytes()).decode()

        # Should not raise any overflow errors
        result = twilio_to_gemini(mulaw_base64)
        pcm_result = np.frombuffer(result, dtype=np.int16)

        # Should still be valid int16 range
        assert pcm_result.min() >= -32768
        assert pcm_result.max() <= 32767

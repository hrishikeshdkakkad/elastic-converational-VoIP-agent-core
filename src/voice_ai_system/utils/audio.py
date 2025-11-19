"""Audio conversion utilities for handling different formats and sample rates."""

import base64
from typing import Literal

import numpy as np
import soxr


AudioFormat = Literal["mulaw", "pcm8", "pcm16", "pcm24", "pcm32"]


# μ-law compression constants
MULAW_MAX = 0x1FFF
MULAW_BIAS = 0x84


def _ulaw_compress(pcm: np.ndarray) -> np.ndarray:
    """
    Compress PCM samples to μ-law.

    Args:
        pcm: PCM samples as int16 numpy array

    Returns:
        μ-law encoded samples as uint8 numpy array
    """
    # Normalize to [-1, 1]
    pcm_float = pcm.astype(np.float32) / 32768.0

    # Get sign
    sign = np.sign(pcm_float)
    pcm_float = np.abs(pcm_float)

    # Apply μ-law compression
    compressed = np.log(1 + MULAW_MAX * pcm_float) / np.log(1 + MULAW_MAX)

    # Scale to uint8 range
    mulaw = (compressed * 127).astype(np.uint8)

    # Apply sign
    mulaw = np.where(sign < 0, 127 - mulaw, 255 - mulaw)

    return mulaw


def _ulaw_decompress(mulaw: np.ndarray) -> np.ndarray:
    """
    Decompress μ-law samples to PCM.

    Args:
        mulaw: μ-law encoded samples as uint8 numpy array

    Returns:
        PCM samples as int16 numpy array
    """
    # Convert to float and extract sign
    mulaw_float = mulaw.astype(np.float32)
    sign = np.where(mulaw_float >= 128, 1, -1)

    # Normalize to [0, 1]
    mulaw_float = np.where(mulaw_float >= 128, 255 - mulaw_float, 127 - mulaw_float)
    mulaw_float = mulaw_float / 127.0

    # Apply μ-law decompression
    pcm_float = sign * (np.exp(mulaw_float * np.log(1 + MULAW_MAX)) - 1) / MULAW_MAX

    # Convert to int16
    pcm = (pcm_float * 32768.0).astype(np.int16)

    return pcm


async def convert_audio(
    audio_data: bytes | str,
    from_format: AudioFormat,
    to_format: AudioFormat,
    from_rate: int,
    to_rate: int,
) -> bytes:
    """
    Convert audio between different formats and sample rates.

    Args:
        audio_data: Audio data (bytes or base64 string)
        from_format: Source audio format
        to_format: Target audio format
        from_rate: Source sample rate in Hz
        to_rate: Target sample rate in Hz

    Returns:
        Converted audio data as bytes
    """
    # Decode base64 if needed
    if isinstance(audio_data, str):
        audio_data = base64.b64decode(audio_data)

    # Step 1: Convert format to PCM16 if needed
    if from_format == "mulaw":
        # μ-law to linear PCM (16-bit)
        mulaw_array = np.frombuffer(audio_data, dtype=np.uint8)
        pcm_array = _ulaw_decompress(mulaw_array)
        pcm_data = pcm_array.tobytes()
    elif from_format == "pcm8":
        # 8-bit PCM to 16-bit PCM
        pcm8_array = np.frombuffer(audio_data, dtype=np.uint8)
        pcm16_array = ((pcm8_array.astype(np.int16) - 128) * 256).astype(np.int16)
        pcm_data = pcm16_array.tobytes()
    elif from_format == "pcm16":
        pcm_data = audio_data
    elif from_format == "pcm24":
        # 24-bit to 16-bit (take every 3 bytes and convert to 2 bytes)
        pcm24_array = np.frombuffer(audio_data, dtype=np.uint8)
        pcm24_reshaped = pcm24_array.reshape(-1, 3)
        pcm16_array = ((pcm24_reshaped[:, 1] << 8) | pcm24_reshaped[:, 2]).astype(np.int16)
        pcm_data = pcm16_array.tobytes()
    elif from_format == "pcm32":
        # 32-bit to 16-bit
        pcm32_array = np.frombuffer(audio_data, dtype=np.int32)
        pcm16_array = (pcm32_array >> 16).astype(np.int16)
        pcm_data = pcm16_array.tobytes()
    else:
        raise ValueError(f"Unsupported source format: {from_format}")

    # Step 2: Resample if sample rates differ
    if from_rate != to_rate:
        # Convert to numpy array for resampling
        pcm_array = np.frombuffer(pcm_data, dtype=np.int16)

        # Resample using soxr (high-quality resampler)
        resampled = soxr.resample(
            pcm_array.astype(np.float32) / 32768.0,  # Normalize to [-1, 1]
            from_rate,
            to_rate,
            quality="HQ",  # High quality resampling
        )

        # Convert back to int16
        pcm_data = (resampled * 32768.0).astype(np.int16).tobytes()

    # Step 3: Convert to target format
    pcm16_array = np.frombuffer(pcm_data, dtype=np.int16)

    if to_format == "mulaw":
        # Linear PCM to μ-law
        mulaw_array = _ulaw_compress(pcm16_array)
        output_data = mulaw_array.tobytes()
    elif to_format == "pcm8":
        # 16-bit to 8-bit PCM
        pcm8_array = ((pcm16_array >> 8) + 128).astype(np.uint8)
        output_data = pcm8_array.tobytes()
    elif to_format == "pcm16":
        output_data = pcm_data
    elif to_format == "pcm24":
        # 16-bit to 24-bit
        pcm24_array = np.zeros(len(pcm16_array) * 3, dtype=np.uint8)
        pcm24_array[1::3] = (pcm16_array >> 8) & 0xFF
        pcm24_array[2::3] = pcm16_array & 0xFF
        output_data = pcm24_array.tobytes()
    elif to_format == "pcm32":
        # 16-bit to 32-bit
        pcm32_array = (pcm16_array.astype(np.int32) << 16)
        output_data = pcm32_array.tobytes()
    else:
        raise ValueError(f"Unsupported target format: {to_format}")

    return output_data


def twilio_to_gemini(mulaw_base64: str) -> bytes:
    """
    Convert Twilio μ-law audio (8kHz) to Gemini PCM16 (16kHz).

    Gemini 2.5 native audio model expects 16kHz PCM input.

    Args:
        mulaw_base64: Base64-encoded μ-law audio from Twilio

    Returns:
        PCM16 audio bytes at 16kHz for Gemini 2.5
    """
    # Decode base64
    mulaw_data = base64.b64decode(mulaw_base64)

    # Convert μ-law to linear PCM 16-bit
    mulaw_array = np.frombuffer(mulaw_data, dtype=np.uint8)
    pcm_array = _ulaw_decompress(mulaw_array)

    # Resample from 8kHz to 16kHz (Gemini 2.5 native audio input)
    resampled = soxr.resample(
        pcm_array.astype(np.float32) / 32768.0, 8000, 16000, quality="HQ"
    )

    # Convert back to int16 bytes
    pcm_16khz = (resampled * 32768.0).astype(np.int16).tobytes()

    return pcm_16khz


def gemini_to_twilio(pcm_24khz: bytes) -> str:
    """
    Convert Gemini PCM16 audio (24kHz) to Twilio μ-law (8kHz).

    Gemini 2.5 native audio model outputs 24kHz PCM (unchanged).

    Args:
        pcm_24khz: PCM16 audio bytes at 24kHz from Gemini 2.5

    Returns:
        Base64-encoded μ-law audio for Twilio
    """
    # Resample from 24kHz to 8kHz (Gemini 2.5 output is still 24kHz)
    pcm_array = np.frombuffer(pcm_24khz, dtype=np.int16)
    resampled = soxr.resample(
        pcm_array.astype(np.float32) / 32768.0, 24000, 8000, quality="HQ"
    )

    # Convert to int16
    pcm_8khz = (resampled * 32768.0).astype(np.int16)

    # Convert to μ-law
    mulaw_array = _ulaw_compress(pcm_8khz)

    # Encode to base64
    return base64.b64encode(mulaw_array.tobytes()).decode("utf-8")


def calculate_audio_duration(audio_data: bytes, sample_rate: int, sample_width: int = 2) -> float:
    """
    Calculate duration of audio in seconds.

    Args:
        audio_data: Audio bytes
        sample_rate: Sample rate in Hz
        sample_width: Bytes per sample (2 for 16-bit PCM)

    Returns:
        Duration in seconds
    """
    num_samples = len(audio_data) // sample_width
    return num_samples / sample_rate


def chunk_audio(audio_data: bytes, chunk_duration_ms: int, sample_rate: int) -> list[bytes]:
    """
    Split audio into fixed-duration chunks.

    Args:
        audio_data: Audio bytes
        chunk_duration_ms: Duration of each chunk in milliseconds
        sample_rate: Sample rate in Hz

    Returns:
        List of audio chunks
    """
    bytes_per_sample = 2  # 16-bit PCM
    samples_per_chunk = int(sample_rate * chunk_duration_ms / 1000)
    bytes_per_chunk = samples_per_chunk * bytes_per_sample

    chunks = []
    for i in range(0, len(audio_data), bytes_per_chunk):
        chunk = audio_data[i : i + bytes_per_chunk]
        if len(chunk) > 0:
            chunks.append(chunk)

    return chunks

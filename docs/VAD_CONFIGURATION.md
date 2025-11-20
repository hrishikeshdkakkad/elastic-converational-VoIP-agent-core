# Voice Activity Detection (VAD) Configuration Guide

## Overview

Voice Activity Detection (VAD) is a critical component of the Voice AI System that determines when a user is speaking. It enables natural conversations by:
- Detecting the start and end of speech
- Allowing interruptions (barge-in)
- Managing conversation turns
- Reducing false positives and unnecessary processing

This system uses Google's Gemini Live API VAD capabilities with configurable parameters optimized for phone conversations.

## Architecture

The VAD configuration flows through the following components:

1. **CallWorkflowInput** → Initial configuration from API request
2. **VoiceCallWorkflow** → Stores and provides config via query
3. **Twilio WebSocket Handler** → Retrieves config and applies defaults
4. **AudioBridgeSession** → Configures Gemini Live API with VAD settings
5. **Gemini Live API** → Processes audio with VAD

## Configuration Parameters

### Basic Settings

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `disabled` | bool | `false` | Enable/disable automatic VAD |
| `start_sensitivity` | string | `"LOW"` | Sensitivity for detecting speech start |
| `end_sensitivity` | string | `"LOW"` | Sensitivity for detecting speech end |
| `prefix_padding_ms` | int | `100` | Ms of speech required before confirming start |
| `silence_duration_ms` | int | `700` | Ms of silence required before ending speech |

### Sensitivity Options

#### Start Sensitivity
- **`"HIGH"`**: More sensitive, detects speech earlier
  - Pro: Faster response to user input
  - Con: More false positives from background noise
- **`"LOW"`** (default): Less sensitive
  - Pro: Fewer false starts
  - Con: May miss very short utterances
- Note: Internally uses `types.StartSensitivity.START_SENSITIVITY_HIGH` or `START_SENSITIVITY_LOW`

#### End Sensitivity
- **`"HIGH"`**: Ends speech detection more frequently
  - Pro: Faster turn-taking
  - Con: May cut off natural pauses
- **`"LOW"`** (default): Allows longer pauses
  - Pro: Natural conversation flow
  - Con: Slightly increased latency
- Note: Internally uses `types.EndSensitivity.END_SENSITIVITY_HIGH` or `END_SENSITIVITY_LOW`

### Timing Parameters

#### Prefix Padding (prefix_padding_ms)
- **Lower values (20-50ms)**: Very responsive but more false positives
- **Medium values (100ms)** (default): Balanced approach
- **Higher values (200-500ms)**: Very accurate but less responsive

#### Silence Duration (silence_duration_ms)
- **Lower values (100-300ms)**: Quick turn-taking, may interrupt
- **Medium values (700ms)** (default): Natural conversation pauses
- **Higher values (1000-2000ms)**: Allows thinking time, higher latency

## Usage Examples

### Default Configuration (Phone Calls)
```python
# No VAD config needed - optimized defaults are applied
input_data = CallWorkflowInput(
    phone_number="+1234567890",
    greeting="Hello, how can I help you?",
    system_prompt="You are a helpful assistant"
)
```

### Custom VAD for Noisy Environments
```python
input_data = CallWorkflowInput(
    phone_number="+1234567890",
    vad_config={
        "start_sensitivity": "LOW",  # Reduce false starts
        "end_sensitivity": "HIGH",   # Quick turn-taking
        "prefix_padding_ms": 200,    # More validation
        "silence_duration_ms": 500   # Shorter pauses
    }
)
```

### VAD for Slow/Thoughtful Speakers
```python
input_data = CallWorkflowInput(
    phone_number="+1234567890",
    vad_config={
        "start_sensitivity": "HIGH",  # Catch soft speech
        "end_sensitivity": "LOW",      # Allow pauses
        "prefix_padding_ms": 50,       # Quick response
        "silence_duration_ms": 1500    # Long thinking pauses
    }
)
```

### Disable Automatic VAD (Manual Control)
```python
input_data = CallWorkflowInput(
    phone_number="+1234567890",
    vad_config={
        "disabled": True  # Manual activity detection
    }
)
```
Note: When disabled, the client must send activity start/end signals manually.

## Implementation Details

### Audio Bridge Integration

The VAD configuration is applied in `audio_bridge.py` when creating the Gemini Live API session:

```python
from google.genai import types

config = {
    "realtime_input_config": {
        "automatic_activity_detection": {
            "disabled": vad_config.get("disabled", False),
            "start_of_speech_sensitivity": types.StartSensitivity.START_SENSITIVITY_LOW,
            "end_of_speech_sensitivity": types.EndSensitivity.END_SENSITIVITY_LOW,
            "prefix_padding_ms": vad_config.get("prefix_padding_ms", 100),
            "silence_duration_ms": vad_config.get("silence_duration_ms", 700)
        },
        "activity_handling": types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        "turn_coverage": types.TurnCoverage.TURN_INCLUDES_ALL_INPUT
    }
}
```

**Important:** The API requires proper enum types from `google.genai.types`, not string values.

### Transcription Support

When VAD is enabled, the system also captures transcriptions:
- **Input transcription**: User's speech-to-text
- **Output transcription**: AI's text-to-speech

These transcriptions are stored in the transcript buffer for debugging and analysis.

### Interruption Handling

The system supports "barge-in" interruptions by default:
- When VAD detects user speech during AI response
- The AI's current response is cancelled
- Audio queue is cleared to prevent outdated responses
- New turn begins with user's input

## Best Practices

### For Phone Conversations
1. Use default settings (LOW/LOW sensitivity)
2. Allow 700ms silence for natural pauses
3. Keep prefix padding at 100ms for balance

### For Customer Service
1. Enable quick turn-taking (HIGH end sensitivity)
2. Reduce silence duration to 500ms
3. Keep start sensitivity LOW to avoid noise

### For Educational/Tutorial Calls
1. Allow longer pauses (1000-1500ms silence)
2. Use HIGH start sensitivity for soft speakers
3. Keep end sensitivity LOW for thinking time

### Monitoring VAD Performance

Monitor these metrics to tune VAD:
- False start rate (too high = reduce start sensitivity)
- Cut-off complaints (too many = reduce end sensitivity)
- Response lag (too high = reduce prefix padding)
- Interruption issues (adjust silence duration)

## Troubleshooting

### Common Issues

1. **AI responds to background noise**
   - Reduce start_sensitivity to LOW
   - Increase prefix_padding_ms

2. **User gets cut off mid-sentence**
   - Reduce end_sensitivity to LOW
   - Increase silence_duration_ms

3. **Slow response to user input**
   - Increase start_sensitivity to HIGH
   - Reduce prefix_padding_ms

4. **Can't interrupt the AI**
   - Ensure activity_handling is set to START_OF_ACTIVITY_INTERRUPTS
   - Check that VAD is not disabled

### Debug Logging

Enable detailed logging to debug VAD issues:

```python
# In audio_bridge.py
logger.info(f"VAD config: {self._vad_config}")
logger.info(f"User transcription: {transcription.text}")
logger.info(f"AI output transcription: {transcription.text}")
```

## API Reference

### CallWorkflowInput.vad_config

```python
vad_config: dict[str, Any] | None = Field(
    default=None,
    description="Voice Activity Detection configuration"
)
```

Supported keys:
- `disabled` (bool): Enable/disable automatic VAD
- `start_sensitivity` (str): "HIGH" or "LOW"
- `end_sensitivity` (str): "HIGH" or "LOW"
- `prefix_padding_ms` (int): 20-500 recommended
- `silence_duration_ms` (int): 100-2000 recommended

## Future Enhancements

Potential improvements for VAD:
1. Dynamic adjustment based on noise levels
2. Per-speaker VAD profiles
3. Language-specific VAD tuning
4. ML-based parameter optimization
5. Real-time VAD metrics dashboard
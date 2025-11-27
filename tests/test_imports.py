"""Basic import tests to verify setup."""

import pytest


def test_config_import():
    """Test that config can be imported."""
    from src.voice_ai_system.config import settings

    assert settings is not None
    assert settings.temporal_host is not None


def test_workflow_import():
    """Test that workflow can be imported."""
    from src.voice_ai_system.workflows.call_workflow import VoiceCallWorkflow

    assert VoiceCallWorkflow is not None


def test_activities_import():
    """Test that activities can be imported."""
    from src.voice_ai_system.activities import (
        database_activities,
        twilio_activities,
    )

    assert database_activities is not None
    assert twilio_activities is not None


def test_api_import():
    """Test that API can be imported."""
    from src.voice_ai_system.api.main import app

    assert app is not None


def test_models_import():
    """Test that models can be imported."""
    from src.voice_ai_system.models.call import CallWorkflowInput, CallWorkflowResult

    assert CallWorkflowInput is not None
    assert CallWorkflowResult is not None


def test_utils_import():
    """Test that utilities can be imported."""
    from src.voice_ai_system.utils.audio import twilio_to_gemini, gemini_to_twilio
    from src.voice_ai_system.utils.logging import get_logger

    assert twilio_to_gemini is not None
    assert gemini_to_twilio is not None
    assert get_logger is not None

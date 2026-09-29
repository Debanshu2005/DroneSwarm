import asyncio
import pytest
from unittest.mock import MagicMock
from DroneOS1.core.command_handler import CommandHandler
from DroneOS1.core.intents import FlightIntent, IntentSource, IntentAction


def _make_intent(command_id=None):
    params = {}
    if command_id is not None:
        params["_command_id"] = command_id
    return FlightIntent(IntentSource.MANUAL, IntentAction.TAKEOFF, params=params)


@pytest.mark.asyncio
async def test_on_pipeline_intent_result_unknown_command_id_does_not_raise():
    handler = CommandHandler(node_id="drone_test")
    intent = _make_intent(command_id="nonexistent-id")
    # Must return silently without raising
    await handler.on_pipeline_intent_result(intent, True)


@pytest.mark.asyncio
async def test_on_pipeline_intent_result_missing_command_id_does_not_raise():
    handler = CommandHandler(node_id="drone_test")
    intent = _make_intent()  # no _command_id in params
    await handler.on_pipeline_intent_result(intent, False)


@pytest.mark.asyncio
async def test_on_pipeline_intent_result_resolves_future_on_success():
    handler = CommandHandler(node_id="drone_test")
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    handler._pending_pipeline_commands["cmd-123"] = {
        "future": future,
        "sender_id": "gcs",
        "action": "TAKEOFF",
        "cmd_id": "cmd-123",
    }
    handler._send_lifecycle = MagicMock()

    intent = _make_intent(command_id="cmd-123")
    await handler.on_pipeline_intent_result(intent, True)

    assert future.done()
    assert future.result() is True


@pytest.mark.asyncio
async def test_on_pipeline_intent_result_resolves_future_on_failure():
    handler = CommandHandler(node_id="drone_test")
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    handler._pending_pipeline_commands["cmd-456"] = {
        "future": future,
        "sender_id": "gcs",
        "action": "LAND",
        "cmd_id": "cmd-456",
    }
    handler._send_lifecycle = MagicMock()

    intent = _make_intent(command_id="cmd-456")
    await handler.on_pipeline_intent_result(intent, False)

    assert future.done()
    assert future.result() is False


@pytest.mark.asyncio
async def test_on_pipeline_intent_result_does_not_set_already_done_future():
    handler = CommandHandler(node_id="drone_test")
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    future.set_result(True)  # already done
    handler._pending_pipeline_commands["cmd-789"] = {
        "future": future,
        "sender_id": "gcs",
        "action": "RTL",
        "cmd_id": "cmd-789",
    }
    handler._send_lifecycle = MagicMock()

    intent = _make_intent(command_id="cmd-789")
    # Must not raise InvalidStateError
    await handler.on_pipeline_intent_result(intent, False)
    assert future.result() is True  # unchanged

"""Session-level pytest configuration to prevent partial-import races on torch."""

import importlib
import pytest


@pytest.fixture(scope="session", autouse=True)
def _preload_torch_if_available():
    """Pre-import torch once on the main thread before any test patches sys.modules.

    Prevents tests that use patch.dict('sys.modules', {'torch': ...}) from
    removing 'torch' while C extensions (torch._C) remain loaded, which would
    otherwise cause RuntimeError ("function '_has_torch_function' already has a
    docstring") on subsequent imports.
    """
    try:
        importlib.import_module("torch")
    except ImportError:
        pass

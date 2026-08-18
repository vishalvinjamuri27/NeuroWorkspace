"""Recording adapters and registry."""

from neuro_workspace.adapters.csv_adapter import CSVAdapter
from neuro_workspace.adapters.mne_adapter import (
    BDFAdapter,
    BrainVisionAdapter,
    EDFAdapter,
    EEGLABAdapter,
    FIFAdapter,
)
from neuro_workspace.adapters.registry import AdapterRegistry, default_registry

__all__ = [
    "AdapterRegistry",
    "BDFAdapter",
    "BrainVisionAdapter",
    "CSVAdapter",
    "EDFAdapter",
    "EEGLABAdapter",
    "FIFAdapter",
    "default_registry",
]

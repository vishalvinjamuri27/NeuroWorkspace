from __future__ import annotations

from pathlib import Path

import pytest

from neuro_workspace.adapters.mne_adapter import BrainVisionAdapter
from neuro_workspace.auth import Identity, SessionSigner, require_capability
from neuro_workspace.config import Settings
from neuro_workspace.domain import Capability, Role
from neuro_workspace.errors import ForbiddenError, NeuroError, ValidationError


def test_signed_session_round_trip_and_tamper_rejection() -> None:
    signer = SessionSigner("test-secret", 600)
    identity = Identity("subject", "person@example.test", "Person")
    assert signer.loads(signer.dumps(identity)) == identity
    with pytest.raises(NeuroError, match="Sign in again"):
        signer.loads(signer.dumps(identity) + "x")


def test_role_capabilities_are_centralized() -> None:
    require_capability(Role.VIEWER, Capability.READ)
    with pytest.raises(ForbiddenError):
        require_capability(Role.VIEWER, Capability.ANNOTATE)


def test_production_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="DEV_AUTH_ENABLED"):
        Settings(environment="production")


def test_brainvision_reports_missing_companion(tmp_path: Path) -> None:
    header = tmp_path / "recording.vhdr"
    header.write_text(
        "Brain Vision Data Exchange Header File Version 1.0\n"
        "[Common Infos]\nDataFile=recording.eeg\nMarkerFile=recording.vmrk\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as error:
        BrainVisionAdapter().validate_companions_only([header])
    assert error.value.code == "missing_companion"
    assert error.value.details["missing_file"] in {"recording.eeg", "recording.vmrk"}

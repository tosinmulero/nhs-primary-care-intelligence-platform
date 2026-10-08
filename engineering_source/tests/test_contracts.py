from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError
from primarycare.contracts import ClinicalApproval, DemoPatient


def test_synthetic_patient_passes():
    p = DemoPatient(patient_id="PAT-000001", date_of_birth=date(1985, 7, 3),
                    sex_at_registration="unknown", area_group="urban")
    assert p.patient_id == "PAT-000001"


def test_authentic_nhs_number_shaped_identifier_is_rejected():
    with pytest.raises(ValidationError):
        DemoPatient(patient_id="1234567890", date_of_birth=date(1985, 7, 3),
                    sex_at_registration="unknown", area_group="urban")


def test_future_birthdate_is_rejected():
    with pytest.raises(ValidationError):
        DemoPatient(patient_id="PAT-000002", date_of_birth=date(2999, 1, 1),
                    sex_at_registration="unknown", area_group="urban")


def test_clinician_review_approved():
    a = ClinicalApproval(reviewer_id="STF-0001", reviewer_role="GP",
                         decision="approved", reviewed_at=datetime.now(timezone.utc))
    assert a.decision == "approved"


def test_admin_cannot_authorise_clinical_changes():
    with pytest.raises(ValidationError):
        ClinicalApproval(reviewer_id="STF-0002", reviewer_role="DATA_ADMIN",
                         decision="approved", reviewed_at=datetime.now(timezone.utc))


def test_timezone_naive_approval_rejected():
    with pytest.raises(ValidationError):
        ClinicalApproval(reviewer_id="STF-0001", reviewer_role="GP",
                         decision="approved", reviewed_at=datetime(2026, 1, 1))
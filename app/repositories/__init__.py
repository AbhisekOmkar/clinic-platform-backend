from app.repositories.appointment import AppointmentRepository, appointment_repository
from app.repositories.branch import BranchRepository, branch_repository
from app.repositories.call import CallRepository, call_repository
from app.repositories.call_session import CallSessionRepository, call_session_repository
from app.repositories.followup import FollowupRepository, followup_repository
from app.repositories.latency_metrics import (
    LatencyMetricsRepository,
    latency_metrics_repository,
)
from app.repositories.outbound_call import OutboundCallRepository, outbound_call_repository
from app.repositories.patient import PatientRepository, patient_repository
from app.repositories.pms_appointment import PmsAppointmentRepository, pms_appointment_repository
from app.repositories.practitioner import PractitionerRepository, practitioner_repository

__all__ = [
    "AppointmentRepository",
    "appointment_repository",
    "BranchRepository",
    "branch_repository",
    "CallRepository",
    "call_repository",
    "CallSessionRepository",
    "call_session_repository",
    "FollowupRepository",
    "followup_repository",
    "LatencyMetricsRepository",
    "latency_metrics_repository",
    "OutboundCallRepository",
    "outbound_call_repository",
    "PatientRepository",
    "patient_repository",
    "PmsAppointmentRepository",
    "pms_appointment_repository",
    "PractitionerRepository",
    "practitioner_repository",
]

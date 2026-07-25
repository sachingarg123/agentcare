"""Repository layer — thin SQLAlchemy access for tools, auth, and services.

Agents never write raw SQL. Tools call these helpers; helpers use a Session.
"""

from db.repositories.user_repo import UserRepository
from db.repositories.patient_repo import PatientRepository
from db.repositories.department_repo import DepartmentRepository
from db.repositories.doctor_repo import DoctorRepository
from db.repositories.slot_repo import SlotRepository
from db.repositories.appointment_repo import AppointmentRepository
from db.repositories.document_repo import DocumentRepository
from db.repositories.workflow_repo import WorkflowRepository
from db.repositories.reminder_repo import ReminderRepository
from db.repositories.escalation_repo import EscalationRepository
from db.repositories.audit_repo import AuditRepository
from db.repositories.notification_repo import NotificationRepository

__all__ = [
    "UserRepository",
    "PatientRepository",
    "DepartmentRepository",
    "DoctorRepository",
    "SlotRepository",
    "AppointmentRepository",
    "DocumentRepository",
    "WorkflowRepository",
    "ReminderRepository",
    "EscalationRepository",
    "AuditRepository",
    "NotificationRepository",
]

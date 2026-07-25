"""Patient document metadata — storage path + checksum for dedupe."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import DocumentType, PatientDocument


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, document_id: str) -> PatientDocument | None:
        return self.db.get(PatientDocument, document_id)

    def list_for_patient(self, patient_id: str) -> list[PatientDocument]:
        stmt = (
            select(PatientDocument)
            .where(PatientDocument.patient_id == patient_id)
            .order_by(PatientDocument.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def find_by_checksum(
        self, patient_id: str, checksum: str
    ) -> PatientDocument | None:
        stmt = select(PatientDocument).where(
            PatientDocument.patient_id == patient_id,
            PatientDocument.checksum == checksum,
        )
        return self.db.scalars(stmt).first()

    def create(
        self,
        *,
        patient_id: str,
        file_path: str,
        checksum: str,
        document_type: str = DocumentType.UNKNOWN.value,
        document_date: date | None = None,
    ) -> PatientDocument:
        doc = PatientDocument(
            patient_id=patient_id,
            file_path=file_path,
            checksum=checksum,
            document_type=document_type,
            document_date=document_date,
        )
        self.db.add(doc)
        self.db.flush()
        return doc

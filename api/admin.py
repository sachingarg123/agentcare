"""Admin API — CRUD departments, doctors, slots (Phase 5.5). ADMIN only."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.schemas import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    DoctorCreate,
    DoctorOut,
    DoctorUpdate,
    SlotCreate,
    SlotOut,
    SlotUpdate,
)
from auth.dependencies import require_role
from db.models import SlotStatus, User
from db.repositories import (
    AuditRepository,
    DepartmentRepository,
    DoctorRepository,
    SlotRepository,
)
from db.session import get_db

router = APIRouter(tags=["admin"])


def _normalize_dt(value: datetime) -> datetime:
    """SQLite often returns naive datetimes; request bodies may be aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _audit(
    db: Session,
    *,
    user: User,
    action: str,
    entity_type: str,
    entity_id: str | None,
    metadata: dict | None = None,
) -> None:
    AuditRepository(db).create(
        actor_id=user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        event_metadata=metadata or {},
    )


# --- Departments ---


@router.get("/staff/departments", response_model=list[DepartmentOut])
def list_departments(
    active_only: bool = Query(default=False),
    user: User = Depends(require_role("STAFF", "ADMIN")),
    db: Session = Depends(get_db),
) -> list[DepartmentOut]:
    """Read departments for Admin CRUD and Staff HITL department override."""
    repo = DepartmentRepository(db)
    rows = repo.list_active() if active_only else repo.list_all()
    return [DepartmentOut.model_validate(r) for r in rows]


@router.post(
    "/staff/departments",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_department(
    body: DepartmentCreate,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    existing = DepartmentRepository(db).get_by_name(body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Department already exists: {body.name}",
        )
    try:
        dept = DepartmentRepository(db).create(
            name=body.name,
            description=body.description,
            active=body.active,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Department name conflict",
        ) from exc
    _audit(
        db,
        user=user,
        action="department_create",
        entity_type="department",
        entity_id=dept.id,
        metadata={"name": dept.name},
    )
    db.commit()
    return DepartmentOut.model_validate(dept)


@router.get("/staff/departments/{department_id}", response_model=DepartmentOut)
def get_department(
    department_id: str,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    dept = DepartmentRepository(db).get_by_id(department_id)
    if dept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return DepartmentOut.model_validate(dept)


@router.patch("/staff/departments/{department_id}", response_model=DepartmentOut)
def update_department(
    department_id: str,
    body: DepartmentUpdate,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    repo = DepartmentRepository(db)
    dept = repo.get_by_id(department_id)
    if dept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    try:
        dept = repo.update(
            dept,
            name=body.name,
            description=body.description,
            active=body.active,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Department name conflict",
        ) from exc
    _audit(
        db,
        user=user,
        action="department_update",
        entity_type="department",
        entity_id=dept.id,
        metadata=body.model_dump(exclude_unset=True),
    )
    db.commit()
    return DepartmentOut.model_validate(dept)


@router.delete("/staff/departments/{department_id}", response_model=DepartmentOut)
def delete_department(
    department_id: str,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DepartmentOut:
    """Soft-delete: set active=False (keeps FK integrity for doctors/slots)."""
    repo = DepartmentRepository(db)
    dept = repo.get_by_id(department_id)
    if dept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    dept = repo.deactivate(dept)
    _audit(
        db,
        user=user,
        action="department_deactivate",
        entity_type="department",
        entity_id=dept.id,
    )
    db.commit()
    return DepartmentOut.model_validate(dept)


# --- Doctors ---


@router.get("/staff/doctors", response_model=list[DoctorOut])
def list_doctors(
    department_id: str | None = Query(default=None),
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> list[DoctorOut]:
    rows = DoctorRepository(db).list_all(department_id=department_id)
    return [DoctorOut.model_validate(r) for r in rows]


@router.post(
    "/staff/doctors",
    response_model=DoctorOut,
    status_code=status.HTTP_201_CREATED,
)
def create_doctor(
    body: DoctorCreate,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DoctorOut:
    if DepartmentRepository(db).get_by_id(body.department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    doctor = DoctorRepository(db).create(
        department_id=body.department_id,
        name=body.name,
        active=body.active,
    )
    _audit(
        db,
        user=user,
        action="doctor_create",
        entity_type="doctor",
        entity_id=doctor.id,
        metadata={"name": doctor.name, "department_id": doctor.department_id},
    )
    db.commit()
    return DoctorOut.model_validate(doctor)


@router.get("/staff/doctors/{doctor_id}", response_model=DoctorOut)
def get_doctor(
    doctor_id: str,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DoctorOut:
    doctor = DoctorRepository(db).get_by_id(doctor_id)
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    return DoctorOut.model_validate(doctor)


@router.patch("/staff/doctors/{doctor_id}", response_model=DoctorOut)
def update_doctor(
    doctor_id: str,
    body: DoctorUpdate,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DoctorOut:
    repo = DoctorRepository(db)
    doctor = repo.get_by_id(doctor_id)
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    if body.department_id and DepartmentRepository(db).get_by_id(body.department_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    doctor = repo.update(
        doctor,
        department_id=body.department_id,
        name=body.name,
        active=body.active,
    )
    _audit(
        db,
        user=user,
        action="doctor_update",
        entity_type="doctor",
        entity_id=doctor.id,
        metadata=body.model_dump(exclude_unset=True),
    )
    db.commit()
    return DoctorOut.model_validate(doctor)


@router.delete("/staff/doctors/{doctor_id}", response_model=DoctorOut)
def delete_doctor(
    doctor_id: str,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> DoctorOut:
    """Soft-delete: set active=False."""
    repo = DoctorRepository(db)
    doctor = repo.get_by_id(doctor_id)
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    doctor = repo.deactivate(doctor)
    _audit(
        db,
        user=user,
        action="doctor_deactivate",
        entity_type="doctor",
        entity_id=doctor.id,
    )
    db.commit()
    return DoctorOut.model_validate(doctor)


# --- Slots ---


@router.get("/staff/slots", response_model=list[SlotOut])
def list_slots(
    doctor_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> list[SlotOut]:
    repo = SlotRepository(db)
    if doctor_id:
        rows = repo.list_for_doctor(doctor_id, limit=limit)
    else:
        rows = repo.list_all(limit=limit)
    return [SlotOut.model_validate(r) for r in rows]


@router.post(
    "/staff/slots",
    response_model=SlotOut,
    status_code=status.HTTP_201_CREATED,
)
def create_slot(
    body: SlotCreate,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> SlotOut:
    if DoctorRepository(db).get_by_id(body.doctor_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    if body.end_time <= body.start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )
    if body.status not in {s.value for s in SlotStatus}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {body.status}",
        )
    slot = SlotRepository(db).create(
        doctor_id=body.doctor_id,
        start_time=_normalize_dt(body.start_time),
        end_time=_normalize_dt(body.end_time),
        status=body.status,
    )
    _audit(
        db,
        user=user,
        action="slot_create",
        entity_type="appointment_slot",
        entity_id=slot.id,
        metadata={"doctor_id": slot.doctor_id},
    )
    db.commit()
    return SlotOut.model_validate(slot)


@router.get("/staff/slots/{slot_id}", response_model=SlotOut)
def get_slot(
    slot_id: str,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> SlotOut:
    slot = SlotRepository(db).get_by_id(slot_id)
    if slot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found")
    return SlotOut.model_validate(slot)


@router.patch("/staff/slots/{slot_id}", response_model=SlotOut)
def update_slot(
    slot_id: str,
    body: SlotUpdate,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> SlotOut:
    repo = SlotRepository(db)
    slot = repo.get_by_id(slot_id)
    if slot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found")
    if body.doctor_id and DoctorRepository(db).get_by_id(body.doctor_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    start = _normalize_dt(
        body.start_time if body.start_time is not None else slot.start_time
    )
    end = _normalize_dt(body.end_time if body.end_time is not None else slot.end_time)
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )
    if body.status is not None and body.status not in {s.value for s in SlotStatus}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {body.status}",
        )
    slot = repo.update(
        slot,
        doctor_id=body.doctor_id,
        start_time=_normalize_dt(body.start_time) if body.start_time is not None else None,
        end_time=_normalize_dt(body.end_time) if body.end_time is not None else None,
        status=body.status,
    )
    _audit(
        db,
        user=user,
        action="slot_update",
        entity_type="appointment_slot",
        entity_id=slot.id,
        metadata=body.model_dump(exclude_unset=True, mode="json"),
    )
    db.commit()
    return SlotOut.model_validate(slot)


@router.delete("/staff/slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_slot(
    slot_id: str,
    user: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> None:
    """Hard-delete only AVAILABLE slots (BOOKED ones keep appointment FKs)."""
    repo = SlotRepository(db)
    slot = repo.get_by_id(slot_id)
    if slot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found")
    if slot.status != SlotStatus.AVAILABLE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only AVAILABLE slots can be deleted (status={slot.status})",
        )
    slot_id_kept = slot.id
    repo.delete(slot)
    _audit(
        db,
        user=user,
        action="slot_delete",
        entity_type="appointment_slot",
        entity_id=slot_id_kept,
    )
    db.commit()

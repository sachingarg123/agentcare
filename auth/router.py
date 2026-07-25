"""Auth HTTP routes — register, login, /me (Phase 5.2)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from auth.jwt import create_access_token
from auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from auth.service import authenticate_user, register_patient
from db.models import User, UserRole
from db.repositories import PatientRepository
from db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Public patient self-registration; returns JWT (auto-login)."""
    try:
        user = register_patient(
            db,
            name=body.name,
            email=str(body.email),
            password=body.password,
            phone=body.phone,
        )
    except ValueError as exc:
        if str(exc) == "email_taken":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            ) from exc
        raise
    db.commit()
    token = create_access_token(user_id=user.id, role=user.role)
    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate_user(db, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = create_access_token(user_id=user.id, role=user.role)
    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    patient_id = None
    if user.role == UserRole.PATIENT.value:
        profile = PatientRepository(db).get_by_user_id(user.id)
        patient_id = profile.id if profile else None
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        patient_id=patient_id,
    )

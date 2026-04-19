from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from api.auth import create_access_token, hash_password, require_admin, require_analyst, require_auth, require_lead, verify_password
from api.database import get_db
from api.models import User, UserRole
from api.schemas import Token, UserCreate, UserOut, UserPatch

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    return {"access_token": create_access_token({"sub": user.username}), "token_type": "bearer"}

@router.post("/register", response_model=UserOut, status_code=201)
def register(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(username=body.username, email=body.email,
                hashed_password=hash_password(body.password), role=body.role, is_active=True)
    db.add(user); db.commit(); db.refresh(user)
    return user

@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.id).all()

@router.get("/assignable-users", response_model=List[UserOut])
def list_assignable_users(db: Session = Depends(get_db), _: User = Depends(require_analyst)):
    return (
        db.query(User)
        .filter(User.is_active == True, User.role.in_([UserRole.analyst, UserRole.lead, UserRole.admin]))
        .order_by(User.username)
        .all()
    )

@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(require_auth)):
    return current_user

@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserPatch,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # If demoting the last admin, block it
    if body.role is not None and body.role != UserRole.admin and user.role == UserRole.admin:
        admin_count = db.query(User).filter(
            User.role == UserRole.admin, User.is_active == True, User.id != user_id
        ).count()
        if admin_count == 0:
            raise HTTPException(status_code=409,
                                detail="Cannot demote the only active admin")
    if body.username is not None:
        if db.query(User).filter(User.username == body.username, User.id != user_id).first():
            raise HTTPException(status_code=409, detail="Username already taken")
        user.username = body.username
    if body.email is not None:
        if db.query(User).filter(User.email == body.email, User.id != user_id).first():
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = body.email
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.hashed_password = hash_password(body.password)
    db.commit(); db.refresh(user)
    return user

@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=409, detail="Cannot delete your own account")
    # Block deletion if this is the only remaining admin
    if user.role == UserRole.admin:
        remaining_admins = db.query(User).filter(
            User.role == UserRole.admin, User.id != user_id
        ).count()
        if remaining_admins == 0:
            raise HTTPException(status_code=409,
                                detail="Cannot delete the only admin account. "
                                       "Create another admin first.")
    db.delete(user); db.commit()

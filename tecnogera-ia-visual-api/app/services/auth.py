"""Serviço de autenticação do portal — IAVS-030."""

from __future__ import annotations

import bcrypt
from sqlalchemy.orm import Session

from app.models.user import User


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Verifica credenciais e retorna o usuário se válido, None caso contrário."""
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return None
    if not user.is_active:
        return None
    if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return None
    return user

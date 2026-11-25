"""Вход и регистрация: пароль храним только как хеш (PBKDF2), вход — по токену в заголовке Authorization."""
import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Account, AuthToken, Courier, Pvz, get_session

ROLES = {"buyer": "покупатель", "pvz": "ПВЗ", "courier": "курьер", "supplier": "поставщик"}


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 20_000).hex()
    return digest, salt


def check_password(password: str, acc: Account) -> bool:
    return secrets.compare_digest(hash_password(password, acc.salt)[0], acc.pass_hash)


def issue_token(s: Session, acc: Account) -> str:
    token = secrets.token_hex(24)
    s.add(AuthToken(token=token, account_id=acc.id))
    s.commit()
    return token


def user_view(s: Session, acc: Account) -> dict:
    pvz = s.get(Pvz, acc.pvz_id) if acc.pvz_id else None
    courier = s.get(Courier, acc.courier_id) if acc.courier_id else None
    return {
        "id": acc.id, "role": acc.role, "login": acc.login, "name": acc.name,
        "pvz": {"id": pvz.id, "name": pvz.name, "address": pvz.address, "lat": pvz.lat, "lon": pvz.lon} if pvz else None,
        "courier": {"id": courier.id, "name": courier.name} if courier else None,
    }


def current_account(authorization: str | None = Header(None), s: Session = Depends(get_session)) -> Account:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Нужно войти в систему")
    row = s.get(AuthToken, authorization.removeprefix("Bearer ").strip())
    acc = s.get(Account, row.account_id) if row else None
    if acc is None:
        raise HTTPException(401, "Сессия устарела — войдите снова")
    return acc


def require(role: str):
    """Зависимость FastAPI: пускает только учётные записи нужной роли."""
    def dep(acc: Account = Depends(current_account)) -> Account:
        if acc.role != role:
            raise HTTPException(403, f"Этот раздел для роли «{ROLES[role]}»")
        return acc
    return dep


def find_account(s: Session, login: str) -> Account | None:
    return s.scalars(select(Account).where(Account.login == login.strip().lower())).first()

from __future__ import annotations

import hmac
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    phone         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    phone_verified INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS email_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_codes_lookup ON email_codes (email, purpose, consumed_at);

CREATE TABLE IF NOT EXISTS tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL UNIQUE,
    account_id   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_account ON tokens (account_id);
"""


class DuplicateAccount(ValueError):
    """Raised when an email or phone is already registered."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class AccountStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        if self.path.parent and str(self.path.parent):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # accounts
    # ------------------------------------------------------------------ #
    @staticmethod
    def _account(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_account(self, email: str, phone: str | None, password_hash: str) -> dict[str, Any]:
        now = iso(utcnow())
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO accounts (email, phone, password_hash, email_verified,"
                    " created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                    (email, phone, password_hash, now, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                message = str(e).lower()
                if "phone" in message:
                    raise DuplicateAccount("phone") from e
                raise DuplicateAccount("email") from e
        account = self.get_account_by_id(cur.lastrowid)
        assert account is not None
        return account

    def get_account_by_id(self, account_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return self._account(row)

    def get_account_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE email = ?", (email.lower(),)
            ).fetchone()
        return self._account(row)

    def get_account_by_phone(self, phone: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE phone = ?", (phone,)
            ).fetchone()
        return self._account(row)

    def get_account_by_login(self, login: str) -> dict[str, Any] | None:
        return (
            self.get_account_by_email(login)
            if "@" in login
            else self.get_account_by_phone(login)
        )

    def set_last_login(self, account_id: int) -> None:
        now = iso(utcnow())
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, account_id),
            )
            self._conn.commit()

    def update_phone(self, account_id: int, phone: str | None) -> dict[str, Any] | None:
        now = iso(utcnow())
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE accounts SET phone = ?, updated_at = ? WHERE id = ?",
                    (phone, now, account_id),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                raise DuplicateAccount("phone") from e
        return self.get_account_by_id(account_id)

    def update_password(self, account_id: int, password_hash: str) -> None:
        now = iso(utcnow())
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, now, account_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # email verification codes
    # ------------------------------------------------------------------ #
    def create_email_code(
        self, email: str, purpose: str, code_hash: str, ttl_minutes: int
    ) -> None:
        now = utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT INTO email_codes (email, purpose, code_hash, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    email.lower(),
                    purpose,
                    code_hash,
                    iso(now),
                    iso(now + timedelta(minutes=ttl_minutes)),
                ),
            )
            self._conn.commit()

    def last_code_time(self, email: str, purpose: str) -> datetime | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM email_codes WHERE email = ? AND purpose = ?"
                " ORDER BY id DESC LIMIT 1",
                (email.lower(), purpose),
            ).fetchone()
        return parse_iso(row["created_at"]) if row else None

    def count_codes_since(self, email: str, since: datetime) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM email_codes WHERE email = ? AND created_at >= ?",
                (email.lower(), iso(since)),
            ).fetchone()
        return int(row["n"]) if row else 0

    def consume_email_code(
        self, email: str, purpose: str, code_hash: str, max_attempts: int
    ) -> bool:
        now = utcnow()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM email_codes WHERE email = ? AND purpose = ?"
                " AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
                (email.lower(), purpose),
            ).fetchone()
            if row is None:
                return False
            if parse_iso(row["expires_at"]) <= now:
                return False
            if int(row["attempts"]) >= max_attempts:
                return False
            attempts = int(row["attempts"]) + 1
            if hmac.compare_digest(row["code_hash"], code_hash):
                self._conn.execute(
                    "UPDATE email_codes SET attempts = ?, consumed_at = ? WHERE id = ?",
                    (attempts, iso(now), row["id"]),
                )
                self._conn.commit()
                return True
            self._conn.execute(
                "UPDATE email_codes SET attempts = ? WHERE id = ?", (attempts, row["id"])
            )
            self._conn.commit()
            return False

    # ------------------------------------------------------------------ #
    # tokens
    # ------------------------------------------------------------------ #
    def create_token(self, account_id: int, token_hash: str, ttl_hours: int) -> dict[str, Any]:
        now = utcnow()
        expires = now + timedelta(hours=ttl_hours)
        with self._lock:
            self._conn.execute(
                "INSERT INTO tokens (token_hash, account_id, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (token_hash, account_id, iso(now), iso(expires)),
            )
            self._conn.commit()
        return {"expires_at": iso(expires)}

    def find_token(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        if parse_iso(row["expires_at"]) <= utcnow():
            return None
        return dict(row)

    def touch_token(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET last_used_at = ? WHERE token_hash = ?",
                (iso(utcnow()), token_hash),
            )
            self._conn.commit()

    def revoke_token(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (iso(utcnow()), token_hash),
            )
            self._conn.commit()

    def revoke_account_tokens(self, account_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL",
                (iso(utcnow()), account_id),
            )
            self._conn.commit()

    def purge_expired(self) -> None:
        now = utcnow()
        with self._lock:
            self._conn.execute(
                "DELETE FROM tokens WHERE expires_at < ? OR (revoked_at IS NOT NULL"
                " AND revoked_at < ?)",
                (iso(now - timedelta(days=7)), iso(now - timedelta(days=7))),
            )
            self._conn.execute(
                "DELETE FROM email_codes WHERE expires_at < ?",
                (iso(now - timedelta(days=1)),),
            )
            self._conn.commit()

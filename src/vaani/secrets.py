"""Secret Service backed API-key handling and non-destructive validation."""
from dataclasses import dataclass
import os
from typing import Any, Mapping

import httpx
import keyring

SERVICE = "vaani"
ACCOUNT = "groq"

class KeyringUnavailable(RuntimeError): pass
class KeyringLocked(KeyringUnavailable): pass
class KeyValidationError(RuntimeError): pass

@dataclass(frozen=True)
class EffectiveKey:
    value: str | None
    runtime_override: bool = False
    keyring_error: str | None = None

@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    transcription_available: bool
    cleanup_available: bool
    error: str | None = None

def _classify(exc: BaseException) -> KeyringUnavailable:
    text = str(exc).lower()
    cls = KeyringLocked if "lock" in text or "unlock" in text else KeyringUnavailable
    return cls("Secret Service is locked or unavailable")

class SecretServiceKeyStore:
    """Small adapter; no filesystem fallback and no secret-bearing attributes."""
    def __init__(self, backend: Any = keyring, *, service: str = SERVICE, account: str = ACCOUNT):
        self.backend, self.service, self.account = backend, service, account
    def get(self) -> str | None:
        try: return self.backend.get_password(self.service, self.account)
        except Exception as exc: raise _classify(exc) from None
    def set(self, value: str, *, state: Any = None) -> None:
        if state is not None and not can_edit_key(state): raise RuntimeError("API key changes disabled while active")
        if not isinstance(value, str) or not value.strip(): raise ValueError("API key must be non-empty")
        try: self.backend.set_password(self.service, self.account, value)
        except Exception as exc: raise _classify(exc) from None
    def remove(self, *, state: Any = None) -> None:
        if state is not None and not can_edit_key(state): raise RuntimeError("API key changes disabled while active")
        try: self.backend.delete_password(self.service, self.account)
        except Exception as exc:
            # keyring reports a missing item as an error; deletion is idempotent.
            if "not found" not in str(exc).lower() and "no password" not in str(exc).lower(): raise _classify(exc) from None

def effective_key(store: SecretServiceKeyStore, environ: Mapping[str, str] | None = None) -> EffectiveKey:
    value = (os.environ if environ is None else environ).get("GROQ_API_KEY")
    if value and value.strip(): return EffectiveKey(value, True)
    try: return EffectiveKey(store.get())
    except KeyringUnavailable as exc: return EffectiveKey(None, False, str(exc))

def masked_key_state(key: str | None, *, runtime_override: bool = False) -> str:
    if runtime_override: return "Runtime override active"
    if not key: return "No API key configured"
    return "API key configured (masked)"

def can_edit_key(state: Any) -> bool:
    name = getattr(state, "value", state)
    return str(name) not in {"Recording", "Processing", "recording", "processing"}

def validate_key(key: str, settings: Any, *, client: Any = None) -> ValidationResult:
    if not key or not isinstance(key, str): return ValidationResult(False, False, False, "missing key")
    base = settings.base_url.rstrip("/")
    close = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
    try:
        response = client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        if response.status_code != 200: return ValidationResult(False, False, False, f"HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return ValidationResult(False, False, False, "malformed models response")
        models = payload["data"]
        ids = {m.get("id") for m in models if isinstance(m, dict)}
        trans = settings.transcription_model in ids
        clean = settings.cleanup_model in ids
        return ValidationResult(trans, trans, clean, None if trans else "transcription model unavailable")
    except (httpx.HTTPError, ValueError) as exc: return ValidationResult(False, False, False, type(exc).__name__)
    finally:
        if close: client.close()

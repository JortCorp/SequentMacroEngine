"""Windows user-scoped DPAPI storage for Sequent secrets. Standard library only.

Encrypted blobs are tied to the current Windows user. Back up/re-pair before
moving to another Windows profile. Never fall back to plaintext on Windows.
"""
import base64
import ctypes
import os
from ctypes import wintypes

class DPAPIError(RuntimeError):
    pass

class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _run(data, protect):
    if os.name != 'nt':
        raise DPAPIError('Windows DPAPI is unavailable on this platform')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    method = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    method.restype = wintypes.BOOL
    method.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    src, hold = _blob(data)
    entropy, hold_entropy = _blob(b'Sequent remote configuration v1')
    result = DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN: do not prompt in background automation.
    if not method(ctypes.byref(src), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(result)):
        raise DPAPIError('Windows DPAPI operation failed: ' + str(ctypes.get_last_error()))
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def protect_text(value):
    if not isinstance(value, str):
        raise TypeError('DPAPI input must be text')
    return 'dpapi:v1:' + base64.b64encode(_run(value.encode('utf-8'), True)).decode('ascii')


def unprotect_text(value):
    if not isinstance(value, str) or not value.startswith('dpapi:v1:'):
        raise DPAPIError('Invalid DPAPI ciphertext')
    try:
        raw = base64.b64decode(value[9:], validate=True)
        return _run(raw, False).decode('utf-8')
    except (ValueError, UnicodeError) as exc:
        raise DPAPIError('Invalid encrypted configuration') from exc

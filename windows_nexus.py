"""Request the configured Nexus key through Windows SendInput.

R is the library default; CTRL is left Control, RCTRL is right Control. The
host uses the user's configured game binding. This actuator
neither reads the game nor constructs an ESCAPE/RECONNECT packet. A successful
key press does not confirm that a character reached the Nexus.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import os
import string
import time
from typing import Callable, Protocol


ULONG_PTR = ctypes.c_size_t
_NEXUS_VIRTUAL_KEYS = {letter: ord(letter) for letter in string.ascii_uppercase}
_NEXUS_VIRTUAL_KEYS.update(CTRL=0xA2, RCTRL=0xA3)  # VK_LCONTROL / VK_RCONTROL
_NEXUS_KEY_ALIASES = {"CONTROL": "CTRL", "LCTRL": "CTRL"}


def normalize_nexus_key(key: object) -> str:
    """Own the supported binding names for configuration and input delivery."""
    if not isinstance(key, str) or not key.isascii():
        raise ValueError("nexus_key must be an ASCII letter, CTRL or RCTRL")
    canonical = key.upper()
    canonical = _NEXUS_KEY_ALIASES.get(canonical, canonical)
    if canonical not in _NEXUS_VIRTUAL_KEYS:
        raise ValueError("nexus_key must be an ASCII letter, CTRL or RCTRL")
    return canonical


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


class NexusInputError(RuntimeError):
    """The input request failed; no game outcome is established."""


class NexusInputBlocked(NexusInputError):
    """The selected process is unavailable or does not own the foreground."""


@dataclass(frozen=True)
class NexusRequestResult:
    expected_pid: int
    key: str
    key_sent: bool = field(default=True, init=False)
    nexus_confirmed: bool = field(default=False, init=False)


class InputBackend(Protocol):
    def open_process(self, pid: int) -> object: ...
    def is_process_running(self, handle: object) -> bool: ...
    def foreground_process_id(self) -> int: ...
    def send_key(self, virtual_key: int, *, key_up: bool) -> None: ...
    def close_process(self, handle: object) -> None: ...


class Win32InputBackend:
    """Windows API boundary. Constructing it does not send input."""

    def __init__(self):
        if os.name != "nt":
            raise NexusInputError("SendInput requires Windows")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.SendInput.argtypes = [
            wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self._user32.MapVirtualKeyW.restype = wintypes.UINT
        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _failure(operation: str) -> NexusInputError:
        code = ctypes.get_last_error()
        return NexusInputError(f"{operation} failed (Windows error {code})")

    def open_process(self, pid: int) -> object:
        # Keep this exact process object alive while checking its foreground.
        # SYNCHRONIZE suffices for a zero-time wait; no memory/write rights.
        handle = self._kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            raise self._failure("OpenProcess")
        return handle

    def is_process_running(self, handle: object) -> bool:
        result = self._kernel32.WaitForSingleObject(handle, 0)
        if result == 0x00000102:  # WAIT_TIMEOUT: process has not exited
            return True
        if result == 0:  # WAIT_OBJECT_0: process has exited
            return False
        raise self._failure("WaitForSingleObject")

    def foreground_process_id(self) -> int:
        window = self._user32.GetForegroundWindow()
        if not window:
            return 0
        pid = wintypes.DWORD()
        if not self._user32.GetWindowThreadProcessId(window, ctypes.byref(pid)):
            raise self._failure("GetWindowThreadProcessId")
        return pid.value

    def send_key(self, virtual_key: int, *, key_up: bool) -> None:
        event = INPUT(type=1)  # INPUT_KEYBOARD
        flags = 0x0002 if key_up else 0  # KEYEVENTF_KEYUP
        if virtual_key in (_NEXUS_VIRTUAL_KEYS["CTRL"], _NEXUS_VIRTUAL_KEYS["RCTRL"]):
            # Use the documented physical-key path for left/right Control.
            # MAPVK_VK_TO_VSC_EX preserves the E0 prefix of right Control;
            # KEYEVENTF_SCANCODE ignores wVk. This is not a claim of a tested
            # Unity outcome: the resulting game action still needs confirmation.
            # https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-mapvirtualkeyw
            # https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-keybdinput
            scan = self._user32.MapVirtualKeyW(virtual_key, 4)
            if scan == 0:
                raise NexusInputError("MapVirtualKeyW could not map the configured Control key")
            prefix = scan >> 8
            if prefix not in (0, 0xE0):
                raise NexusInputError("MapVirtualKeyW returned an unsupported Control scan code")
            flags |= 0x0008  # KEYEVENTF_SCANCODE
            if prefix == 0xE0:
                flags |= 0x0001  # KEYEVENTF_EXTENDEDKEY
            event.ki = KEYBDINPUT(wVk=0, wScan=scan & 0xFF, dwFlags=flags)
        else:
            event.ki = KEYBDINPUT(wVk=virtual_key, dwFlags=flags)
        ctypes.set_last_error(0)
        if self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise self._failure("SendInput key-up" if key_up else "SendInput key-down")

    def close_process(self, handle: object) -> None:
        if not self._kernel32.CloseHandle(handle):
            raise self._failure("CloseHandle")


class WindowsNexus:
    def __init__(self, backend: InputBackend | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self._backend = backend if backend is not None else Win32InputBackend()
        self._sleep = sleeper

    def request(self, expected_pid: int, key: str = "R") -> NexusRequestResult:
        """Hold one configured letter or Control key for 50 ms after checks.

        The caller chooses the game PID and must verify its Nexus binding.
        The result acknowledges input delivery only. Do not retry based on
        this acknowledgement as though it confirmed a map change.
        """
        if (isinstance(expected_pid, bool) or not isinstance(expected_pid, int)
                or not 0 < expected_pid <= 0xFFFFFFFF):
            raise ValueError("expected_pid must be a positive Windows process ID")
        key = normalize_nexus_key(key)
        virtual_key = _NEXUS_VIRTUAL_KEYS[key]
        handle = self._backend.open_process(expected_pid)
        request_error = None
        try:
            if not self._backend.is_process_running(handle):
                raise NexusInputBlocked("The selected game process has exited")
            if self._backend.foreground_process_id() != expected_pid:
                raise NexusInputBlocked("The selected game does not own the foreground window")
            # Recheck after reading the window, using the same process handle.
            if not self._backend.is_process_running(handle):
                raise NexusInputBlocked("The selected game process has exited")
            try:
                self._backend.send_key(virtual_key, key_up=False)
                self._sleep(0.05)
            except BaseException as exc:
                request_error = exc
                raise
            finally:
                # Release even if SendInput or the hold is interrupted.
                try:
                    self._backend.send_key(virtual_key, key_up=True)
                except Exception as exc:
                    if request_error is None:
                        raise
                    request_error.add_note(f"The key-up attempt also failed: {exc}")
            return NexusRequestResult(expected_pid, key)
        except BaseException as exc:
            request_error = exc
            raise
        finally:
            try:
                self._backend.close_process(handle)
            except Exception as exc:
                if request_error is None:
                    raise
                request_error.add_note(f"Closing the process handle also failed: {exc}")

"""No test calls native SendInput; Win32 payloads use a fake user32 boundary."""

import ctypes
from types import SimpleNamespace
import unittest

from windows_nexus import (
    INPUT, KEYBDINPUT, MOUSEINPUT, NexusInputBlocked, NexusInputError, WindowsNexus,
    Win32InputBackend, normalize_nexus_key,
)


class FakeBackend:
    def __init__(self, *, pid=1234, running=True, fail_down=False, fail_up=False):
        self.pid = pid
        self.running = running
        self.fail_down = fail_down
        self.fail_up = fail_up
        self.events = []

    def open_process(self, pid):
        self.events.append(("open", pid))
        return "held-process"

    def is_process_running(self, handle):
        self.events.append(("running", handle))
        return self.running

    def foreground_process_id(self):
        self.events.append(("foreground", self.pid))
        return self.pid

    def send_key(self, virtual_key, *, key_up):
        self.events.append(("up" if key_up else "down", virtual_key))
        if self.fail_up if key_up else self.fail_down:
            raise NexusInputError("fake SendInput failure")

    def close_process(self, handle):
        self.events.append(("close", handle))


class WindowsNexusTests(unittest.TestCase):
    def test_full_win32_input_layout_on_windows_x64(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_long) != 4:
            self.skipTest("Windows x64 layout assertion")
        self.assertEqual(ctypes.sizeof(KEYBDINPUT), 24)
        self.assertEqual(ctypes.sizeof(MOUSEINPUT), 32)
        self.assertEqual(ctypes.sizeof(INPUT), 40)
        self.assertEqual(INPUT.data.offset, 8)

    def test_other_foreground_app_blocks_without_sending_keys(self):
        backend = FakeBackend(pid=5678)
        with self.assertRaises(NexusInputBlocked):
            WindowsNexus(backend, lambda _: None).request(1234)
        self.assertFalse(any(e[0] in ("up", "down") for e in backend.events))
        self.assertEqual(backend.events[-1], ("close", "held-process"))

    def test_exited_process_blocks_without_sending_keys(self):
        backend = FakeBackend(running=False)
        with self.assertRaises(NexusInputBlocked):
            WindowsNexus(backend, lambda _: None).request(1234)
        self.assertFalse(any(e[0] in ("up", "down") for e in backend.events))
        self.assertEqual(backend.events[-1][0], "close")

    def test_success_holds_configured_key_but_does_not_confirm_nexus(self):
        backend = FakeBackend()
        holds = []
        result = WindowsNexus(backend, holds.append).request(1234, "n")
        self.assertEqual(holds, [0.05])
        self.assertEqual([e for e in backend.events if e[0] in ("up", "down")],
                         [("down", ord("N")), ("up", ord("N"))])
        self.assertEqual((result.expected_pid, result.key), (1234, "N"))
        self.assertTrue(result.key_sent)
        self.assertFalse(result.nexus_confirmed)
        self.assertEqual(backend.events[-1][0], "close")

    def test_control_binding_aliases_use_the_correct_virtual_key_down_and_up(self):
        for supplied, canonical, virtual_key in (
                ("CTRL", "CTRL", 0xA2), ("control", "CTRL", 0xA2),
                ("lctrl", "CTRL", 0xA2), ("rctrl", "RCTRL", 0xA3)):
            with self.subTest(key=supplied):
                backend = FakeBackend()
                result = WindowsNexus(backend, lambda _: None).request(1234, supplied)
                self.assertEqual(result.key, canonical)
                self.assertFalse(result.nexus_confirmed)
                self.assertEqual([e for e in backend.events if e[0] in ("up", "down")],
                                 [("down", virtual_key), ("up", virtual_key)])

    def test_native_control_payloads_use_physical_scan_codes_without_real_input(self):
        if not hasattr(ctypes, "set_last_error"):
            self.skipTest("Windows ctypes last-error API")
        calls = []
        maps = []

        def fake_map(virtual_key, mapping):
            maps.append((virtual_key, mapping))
            return 0xE01D if virtual_key == 0xA3 else 0x1D

        def fake_send(count, address, size):
            event = ctypes.cast(address, ctypes.POINTER(INPUT)).contents
            calls.append((count, size, event.type, event.ki.wVk,
                          event.ki.wScan, event.ki.dwFlags))
            return 1

        # Bypass __init__: user32 is entirely fake, so no native SendInput is bound.
        backend = object.__new__(Win32InputBackend)
        backend._user32 = SimpleNamespace(MapVirtualKeyW=fake_map, SendInput=fake_send)
        for virtual_key in (0xA2, 0xA3):
            backend.send_key(virtual_key, key_up=False)
            backend.send_key(virtual_key, key_up=True)
        self.assertEqual(maps, [(0xA2, 4), (0xA2, 4), (0xA3, 4), (0xA3, 4)])
        self.assertEqual(calls, [(1, ctypes.sizeof(INPUT), 1, 0, 0x1D, flags)
                                 for flags in (0x8, 0xA, 0x9, 0xB)])
        # Existing letter delivery continues to use its virtual-key identity.
        backend.send_key(ord("R"), key_up=False)
        self.assertEqual(calls[-1], (1, ctypes.sizeof(INPUT), 1, ord("R"), 0, 0))
        self.assertEqual(len(maps), 4)

    def test_failed_native_control_mapping_does_not_call_sendinput(self):
        if not hasattr(ctypes, "get_last_error"):
            self.skipTest("Windows ctypes last-error API")
        calls = []
        backend = object.__new__(Win32InputBackend)
        backend._user32 = SimpleNamespace(MapVirtualKeyW=lambda *_: 0,
                                          SendInput=lambda *args: calls.append(args))
        with self.assertRaisesRegex(NexusInputError, "MapVirtualKeyW"):
            backend.send_key(0xA2, key_up=False)
        self.assertEqual(calls, [])

    def test_down_failure_propagates_and_still_releases_key(self):
        backend = FakeBackend(fail_down=True)
        with self.assertRaisesRegex(NexusInputError, "fake SendInput"):
            WindowsNexus(backend, lambda _: None).request(1234)
        self.assertEqual([e[0] for e in backend.events][-3:], ["down", "up", "close"])

    def test_interrupted_hold_releases_key_and_propagates(self):
        backend = FakeBackend()
        def interrupted(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            WindowsNexus(backend, interrupted).request(1234)
        self.assertEqual([e[0] for e in backend.events][-3:], ["down", "up", "close"])

    def test_up_failure_propagates_without_success_acknowledgement(self):
        backend = FakeBackend(fail_up=True)
        with self.assertRaises(NexusInputError):
            WindowsNexus(backend, lambda _: None).request(1234)
        self.assertEqual(backend.events[-1][0], "close")

    def test_invalid_configuration_fails_before_backend_calls(self):
        backend = FakeBackend()
        actuator = WindowsNexus(backend, lambda _: None)
        for pid in (None, True, 0, -1, 2**32):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                actuator.request(pid)
        for key in (None, True, "", "RR", "ñ", "1", "SHIFT", "ALT", "CTRL+R", " CTRL"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                actuator.request(1234, key)
        self.assertEqual(backend.events, [])
        for key in (None, True, "ſ", "SHIFT", "CTRL+R"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_nexus_key(key)


if __name__ == "__main__":
    unittest.main()

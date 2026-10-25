import unittest

from debuffs_settings import (
    DebuffSettings, QUIET_SUPPORTED, QUIET_UNSUPPORTED_REASON,
    debuffs_mapping, parse_debuffs,
)


class DebuffSettingsTests(unittest.TestCase):
    def test_defaults_leave_module_off_and_quiet_unavailable(self):
        self.assertEqual(parse_debuffs(), DebuffSettings(False, True, False))
        self.assertEqual(parse_debuffs({}), DebuffSettings(False, True, False))
        self.assertFalse(QUIET_SUPPORTED)
        with self.assertRaisesRegex(ValueError, "AntiQuiet no disponible"):
            parse_debuffs({"quiet": True})
        self.assertIn("maná", QUIET_UNSUPPORTED_REASON)

    def test_each_switch_is_independent_and_round_trips(self):
        for enabled in (False, True):
            for confuse in (False, True):
                settings = DebuffSettings(enabled, confuse, False)
                self.assertEqual(parse_debuffs(debuffs_mapping(settings)), settings)
                self.assertEqual(settings.has_protection, enabled and confuse)

    def test_rejects_non_object_and_non_boolean_switches(self):
        for value in (False, [], "", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_debuffs(value)
        for field in ("enabled", "confuse", "quiet"):
            for value in (0, 1, "true", None, []):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_debuffs({field: value})


if __name__ == "__main__":
    unittest.main()

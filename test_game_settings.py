import unittest

from game_settings import parse_game_bindings, unity_key_name
from autoaim_settings import parse_autoaim
from live_autonexus import config_from_mapping, config_to_mapping, resolve_game_nexus_key


class BindingTests(unittest.TestCase):
    def test_saved_unity_bindings_keep_their_source_and_missing_stays_unknown(self):
        bindings = parse_game_bindings({"Key.EscapeToNexus_h1780616307": 306,
                                       "unrelated": "not retained"})
        self.assertEqual(bindings["nexus"].key, "CTRL")
        self.assertEqual(bindings["nexus"].unity_code, 306)
        self.assertIn("EscapeToNexus_h1780616307", bindings["nexus"].source)
        self.assertIsNone(bindings["autofire"].key)
        self.assertEqual(unity_key_name(105), "I")

    def test_ambiguous_or_malformed_saved_binding_is_not_guessed(self):
        bindings = parse_game_bindings({"Key.AutoFire_h1": 105, "Key.AutoFire_h2": 106,
                                       "Key.EscapeToNexus_h1": True})
        self.assertIsNone(bindings["autofire"].key)
        self.assertIsNone(bindings["nexus"].key)
        self.assertIsNone(bindings["nexus"].unity_code)

    def test_aim_config_rejects_nonfinite_and_unsupported_semantics(self):
        self.assertFalse(parse_autoaim().enabled)
        self.assertEqual(parse_autoaim({"enabled": True, "extrapolation": 0}).extrapolation, 0)
        for invalid in ({"enabled": 1}, {"extrapolation": float("nan")},
                        {"extrapolation": 10**400}, {"mouse_radius": 0},
                        {"mode": "nearest"}, {"activation": "hold"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_autoaim(invalid)

    def test_target_filters_default_off_and_round_trip_independent_explicit_booleans(self):
        self.assertFalse(parse_autoaim().prioritize_bosses)
        self.assertFalse(parse_autoaim().ignore_walls)
        for bosses in (False, True):
            for walls in (False, True):
                with self.subTest(bosses=bosses, walls=walls):
                    config = config_from_mapping({"autoaim": {"prioritize_bosses": bosses,
                                                               "ignore_walls": walls}})
                    self.assertIs(config.autoaim.prioritize_bosses, bosses)
                    self.assertIs(config.autoaim.ignore_walls, walls)
                    mapping = config_to_mapping(config)
                    self.assertIs(mapping["autoaim"]["prioritize_bosses"], bosses)
                    self.assertIs(mapping["autoaim"]["ignore_walls"], walls)
                    self.assertEqual(config_from_mapping(mapping), config)
        for field in ("prioritize_bosses", "ignore_walls"):
            for invalid in (0, 1, None, "false", [], {}):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    parse_autoaim({field: invalid})

    def test_runtime_auto_key_waits_for_current_supported_binding_and_keeps_manual_explicit(self):
        config = config_from_mapping({"detect_game_keys": True, "autoaim": {"enabled": True}})
        self.assertEqual(config_from_mapping(config_to_mapping(config)), config)
        self.assertEqual(resolve_game_nexus_key(None, config), (None, "waiting"))
        payload = {"game_bindings": {"nexus": {"unity_code": 306, "age_ms": 10}}}
        self.assertEqual(resolve_game_nexus_key(payload, config), ("CTRL", "detected"))
        payload["game_bindings"]["nexus"]["age_ms"] = 251
        self.assertEqual(resolve_game_nexus_key(payload, config), (None, "waiting"))
        payload["game_bindings"]["nexus"].update(age_ms=0, unity_code=308)
        self.assertEqual(resolve_game_nexus_key(payload, config), (None, "unsupported"))
        manual = config_from_mapping({"nexus_key": "R", "detect_game_keys": False})
        self.assertEqual(resolve_game_nexus_key(None, manual), ("R", "manual"))

import unittest

from autonexus_core import Action, AutoNexus, INT32_MAX, INT32_MIN


class AutoNexusTests(unittest.TestCase):
    def ready(self, threshold=0.30, player_hp=1000, tracked_hp=None, max_hp=1000):
        engine = AutoNexus(threshold)
        engine.new_map(excluded=False)
        engine.set_gates(False, False)
        engine.update_health(player_hp, max_hp, tracked_hp)
        return engine

    def test_strict_integer_limit_and_decision_snapshot(self):
        engine = self.ready(player_hp=300)
        decision = engine.evaluate()
        self.assertEqual(decision.action, Action.NONE)
        self.assertEqual(decision.limit, 300)
        engine.update_health(299, 1000)
        decision = engine.evaluate()
        self.assertEqual(decision.action, Action.ESCAPE)
        self.assertEqual((decision.player_hp, decision.tracked_hp, decision.max_hp),
                         (299, 299, 1000))
        self.assertIsNone(decision.cause)
        self.assertIn("requested", decision.reason)

    def test_float32_storage_before_double_product_and_truncation(self):
        engine = self.ready(threshold=0.258, player_hp=257)
        self.assertEqual(engine.threshold, 0.257999986410141)
        self.assertEqual(engine.evaluate().limit, 257)
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.update_health(256, 1000)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)
        # A percent comparison or float32 product would wrongly give limit 258.
        tiny = self.ready(threshold=0.01, player_hp=0, max_hp=100)
        self.assertEqual(tiny.evaluate().limit, 0)
        self.assertEqual(tiny.evaluate().action, Action.NONE)

    def test_live_threshold_changes_validate_and_preserve_this_maps_request(self):
        engine = self.ready(threshold=0.3, player_hp=250)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)
        engine.threshold = 0.8
        self.assertEqual(engine.evaluate().action, Action.NONE)
        previous = engine.threshold
        with self.assertRaises(ValueError):
            engine.threshold = 1.0
        self.assertEqual(engine.threshold, previous)
        engine.new_map()
        engine.set_gates(False, False)
        engine.update_health(250, 1000)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)

    def test_either_health_source_can_trigger(self):
        for player_hp, tracked_hp in ((900, 299), (299, 900)):
            with self.subTest(player_hp=player_hp, tracked_hp=tracked_hp):
                decision = self.ready(player_hp=player_hp, tracked_hp=tracked_hp).evaluate()
                self.assertEqual(decision.action, Action.ESCAPE)

    def test_damage_subtracts_before_check_and_does_not_clamp(self):
        engine = self.ready(player_hp=360)
        decision = engine.apply_damage(160, "projectile")
        self.assertEqual(decision.action, Action.ESCAPE)
        self.assertEqual(decision.player_hp, 360)
        self.assertEqual(decision.tracked_hp, 200)
        self.assertEqual(decision.cause, "projectile")
        engine = self.ready(player_hp=100)
        self.assertEqual(engine.apply_damage(150).tracked_hp, -50)
        engine = self.ready(player_hp=INT32_MIN, tracked_hp=INT32_MIN)
        self.assertEqual(engine.apply_damage(1).tracked_hp, INT32_MAX)

    def test_gates_map_exclusion_and_enabled_block_without_consuming_latch(self):
        engine = self.ready(player_hp=10)
        for gate_a, gate_b in ((True, False), (False, True), (True, True), (None, False)):
            engine.set_gates(gate_a, gate_b)
            self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.set_gates(False, False)
        engine.enabled = False
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.enabled = True
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)
        engine.new_map(excluded=True)
        engine.set_gates(False, False)
        engine.update_health(10, 1000)
        self.assertEqual(engine.evaluate().action, Action.NONE)

    def test_unknown_state_blocks_without_fabricating_health(self):
        engine = AutoNexus()
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.update_health(10, 1000)
        engine.set_gates(False, False)
        self.assertEqual(engine.evaluate().action, Action.NONE)  # no known map
        engine.new_map()
        engine.update_health(10, 1000)
        self.assertEqual(engine.evaluate().action, Action.NONE)  # no known gates
        engine.set_gates(False, False)
        for player_hp, max_hp, tracked_hp in ((None, 1000, 10), (10, None, 10)):
            engine.update_health(player_hp, max_hp, tracked_hp)
            self.assertEqual(engine.apply_damage(1).action, Action.NONE)
        engine.update_health(10, 1000)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)

    def test_one_request_per_map_recovery_does_not_rearm(self):
        engine = self.ready(player_hp=299)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)
        engine.update_health(900, 1000)
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.update_health(200, 1000)
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.new_map()
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine.set_gates(False, False)
        self.assertEqual(engine.evaluate().action, Action.NONE)  # HP invalidated
        engine.update_health(200, 1000)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)

    def test_authoritative_health_update_synchronizes_tracked_hp(self):
        engine = self.ready(player_hp=900)
        self.assertEqual(engine.apply_damage(100).tracked_hp, 800)
        engine.update_health(850, 1000)
        decision = engine.evaluate()
        self.assertEqual(decision.tracked_hp, 850)
        self.assertEqual(decision.player_hp, 850)

    def test_tick_checks_before_and_after_sync_and_returns_first_action(self):
        engine = self.ready(player_hp=900, tracked_hp=200)
        before = engine.on_tick_health(900, 1000)
        self.assertEqual(before.action, Action.ESCAPE)
        self.assertEqual(before.tracked_hp, 200)
        self.assertEqual(before.cause, "NT")
        self.assertEqual(engine.evaluate().tracked_hp, 900)
        self.assertEqual(engine.evaluate().action, Action.NONE)
        engine = self.ready(player_hp=900)
        after = engine.on_tick_health(200, 1000)
        self.assertEqual(after.action, Action.ESCAPE)
        self.assertEqual(after.tracked_hp, 200)

    def test_threshold_boundary_validated_before_and_after_float32(self):
        for threshold in (0, 1, -0.1, float("nan"), float("inf"), True, None,
                          10**400, 1e-50, 1 - 1e-10):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    AutoNexus(threshold)
        for threshold in (0.001, 0.999):
            with self.subTest(threshold=threshold):
                self.assertTrue(0 < AutoNexus(threshold).threshold < 1)

    def test_invalid_input_is_transactional_and_int32_values_are_preserved(self):
        engine = self.ready(player_hp=300)
        for value in (True, 20.0, "20", INT32_MIN - 1, INT32_MAX + 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    engine.update_health(value, 1000)
                with self.assertRaises(ValueError):
                    engine.apply_damage(value)
        for maximum in (0, -1, True, 1000.0, INT32_MAX + 1):
            with self.assertRaises(ValueError):
                engine.update_health(20, maximum)
        with self.assertRaises(ValueError):
            engine.apply_damage(-1)
        with self.assertRaises(ValueError):
            engine.set_gates(False, 0)
        with self.assertRaises(ValueError):
            engine.enabled = 1
        with self.assertRaises(ValueError):
            engine.new_map(excluded="false")
        with self.assertRaises(ValueError):
            engine.on_tick_health(200, 0)
        self.assertEqual(engine.evaluate().player_hp, 300)
        self.assertEqual(engine.evaluate().action, Action.NONE)
        # Invalid incoming NT data must not consume a pending pre-sync action.
        engine.update_health(200, 1000)
        with self.assertRaises(ValueError):
            engine.on_tick_health(True, 1000)
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)
        high = self.ready(player_hp=INT32_MAX, max_hp=INT32_MAX)
        self.assertEqual(high.evaluate().player_hp, INT32_MAX)

    def test_state_filters_are_explicit_and_do_not_replace_unknown_observations(self):
        engine = AutoNexus()
        self.assertTrue(engine.use_state_filters)
        engine.new_map(excluded=False)
        engine.update_health(200, 1000)
        blocked = engine.evaluate()
        self.assertEqual(blocked.action, Action.NONE)
        self.assertEqual(blocked.player_hp, 200)
        self.assertIn("gates are unknown", blocked.reason)
        engine.use_state_filters = False
        self.assertEqual(engine.evaluate().action, Action.ESCAPE)
        # Omitting filters never fabricates False gates or clears a request.
        engine.use_state_filters = True
        self.assertIn("gates are unknown", engine.evaluate().reason)
        engine.use_state_filters = False
        self.assertIn("already requested", engine.evaluate().reason)

    def test_state_filter_policy_rejects_non_boolean_values(self):
        engine = AutoNexus()
        for value in (None, 0, 1, "false"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    AutoNexus(use_state_filters=value)
                with self.assertRaises(ValueError):
                    engine.use_state_filters = value
                self.assertIs(engine.use_state_filters, True)


if __name__ == "__main__":
    unittest.main()

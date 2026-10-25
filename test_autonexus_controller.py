import unittest

from autonexus_controller import Controller, HealthFrame
from autonexus_core import Action, AutoNexus


def health_frame(*, map_id="realm:epoch-1", map_name="Realm", player_hp=900,
                 max_hp=1000, gate_a=False, gate_b=False, tracked_hp=None):
    return HealthFrame(map_id, map_name, player_hp, max_hp,
                       gate_a, gate_b, tracked_hp)


class ControllerTests(unittest.TestCase):
    def test_distinct_map_load_identity_rearms_even_for_the_same_map_name(self):
        controller = Controller(AutoNexus())
        self.assertEqual(controller.consume(health_frame(player_hp=299)).action,
                         Action.ESCAPE)
        self.assertEqual(controller.consume(health_frame(player_hp=299)).action,
                         Action.NONE)
        self.assertEqual(controller.consume(health_frame(
            map_id="realm:epoch-2", player_hp=299)).action, Action.ESCAPE)

    def test_unknown_map_invalidates_observations_without_rearming(self):
        controller = Controller(AutoNexus())
        self.assertEqual(controller.consume(health_frame(player_hp=299)).action,
                         Action.ESCAPE)
        unknown = controller.consume(health_frame(map_id=None, player_hp=1))
        self.assertEqual(unknown.action, Action.NONE)
        self.assertIsNone(unknown.player_hp)
        self.assertIsNone(unknown.tracked_hp)
        decision = controller.consume(health_frame(player_hp=1))
        self.assertEqual(decision.action, Action.NONE)
        self.assertIn("already requested", decision.reason)

    def test_unknown_health_never_runs_the_old_pre_tick_check(self):
        for unknown_field in ("player_hp", "max_hp"):
            with self.subTest(field=unknown_field):
                engine = AutoNexus()
                controller = Controller(engine)
                controller.consume(health_frame())
                # A pending modeled low value must not override unknown data.
                engine.update_health(900, 1000, tracked_hp=250)
                decision = controller.consume(health_frame(**{unknown_field: None}))
                self.assertEqual(decision.action, Action.NONE)
                self.assertIsNone(decision.player_hp)
                self.assertIsNone(decision.tracked_hp)
                # The unknown frame did not consume or rearm the request latch.
                self.assertEqual(controller.consume(health_frame(player_hp=250)).action,
                                 Action.ESCAPE)

    def test_unknown_gates_retain_health_and_follow_the_explicit_core_policy(self):
        frame = health_frame(player_hp=250, gate_a=None, gate_b=None)
        for filters, expected in ((True, Action.NONE), (False, Action.ESCAPE)):
            with self.subTest(use_state_filters=filters):
                engine = AutoNexus(use_state_filters=filters)
                controller = Controller(engine)
                decision = controller.consume(frame)
                self.assertEqual(decision.action, expected)
                self.assertEqual((decision.player_hp, decision.tracked_hp, decision.max_hp),
                                 (250, 250, 1000))
                self.assertEqual(controller.consume(frame).action, Action.NONE)
                # With filters active this remains blocked; HP-only mode rearms
                # for an actual new identity and still requests just once.
                next_decision = controller.consume(health_frame(
                    map_id="realm:epoch-2", player_hp=250, gate_a=None, gate_b=None))
                self.assertEqual(next_decision.action, expected)
                engine.use_state_filters = True
                self.assertIn("gates are unknown", engine.evaluate().reason)

    def test_omitting_state_filters_does_not_allow_unknown_map_or_health(self):
        for missing in ({"map_id": None}, {"map_name": None}, {"player_hp": None},
                        {"max_hp": None}):
            with self.subTest(missing=missing):
                controller = Controller(AutoNexus(use_state_filters=False))
                decision = controller.consume(health_frame(
                    player_hp=250, gate_a=None, gate_b=None, **missing)
                    if "player_hp" not in missing else health_frame(
                        gate_a=None, gate_b=None, **missing))
                self.assertEqual(decision.action, Action.NONE)

    def test_invalid_new_map_frame_leaves_previous_map_latch_and_state_intact(self):
        for invalid in ({"max_hp": 0}, {"player_hp": True}, {"gate_b": 1},
                        {"tracked_hp": 2**31}, {"map_name": ""}):
            with self.subTest(invalid=invalid):
                engine = AutoNexus()
                controller = Controller(engine)
                controller.consume(health_frame(player_hp=299))
                frame = health_frame(map_id="realm:epoch-2", **invalid)
                with self.assertRaises(ValueError):
                    controller.consume(frame)
                unchanged = engine.evaluate()
                self.assertEqual(unchanged.player_hp, 299)
                self.assertIn("already requested", unchanged.reason)
                self.assertEqual(controller.consume(health_frame(player_hp=1)).action,
                                 Action.NONE)
                self.assertEqual(controller.consume(health_frame(
                    map_id="realm:epoch-2", player_hp=1)).action, Action.ESCAPE)

    def test_low_tracked_health_is_checked_before_authoritative_tick_sync(self):
        engine = AutoNexus()
        controller = Controller(engine)
        controller.consume(health_frame())
        engine.update_health(900, 1000, tracked_hp=250)
        decision = controller.consume(health_frame(player_hp=900))
        self.assertEqual(decision.action, Action.ESCAPE)
        self.assertEqual(decision.cause, "NT")
        self.assertEqual(decision.tracked_hp, 250)
        self.assertEqual(engine.evaluate().tracked_hp, 900)

    def test_same_map_name_correction_changes_exclusion_without_rearming(self):
        controller = Controller(AutoNexus())
        self.assertEqual(controller.consume(health_frame(
            map_name=None, player_hp=1)).action, Action.NONE)
        self.assertEqual(controller.consume(health_frame(
            map_name="Nexus", player_hp=1)).action, Action.NONE)
        self.assertEqual(controller.consume(health_frame(player_hp=1)).action,
                         Action.ESCAPE)
        controller.consume(health_frame(map_name="Nexus", player_hp=1))
        decision = controller.consume(health_frame(map_name="Realm", player_hp=1))
        self.assertEqual(decision.action, Action.NONE)
        self.assertIn("already requested", decision.reason)

    def test_nexus_exclusion_is_configurable(self):
        controller = Controller(AutoNexus(), excluded_maps=("Vault",))
        self.assertEqual(controller.consume(health_frame(
            map_name="Nexus", player_hp=1)).action, Action.ESCAPE)
        with self.assertRaises(ValueError):
            Controller(AutoNexus(), excluded_maps="Nexus")


if __name__ == "__main__":
    unittest.main()

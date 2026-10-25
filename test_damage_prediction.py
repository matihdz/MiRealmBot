from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from autonexus_controller import Controller, HealthFrame, validate_frame
from autonexus_core import Action, AutoNexus
from live_autonexus import TelemetryConsumer, load_config


def frame(hp=100, map_id="epoch:1", **changes):
    result = asdict(HealthFrame(map_id, "Vault", hp, 100, False, False))
    result.update(changes)
    return result


def event(event_id, amount=0, *, kind="damage", age_ms=0, observed=None, **changes):
    result = {"event_id": event_id, "kind": kind, "age_ms": age_ms,
              "frame": frame() if observed is None else observed}
    if kind == "damage":
        result.update(amount=amount, cause="projectile")
    result.update(changes)
    return result


def poll(sequence, *, observed=None, events=None, age_ms=0, **changes):
    result = dict(frame() if observed is None else observed,
                  sequence=sequence, age_ms=age_ms, events=[] if events is None else events,
                  events_lost=False)
    result.update(changes)
    return result


def consumer(enabled=True, *, use_state_filters=False):
    return TelemetryConsumer(Controller(AutoNexus(use_state_filters=use_state_filters)),
                             prediction_enabled=enabled)


class AcceptedHitPredictionTests(unittest.TestCase):
    def test_accumulates_across_unchanged_samples_and_keeps_first_action(self):
        subject = consumer()
        _, first = subject.consume(poll(1, events=[event(1, 40)]))
        self.assertEqual((first.action, first.player_hp, first.tracked_hp),
                         (Action.NONE, 100, 60))
        _, unchanged = subject.consume(poll(2))
        self.assertEqual(unchanged.tracked_hp, 60)
        _, crossing = subject.consume(poll(3, events=[event(2, 31), event(3, 1)]))
        self.assertEqual((crossing.action, crossing.tracked_hp, crossing.cause),
                         (Action.ESCAPE, 29, "projectile"))
        self.assertEqual(subject.prediction.state()["tracked_hp"], 28)
        _, latched = subject.consume(poll(4, events=[event(4, 90)]))
        self.assertEqual(latched.action, Action.NONE)

    def test_explicit_health_sync_same_hp_discards_pending_damage(self):
        subject = consumer()
        subject.consume(poll(1, events=[event(1, 60)]))
        _, decision = subject.consume(poll(2, events=[event(2, kind="health_sync"),
                                                      event(3, 20)]))
        self.assertEqual((decision.action, decision.tracked_hp), (Action.NONE, 80))

    def test_applied_hp_change_and_health_sync_do_not_double_subtract(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                subject = consumer()
                events = [event(1, 20)]
                if explicit:
                    events.append(event(2, kind="health_sync", observed=frame(80)))
                _, synced = subject.consume(poll(1, observed=frame(80), events=events))
                self.assertEqual(synced.tracked_hp, 80)
                next_id = 3 if explicit else 2
                _, next_hit = subject.consume(poll(2, observed=frame(80),
                                                  events=[event(next_id, 10, observed=frame(80))]))
                self.assertEqual((next_hit.player_hp, next_hit.tracked_hp), (80, 70))

    def test_stale_event_and_stale_or_missing_sample_invalidate_prediction(self):
        subject = consumer()
        subject.consume(poll(1, events=[event(1, 40)]))
        _, stale_event = subject.consume(poll(2, events=[event(2, 60, age_ms=251)]))
        self.assertEqual((stale_event.action, stale_event.tracked_hp), (Action.NONE, 100))
        _, stale_poll = subject.consume(poll(3, age_ms=251, events=[event(3, 99)]))
        self.assertEqual(stale_poll.action, Action.NONE)
        self.assertIsNone(stale_poll.tracked_hp)
        self.assertEqual(subject.prediction.last_event_id, 3)
        subject.consume(None)
        _, resumed = subject.consume(poll(4))
        self.assertEqual(resumed.tracked_hp, 100)

    def test_duplicate_poll_preserves_prediction_but_duplicate_events_are_an_error(self):
        subject = consumer()
        subject.consume(poll(1, events=[event(1, 40)]))
        sample, decision = subject.consume(poll(1))
        self.assertEqual(sample.status, "duplicate")
        self.assertIsNone(decision)
        self.assertEqual(subject.prediction.state()["tracked_hp"], 60)
        with self.assertRaisesRegex(ValueError, "repetida"):
            subject.consume(poll(2, events=[event(1, 99)]))
        self.assertIsNone(subject.controller.engine.evaluate().tracked_hp)
        self.assertEqual(subject.last_sequence, 1)

    def test_events_cannot_arrive_on_an_unchanged_poll_sequence(self):
        subject = consumer()
        subject.consume(poll(1))
        with self.assertRaisesRegex(ValueError, "secuencia de frame nueva"):
            subject.consume(poll(1, events=[event(1, 99)]))
        self.assertEqual(subject.prediction.last_event_id, 0)

    def test_entire_batch_validates_before_any_action_or_event_state_is_consumed(self):
        malformed = [event(2, True), event(2, -1), event(2, 2**31),
                     event(2, 1, observed=frame(hp=True)),
                     event(2, 1, observed=frame(map_id="")),
                     event(2, 1, observed=frame(tracked_hp=90)),
                     event(2, 1, cause="aoe"), event(2, 1, age_ms=float("nan")),
                     event(2, 1, age_ms=True), dict(event(2, kind="health_sync"), amount=1)]
        for bad in malformed:
            with self.subTest(bad=bad):
                subject = consumer()
                subject.consume(poll(1))
                with self.assertRaises(ValueError):
                    subject.consume(poll(2, events=[event(1, 99), bad]))
                self.assertEqual(subject.prediction.last_event_id, 0)
                self.assertEqual(subject.last_sequence, 1)
                self.assertIsNone(subject.controller.engine.evaluate().tracked_hp)
                # A malformed batch did not consume the one-shot action latch.
                _, low_hp = subject.consume(poll(3, observed=frame(20)))
                self.assertEqual(low_hp.action, Action.ESCAPE)

    def test_missing_or_reordered_event_id_and_loss_are_visible_errors(self):
        cases = [poll(1, events=[event(2, 99)]),
                 poll(1, events=[event(1, 99), event(1, 1)]),
                 poll(1, events=[event(True, 99)]),
                 poll(1, events=[event(1, 99)], events_lost=True),
                 poll(1, events_lost=0), poll(1, events={}),
                 poll(1, events=[None])]
        for item in cases:
            with self.subTest(item=item):
                subject = consumer()
                with self.assertRaises(ValueError):
                    subject.consume(item)
                self.assertEqual(subject.prediction.last_event_id, 0)
                self.assertIsNone(subject.controller.engine.evaluate().player_hp)

    def test_old_map_events_are_discarded_without_consuming_current_latch(self):
        subject = consumer()
        old = frame(1, "epoch:old")
        _, initial = subject.consume(poll(1, events=[event(1, 99, observed=old)]))
        self.assertEqual((initial.action, initial.tracked_hp), (Action.NONE, 100))
        _, current_hit = subject.consume(poll(2, events=[event(2, 71)]))
        self.assertEqual(current_hit.action, Action.ESCAPE)
        _, old_then_low = subject.consume(poll(3, observed=frame(1),
                                              events=[event(3, 99, observed=old)]))
        self.assertEqual(old_then_low.action, Action.NONE)
        self.assertEqual(subject.prediction.last_event_id, 3)

    def test_new_map_epoch_with_same_name_rearms_once(self):
        subject = consumer()
        _, first = subject.consume(poll(1, events=[event(1, 71)]))
        self.assertEqual(first.action, Action.ESCAPE)
        _, new_map = subject.consume(poll(2, observed=frame(100, "epoch:2"),
                                         events=[event(2, 99)]))
        self.assertEqual(new_map.action, Action.NONE)
        _, second = subject.consume(poll(3, observed=frame(100, "epoch:2"),
                                        events=[event(3, 71, observed=frame(100, "epoch:2"))]))
        self.assertEqual(second.action, Action.ESCAPE)

    def test_unknown_latest_map_or_health_never_uses_queued_damage(self):
        for missing in (frame(map_id=None), frame(hp=None), frame(map_name=None), frame(max_hp=None)):
            with self.subTest(missing=missing):
                subject = consumer()
                _, result = subject.consume(poll(1, observed=missing, events=[event(1, 99)]))
                self.assertEqual(result.action, Action.NONE)
                self.assertIsNone(result.tracked_hp)

    def test_disabled_prediction_uses_observed_health_only(self):
        subject = consumer(enabled=False)
        _, result = subject.consume(poll(1, events=[event(1, 99)]))
        self.assertEqual((result.action, result.tracked_hp), (Action.NONE, 100))
        self.assertFalse(subject.prediction.state()["enabled"])

    def test_state_filters_keep_pending_damage_but_sync_reconciles_it(self):
        subject = consumer(use_state_filters=True)
        gated = frame(gate_a=True)
        subject.consume(poll(1, observed=gated, events=[event(1, 90, observed=gated)]))
        _, result = subject.consume(poll(2, events=[event(2, kind="health_sync")]))
        self.assertEqual((result.action, result.tracked_hp), (Action.NONE, 100))

    def test_public_frame_validator_has_no_live_side_effects(self):
        subject = consumer()
        subject.consume(poll(1))
        before = subject.controller.engine.evaluate()
        with self.assertRaises(ValueError):
            validate_frame(HealthFrame("", "Vault", 1, 100, False, False))
        self.assertEqual(subject.controller.engine.evaluate(), before)

    def test_prediction_policy_requires_explicit_boolean_and_defaults_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}", encoding="utf-8")
            self.assertIs(load_config(path).prediction_enabled, False)
            path.write_text('{"prediction_enabled": true}', encoding="utf-8")
            self.assertIs(load_config(path).prediction_enabled, True)
            for invalid in (None, 0, "false", []):
                path.write_text(json.dumps({"prediction_enabled": invalid}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()

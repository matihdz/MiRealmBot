from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from autonexus_controller import HealthFrame
from replay_autonexus import (DEFAULT_RECORDING, compare_thresholds,
                              load_recording, replay_recording)


START = datetime(2026, 10, 24, tzinfo=timezone.utc)


def frame(hp=100, map_id="map:1", map_name="Realm", **changes):
    result = asdict(HealthFrame(map_id, map_name, hp, 100, None, None))
    result.update(changes)
    return result


def row(seconds, event, **values):
    return {"captured_at_utc": (START + timedelta(seconds=seconds)).isoformat(),
            "event": event, **values}


def observation(seconds, sequence, hp=100, **values):
    result = row(seconds, "decision", status="fresh", sequence=sequence,
                 age_ms=0, frame=frame(hp))
    result.update(values)
    return result


def hit(seconds, sequence, event_id, amount=65, hp=100, kind="damage", **values):
    event = {"event_id": event_id, "kind": kind, "age_ms": 0, "frame": frame(hp)}
    if kind == "damage":
        event.update(amount=amount, cause="projectile")
    event.update(values)
    return row(seconds, "prediction_transport", sequence=sequence,
               age_ms=0, events=[event], events_lost=False)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "recording.jsonl"

    def write_recording(self, *items):
        content = "\n".join(json.dumps(item) for item in (
            row(0, "start", active=False), *items,
            row(20, "cleanup", complete=True, errors=[]))) + "\n"
        self.path.write_text(content, encoding="utf-8")
        return load_recording(self.path)

    def sample_recording(self):
        return self.write_recording(observation(0.1, 1), hit(1, 2, 1),
            hit(1.4, 3, 2, hp=35, kind="health_sync"),
            observation(1.5, 4, hp=35), hit(1.7, 5, 3, hp=35),
            observation(1.7, 5, hp=35))

    def test_actual_owners_apply_strict_threshold_and_keep_one_request(self):
        recording = self.sample_recording()
        at35 = replay_recording(recording, 35)
        at40 = replay_recording(recording, 40)
        self.assertEqual((at35.summary["first_escape"]["event_id"],
                          at35.summary["first_escape"]["predicted_hp"]), (3, -30))
        self.assertEqual((at40.summary["first_escape"]["event_id"],
                          at40.summary["first_escape"]["predicted_hp"]), (1, 35))
        self.assertEqual(at40.summary["escape_count"], 1)
        self.assertIn("supervivencia no comprobada", at40.summary["result_label"])

    def test_prediction_disabled_uses_observed_health_not_damage_subtraction(self):
        result = replay_recording(self.sample_recording(), 40, prediction_enabled=False)
        first = result.summary["first_escape"]
        self.assertEqual((first["event_id"], first["time_seconds"], first["predicted_hp"]),
                          (2, 1.4, 35))

    def test_same_sequence_transport_and_decision_consumed_once(self):
        result = replay_recording(self.sample_recording(), 30)
        self.assertEqual(len(result.rows), 5)
        self.assertEqual(result.summary["damage_count"], 2)
        self.assertEqual([item["event_id"] for item in result.rows].count(3), 1)

    def test_source_hash_and_missing_poll_frame_limitation_are_preserved(self):
        recording = self.sample_recording()
        self.assertEqual(recording.source_sha256,
                         hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(recording.source_path, str(self.path.resolve()))
        self.assertTrue(any("aproximación" in item for item in recording.limitations))
        self.assertEqual(recording.steps[-1].frame_basis, "recorded_poll_frame")

    def test_logged_action_is_not_reused(self):
        recording = self.write_recording(observation(1, 1, decision={
            "action": "escape", "tracked_hp": -300, "limit": 999}))
        self.assertIsNone(replay_recording(recording).summary["first_escape"])

    def test_future_telemetry_and_decision_share_one_poll_observation(self):
        telemetry = observation(1, 1)
        telemetry["event"] = "telemetry"
        recording = self.write_recording(row(.1, "lifecycle", status="running"),
            telemetry, observation(1, 1), row(2, "stop_requested"),
            row(3, "lifecycle", status="stopping"))
        self.assertEqual(len(recording.steps), 1)
        self.assertEqual(len(replay_recording(recording).rows), 1)

    def test_conflicting_same_sequence_observations_are_not_silently_merged(self):
        telemetry = observation(1, 1)
        telemetry["event"] = "telemetry"
        with self.assertRaisesRegex(ValueError, "incompatibles"):
            self.write_recording(telemetry, observation(1, 1, hp=10))

    def test_original_event_age_not_current_clock_controls_freshness(self):
        recording = self.write_recording(observation(0.1, 1),
            hit(1, 2, 1, amount=99, age_ms=251))
        result = replay_recording(recording)
        self.assertIsNone(result.summary["first_escape"])
        self.assertEqual(result.rows[-1]["predicted_hp"], 100)

    def test_old_map_event_does_not_override_exact_poll_map_or_rearm(self):
        recording = self.write_recording(observation(0.1, 1),
            hit(1, 2, 1, amount=99),
            observation(1, 2, frame=frame(100, "map:2", "Nexus")))
        result = replay_recording(recording)
        self.assertIsNone(result.summary["first_escape"])
        self.assertEqual(result.rows[-1]["map_name"], "Nexus")

    def test_unknown_frame_does_not_rearm_same_map_latch(self):
        recording = self.write_recording(observation(0.1, 1), hit(1, 2, 1, amount=90),
            observation(2, None, status="missing", age_ms=None, frame=None),
            observation(3, 3, hp=20))
        result = replay_recording(recording)
        self.assertEqual(result.summary["escape_count"], 1)
        self.assertEqual(result.rows[-1]["reason"], "exit was already requested for this map")

    def test_unknown_gates_block_when_filters_enabled(self):
        result = replay_recording(self.sample_recording(), 40, use_state_filters=True)
        self.assertIsNone(result.summary["first_escape"])
        self.assertEqual(result.rows[1]["reason"], "player gates are unknown")

    def test_map_transition_rearms_and_nexus_is_excluded(self):
        recording = self.write_recording(observation(.1, 1, hp=10),
            observation(1, 2, frame=frame(10, "map:2", "Nexus")),
            observation(2, 3, frame=frame(10, "map:3", "Realm")))
        result = replay_recording(recording)
        self.assertEqual([item["action"] for item in result.rows], ["escape", "none", "escape"])

    def test_sync_of_unchanged_hp_discards_pending_damage(self):
        recording = self.write_recording(observation(.1, 1), hit(1, 2, 1, amount=60),
            hit(2, 3, 2, kind="health_sync"), hit(3, 4, 3, amount=20))
        result = replay_recording(recording)
        self.assertEqual(result.rows[-1]["predicted_hp"], 80)
        self.assertIsNone(result.summary["first_escape"])

    def test_comparison_returns_simple_serializable_rows(self):
        result = compare_thresholds(self.sample_recording(), (30, 35, 40))
        self.assertEqual([item["event_id"] for item in result], [3, 3, 1])
        json.dumps(result)

    def test_bool_threshold_and_invalid_percent_rejected(self):
        recording = self.sample_recording()
        for percent in (True, 0, 100, float("nan"), float("inf"), "40", 10**1000):
            with self.subTest(percent=percent), self.assertRaises(ValueError):
                replay_recording(recording, percent)

    def test_invalid_json_reports_source_line(self):
        self.path.write_text(json.dumps(row(0, "start")) + "\nnot json\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "línea 2"):
            load_recording(self.path)

    def test_unknown_schema_missing_start_and_bad_timestamp_are_explained(self):
        for content, message in (
            (json.dumps(row(0, "other")), "no soportado"),
            (json.dumps(observation(1, 1)), "start"),
            (json.dumps({"event": "start", "captured_at_utc": "2026-10-24"}), "zona horaria"),
        ):
            with self.subTest(message=message):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_recording(self.path)

    def test_invalid_or_lost_event_rejected_with_transport_line(self):
        for changes, message in (({"events_lost": True}, "perdió eventos"),
                                 ({"events": [hit(1, 1, 2)["events"][0]]}, "esperaba 1")):
            transport = hit(1, 2, 1)
            transport.update(changes)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, "línea 3.*" + message):
                self.write_recording(observation(.1, 1), transport)

    def test_bad_health_and_freshness_do_not_silently_change_meaning(self):
        for change, message in (({"frame": frame(True)}, "int32"),
                                ({"age_ms": 251}, "250 ms")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                self.write_recording(observation(1, 1, **change))

    def test_backward_timestamp_or_agent_sequence_rejected(self):
        for records, message in (
            ((observation(2, 1), observation(1, 2)), "tiempo retroceden"),
            ((observation(1, 2), observation(2, 1)), "secuencia.*retrocedió"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.write_recording(*records)

    def test_public_synthetic_two_hit_record_uses_production_owners(self):
        recording = load_recording(DEFAULT_RECORDING)
        start = json.loads(DEFAULT_RECORDING.read_text(encoding="utf-8").splitlines()[0])
        self.assertIs(start["synthetic"], True)
        self.assertEqual(recording.damage_count, 2)
        self.assertEqual(recording.duration_seconds, 3)
        self.assertEqual(len(recording.steps), 6)
        self.assertTrue(all(step.frame_basis == "recorded_poll_frame"
                            for step in recording.steps))
        for percent, event_id, predicted_hp, limit in (
                (30, 3, -30, 30), (35, 3, -30, 34), (40, 1, 35, 40)):
            with self.subTest(percent=percent):
                result = replay_recording(recording, percent)
                first = result.summary["first_escape"]
                self.assertEqual((first["event_id"], first["predicted_hp"], first["limit"]),
                                 (event_id, predicted_hp, limit))
                self.assertEqual(result.summary["escape_count"], 1)
                self.assertEqual(result.rows[-2]["observed_hp"], 36)
                self.assertEqual(result.rows[-1]["observed_hp"], 0)
        at30 = replay_recording(recording, 30)
        at40 = replay_recording(recording, 40)
        self.assertAlmostEqual(at30.summary["first_escape"]["time_seconds"] -
                               at40.summary["first_escape"]["time_seconds"], .766946, places=6)

    def test_bundled_default_recording_loads_from_another_working_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.directory.name)
            recording = load_recording()
        finally:
            os.chdir(previous)
        self.assertEqual(Path(recording.source_path), DEFAULT_RECORDING)
        self.assertEqual(recording.damage_count, 2)


if __name__ == "__main__":
    unittest.main()

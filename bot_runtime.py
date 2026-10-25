"""MiRealmBot module runtime; CLI observes by default and --active enables actions.

Requires the adjacent Frida agent and a matching, locally pinned GameAssembly.
The game must already be running. No account payloads are written to the log.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable
from uuid import uuid4

from app_paths import APP_ROOT, ensure_default_config, resource_path
from autoaim_settings import AutoAimSettings, autoaim_mapping, parse_autoaim
from autonexus_controller import Controller, HealthFrame
from autonexus_core import Action, AutoNexus, Decision
from damage_prediction import AcceptedHitPrediction, finite_age
from debuffs_settings import DebuffSettings, debuffs_mapping, parse_debuffs
from game_settings import unity_key_name
from probe_game import EXPECTED_EXE, ProcessIdentity
from runtime_logs import BoundedRuntimeLog, LOG_BASENAME
from runtime_control import RuntimeControl
from windows_nexus import (NexusInputBlocked, NexusInputError, WindowsNexus,
                           Win32InputBackend, normalize_nexus_key)

ROOT = APP_ROOT
SUPPORTED_GAMEASSEMBLY_SHA256 = "c4f87cde09d713be23c96a5c17774bdbd65e36cd4a4c6fb017759b24daea01cb"
UNKNOWN_FRAME = HealthFrame(None, None, None, None, None, None)
MAX_AGE_MS = 250
POLL_SECONDS = 0.05


@dataclass(frozen=True)
class RuntimeConfig:
    threshold: float
    nexus_key: str
    excluded_maps: tuple[str, ...]
    gameassembly_sha256: str
    use_state_filters: bool = False
    prediction_enabled: bool = False
    autoaim: AutoAimSettings = AutoAimSettings()
    detect_game_keys: bool = True
    autonexus_enabled: bool = True
    debuffs: DebuffSettings = DebuffSettings()


def config_from_mapping(data: object, threshold_percent: float | None = None) -> RuntimeConfig:
    """Validate CLI and interface configuration through the semantic owners."""
    if not isinstance(data, dict):
        raise ValueError("La configuración de MiRealmBot debe contener un objeto JSON")
    threshold = data.get("threshold", 0.30)
    if threshold_percent is not None:
        if isinstance(threshold_percent, bool) or not isinstance(threshold_percent, (int, float)):
            raise ValueError("El umbral porcentual debe ser un número")
        try:
            threshold = threshold_percent / 100
        except OverflowError as error:
            raise ValueError("El umbral porcentual debe ser finito") from error
    key = normalize_nexus_key(data.get("nexus_key", "R"))
    names = data.get("excluded_maps", ["Nexus"])
    if not isinstance(names, list):
        raise ValueError("excluded_maps debe ser una lista de nombres")
    pin = data.get("gameassembly_sha256", SUPPORTED_GAMEASSEMBLY_SHA256)
    if not isinstance(pin, str) or pin.lower() != SUPPORTED_GAMEASSEMBLY_SHA256:
        raise ValueError("La configuración no corresponde a esta versión de MiRealmBot. Usa la configuración incluida en su descarga.")
    use_state_filters = data.get("use_state_filters", False)
    if not isinstance(use_state_filters, bool):
        raise ValueError("use_state_filters debe ser true o false")
    prediction_enabled = data.get("prediction_enabled", False)
    if not isinstance(prediction_enabled, bool):
        raise ValueError("prediction_enabled debe ser true o false")
    detect_game_keys = data.get("detect_game_keys", True)
    if not isinstance(detect_game_keys, bool):
        raise ValueError("detect_game_keys debe ser true o false")
    autoaim = parse_autoaim(data.get("autoaim"))
    autonexus_enabled = data.get("autonexus_enabled", True)
    if not isinstance(autonexus_enabled, bool):
        raise ValueError("AutoNexus activado debe ser true o false")
    debuffs = parse_debuffs(data.get("debuffs"))
    # Validate now, before saving or starting a worker. Retain the supplied
    # fraction; AutoNexus owns its float32 representation when used.
    Controller(AutoNexus(threshold, use_state_filters=use_state_filters), names)
    return RuntimeConfig(threshold, key, tuple(names), pin.lower(), use_state_filters,
                         prediction_enabled, autoaim, detect_game_keys, autonexus_enabled, debuffs)


def load_config(path: Path, threshold_percent: float | None = None) -> RuntimeConfig:
    with Path(path).open(encoding="utf-8-sig") as handle:
        return config_from_mapping(json.load(handle), threshold_percent)


def config_to_mapping(config: RuntimeConfig) -> dict[str, Any]:
    """Return only owned settings; save_config retains other existing keys."""
    return {"threshold": config.threshold, "nexus_key": config.nexus_key,
            "excluded_maps": list(config.excluded_maps),
            "gameassembly_sha256": config.gameassembly_sha256,
            "use_state_filters": config.use_state_filters,
            "prediction_enabled": config.prediction_enabled,
            "autoaim": autoaim_mapping(config.autoaim),
            "detect_game_keys": config.detect_game_keys,
            "autonexus_enabled": config.autonexus_enabled,
            "debuffs": debuffs_mapping(config.debuffs)}


def resolve_game_nexus_key(payload: object, config: RuntimeConfig) -> tuple[str | None, str]:
    """Use the game's current resolved binding, or the explicit manual policy."""
    if not config.autonexus_enabled:
        return None, "disabled"
    if not config.detect_game_keys:
        return config.nexus_key, "manual"
    if not isinstance(payload, dict):
        return None, "waiting"
    bindings = payload.get("game_bindings")
    binding = bindings.get("nexus") if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        return None, "waiting"
    try:
        age = finite_age(binding.get("age_ms"))
    except ValueError:
        return None, "waiting"
    if age > MAX_AGE_MS:
        return None, "waiting"
    name = unity_key_name(binding.get("unity_code"))
    try:
        return normalize_nexus_key(name), "detected"
    except ValueError:
        return None, "unsupported"


def save_config(path: Path, config: RuntimeConfig) -> Path | None:
    """Validate and atomically replace settings, retaining one previous backup."""
    config = config_from_mapping(config_to_mapping(config))
    path = Path(path)
    original = path.read_bytes() if path.exists() else None
    data = {} if original is None else json.loads(original.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("La configuración de MiRealmBot debe contener un objeto JSON")
    data.update(config_to_mapping(config))
    replacement = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if original == replacement:
        return None
    backup = None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        if original is not None:
            backup = path.with_name(f"{path.name}.bak")
            with backup.open("wb") as handle:
                handle.write(original)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


def verify_module_hash(path: Path, expected_hash: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except FileNotFoundError as error:
        raise RuntimeError("No se encontró la instalación compatible del juego. Abre RotMG Exalt desde el launcher oficial; esta versión no admite instalaciones de Steam ni ubicaciones personalizadas.") from error
    result = digest.hexdigest()
    if result != expected_hash:
        raise RuntimeError("Esta versión del juego no es compatible con MiRealmBot. El juego puede haberse actualizado; necesitas una versión de MiRealmBot con un perfil verificado para esa actualización.")
    return result


def verify_start(result: object, expected_pid: int, expected_module: Path) -> None:
    if not isinstance(result, dict):
        raise RuntimeError("start() no devolvió la identidad del agente")
    pid, module_path = result.get("pid"), result.get("module_path")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid != expected_pid:
        raise RuntimeError("El PID del agente no coincide con el proceso verificado")
    if not isinstance(module_path, str) or not module_path:
        raise RuntimeError("El agente no identificó GameAssembly.dll")
    actual = os.path.normcase(str(Path(module_path).resolve(strict=True)))
    expected = os.path.normcase(str(expected_module.resolve(strict=True)))
    if actual != expected:
        raise RuntimeError("La ruta GameAssembly.dll del agente no coincide con la instalación verificada")


@dataclass(frozen=True)
class PollSample:
    status: str
    sequence: int | None
    age_ms: float | None
    frame: HealthFrame | None


def parse_poll(payload: object, last_sequence: int) -> PollSample:
    """Validate transport identity/freshness; leave observation semantics to Controller."""
    if payload is None:
        return PollSample("missing", None, None, UNKNOWN_FRAME)
    if not isinstance(payload, dict):
        raise ValueError("poll() debe devolver un objeto o null")
    sequence, age = payload.get("sequence"), payload.get("age_ms")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("sequence debe ser un entero no negativo")
    age = finite_age(age)
    if sequence < last_sequence:
        raise ValueError("La secuencia del agente retrocedió")
    if age > MAX_AGE_MS:
        return PollSample("stale", sequence, age, UNKNOWN_FRAME)
    if sequence == last_sequence:
        return PollSample("duplicate", sequence, age, None)
    frame = HealthFrame(*(payload.get(name) for name in (
        "map_id", "map_name", "player_hp", "max_hp", "gate_a", "gate_b", "tracked_hp")))
    return PollSample("fresh", sequence, age, frame)


class TelemetryConsumer:
    def __init__(self, controller: Controller, *, prediction_enabled: bool = False):
        self.controller = controller
        self.last_sequence = -1
        self.prediction = AcceptedHitPrediction(controller, enabled=prediction_enabled,
                                                max_age_ms=MAX_AGE_MS)

    def consume(self, payload: object) -> tuple[PollSample, Decision | None]:
        try:
            sample = parse_poll(payload, self.last_sequence)
            decision = self.prediction.consume(
                status=sample.status, frame=sample.frame,
                raw_events=payload.get("events", []) if isinstance(payload, dict) else [],
                events_lost=payload.get("events_lost", False) if isinstance(payload, dict) else False)
        except ValueError:
            self.prediction.invalidate("invalid_transport")
            raise
        if sample.sequence is not None:
            self.last_sequence = sample.sequence
        return sample, decision


def deliver(decision: Decision, *, active: bool, pid: int, key: str,
            actuator: WindowsNexus | None) -> dict[str, Any]:
    """Report input delivery separately from reaching Nexus; never retry a latch."""
    result = {"status": "none", "key_sent": False, "nexus_confirmed": False}
    if decision.action is not Action.ESCAPE:
        return result
    if not active:
        result["status"] = "observe_only"
        return result
    if actuator is None:
        raise RuntimeError("Falta el actuador de Windows en modo activo")
    try:
        request = actuator.request(pid, key)
        result.update(status="key_sent", key_sent=request.key_sent,
                      nexus_confirmed=request.nexus_confirmed)
    except NexusInputError as error:
        result.update(status="blocked" if isinstance(error, NexusInputBlocked) else "failed",
                      error=str(error))
    return result


def cleanup_runtime(script: Any, session: Any, identity: Any) -> list[str]:
    errors = []
    actions = []
    if script is not None:
        actions.extend((("agent.stop", lambda: script.exports_sync.stop()),
                        ("script.unload", script.unload)))
    if session is not None:
        actions.append(("session.detach", session.detach))
    if identity is not None:
        actions.append(("identity.close", identity.close))
    for name, action in actions:
        try:
            action()
        except Exception as error:
            errors.append(f"{name}: {error}")
    return errors


def _positive_duration(value: str) -> float:
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("La duración debe ser positiva y finita")
    return result


def _decision_record(decision: Decision) -> dict[str, Any]:
    record = asdict(decision)
    record["action"] = decision.action.value
    return record


def _console_health(decision: Decision, frame: HealthFrame | None) -> str:
    """Keep observed partial HP visible when a blocked decision lacks it."""
    hp, maximum, tracked = decision.player_hp, decision.max_hp, decision.tracked_hp
    if frame is not None:
        if hp is None:
            hp = frame.player_hp
        if maximum is None:
            maximum = frame.max_hp
        if tracked is None:
            tracked = frame.tracked_hp
    return f"HP {hp}/{maximum}, seguido {tracked}"


class _RuntimeCancelled(Exception):
    pass


def _agent_options(config: RuntimeConfig, *, active: bool) -> dict[str, Any]:
    aim = autoaim_mapping(config.autoaim)
    aim["active"] = active and config.autoaim.enabled
    debuffs = debuffs_mapping(config.debuffs)
    debuffs["active"] = active and config.debuffs.has_protection
    return {"prediction_enabled": config.autonexus_enabled and config.prediction_enabled,
            "detect_game_keys": config.detect_game_keys,
            "autoaim": aim, "debuffs": debuffs}


def run_runtime(config: RuntimeConfig, *, active: bool = False, pid: int | None = None,
                duration: float | None = None, stop_event: Any = None,
                on_update: Callable[[dict[str, Any]], None] | None = None,
                control: RuntimeControl | None = None) -> int:
    """Own one integration lifecycle for CLI or a single interface worker.

    Callbacks run on this calling thread and must enqueue UI work. Stop is
    cooperative: a completed Frida call is followed by a stop check before the
    next attachment, installation, or input request. Finished is emitted only
    after every owned resource has had its cleanup attempt.
    """
    identity = session = script = log = None
    code = 0
    log_path = None
    selected_pid = None
    callback = on_update
    callback_error = None
    script_load_attempted = False
    session_id = uuid4().hex
    logging_failed = False

    def check_stop() -> None:
        if callback_error is not None:
            raise RuntimeError(f"La interfaz dejó de recibir actualizaciones: {callback_error}")
        if stop_event is not None and stop_event.is_set():
            raise _RuntimeCancelled()

    def notify(entry: dict[str, Any]) -> None:
        nonlocal callback, callback_error
        if callback is not None:
            try:
                callback(entry)
            except Exception as error:
                # Stop at the next cooperative boundary if presentation fails.
                callback = None
                callback_error = str(error)
                print(f"La interfaz dejó de recibir actualizaciones: {error}", file=sys.stderr)

    def entry_for(event: str, **fields: Any) -> dict[str, Any]:
        return {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "event": event, "pid": selected_pid, "session_id": session_id,
                "log_path": None if log_path is None else str(log_path), **fields}

    def disable_logging(error: Exception) -> None:
        nonlocal log, logging_failed
        failed_log, log = log, None
        if failed_log is not None:
            try:
                failed_log.close()
            except Exception:
                pass
        if not logging_failed:
            logging_failed = True
            message = f"No se pudo guardar el registro; la sesión continúa: {error}"
            print(message, file=sys.stderr)
            notify(entry_for("logging_warning", reason="logging_disabled",
                             logging_enabled=False, error=message))

    def record(event: str, **fields: Any) -> None:
        entry = entry_for(event, **fields)
        try:
            if log is not None:
                log.record(entry)
        except Exception as error:
            # Disk failure cannot stop protection, input, or terminal delivery.
            disable_logging(error)
        notify(entry)

    try:
        record("lifecycle", status="connecting", active=active)
        check_stop()
        config = config_from_mapping(config_to_mapping(config))
        if not (config.autonexus_enabled or config.autoaim.enabled or config.debuffs.has_protection):
            raise ValueError("Activa al menos un módulo antes de iniciar MiRealmBot")
        controller = Controller(AutoNexus(config.threshold,
                                         enabled=config.autonexus_enabled,
                                         use_state_filters=config.use_state_filters),
                                config.excluded_maps)
        prediction_enabled = config.autonexus_enabled and config.prediction_enabled
        if not isinstance(active, bool):
            raise ValueError("active debe ser true o false")
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int)
                                or not 0 < pid <= 0xFFFFFFFF):
            raise ValueError("PID debe ser un entero positivo de Windows")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise ValueError("La duración debe ser positiva y finita")
            _positive_duration(str(duration))
        if sys.platform != "win32":
            raise RuntimeError("La integración interna requiere Windows")
        try:
            import frida
        except ImportError as error:
            raise RuntimeError("Falta frida. Utiliza .venv\\Scripts\\python.exe de esta carpeta") from error
        check_stop()
        device = frida.get_local_device()
        matches = [process for process in device.enumerate_processes()
                   if process.name == "RotMG Exalt.exe" and (pid is None or process.pid == pid)]
        check_stop()
        if not matches:
            code = 2
            message = "No se encontró el juego abierto. Abre RotMG Exalt desde el launcher oficial y deja un personaje en Nexus. Esta versión no admite Steam ni ubicaciones personalizadas."
            print(message)
            record("error", error_type="GameNotFound", error=message)
            return code
        if len(matches) != 1:
            raise ValueError("Hay varios procesos del juego. Selecciona uno con --pid")
        selected_pid = pid = matches[0].pid
        identity = ProcessIdentity(pid, EXPECTED_EXE)
        module_path = EXPECTED_EXE.parent / "GameAssembly.dll"
        module_hash = verify_module_hash(module_path, config.gameassembly_sha256)
        agent_path = resource_path("bot_agent.js")
        source_bytes = agent_path.read_bytes()
        source_files = [resource_path(name) for name in (
            "game_bindings_agent.js", "autoaim_agent.js", "debuffs_agent.js", "bot_agent.js")]
        sources = [(path, path.read_bytes()) for path in source_files]
        source = "\n".join(content.decode("utf-8") for _, content in sources)
        analysis = ROOT / "analysis"
        log_path = analysis / f"{LOG_BASENAME}.jsonl"
        try:
            log = BoundedRuntimeLog(analysis)
            log_path = log.path
            if log.legacy_files_retained:
                message = (f"Quedan {log.legacy_files_retained} registros anteriores ocupados "
                           "o sin permiso de borrado; se reintentará al iniciar otra sesión.")
                print(message, file=sys.stderr)
                record("logging_warning", reason="legacy_cleanup_pending",
                       logging_enabled=True, retained_files=log.legacy_files_retained,
                       error=message)
        except Exception as error:
            disable_logging(error)
        check_stop()
        identity.assert_alive()
        session = device.attach(pid)
        check_stop()
        script = session.create_script(source, name="mirealmbot_runtime", runtime="qjs")
        check_stop()
        script_load_attempted = True
        script.load()
        check_stop()
        options = _agent_options(config, active=active)
        aim_options, debuff_options = options["autoaim"], options["debuffs"]
        start = script.exports_sync.start(options)
        verify_start(start, pid, module_path)
        identity.assert_alive()
        check_stop()
        mode = "ACTIVO" if active else "OBSERVACION"
        print(f"MiRealmBot {mode}; PID {pid}")
        print("AutoNexus: " + (f"ACTIVADO; umbral {config.threshold * 100:g}%; tecla {config.nexus_key}"
                                   if config.autonexus_enabled else "DESACTIVADO"))
        if config.use_state_filters:
            print("Filtros de estado: ACTIVADOS; estados desconocidos bloquean AutoNexus.")
        else:
            print("Filtros de estado: OMITIDOS por configuración; decide por vida, mapa y habilitación.")
        print("Predicción de impactos aceptados: "
              + ("ACTIVADA (proyectiles locales)" if prediction_enabled else "DESACTIVADA"))
        print(f"Registro: {log_path}")
        record("start", active=active, threshold=controller.engine.threshold,
               nexus_key=config.nexus_key, gameassembly_sha256=module_hash,
               process_creation_filetime=identity.created_filetime,
               use_state_filters=config.use_state_filters, bindings=start.get("bindings"),
               prediction_enabled=prediction_enabled, autonexus_enabled=config.autonexus_enabled,
               autoaim=aim_options, debuffs=debuff_options, detect_game_keys=config.detect_game_keys,
               prediction_scope="accepted_local_projectile_hits",
               agent_path=str(agent_path), agent_sha256=hashlib.sha256(source_bytes).hexdigest(),
               agent_sources=[{"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
                              for path, content in sources],
               injected_source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest())
        consumer = TelemetryConsumer(controller, prediction_enabled=prediction_enabled)
        input_backend = Win32InputBackend()
        actuator = WindowsNexus(backend=input_backend) if active and config.autonexus_enabled else None
        record("config_applied", revision=0, configuration=config_to_mapping(config))
        record("lifecycle", status="running", active=active)
        started = time.monotonic()
        next_report = started
        next_telemetry = started
        last_map = None
        while duration is None or time.monotonic() - started < duration:
            check_stop()
            identity.assert_alive()
            pending = None if control is None else control.take_latest()
            if pending is not None:
                revision, requested_config = pending
                try:
                    requested_config = config_from_mapping(config_to_mapping(requested_config))
                    if requested_config.gameassembly_sha256 != config.gameassembly_sha256:
                        raise ValueError("El perfil del juego no puede cambiar durante una sesión")
                    if requested_config.excluded_maps != config.excluded_maps:
                        raise ValueError("Los mapas excluidos no pueden cambiar durante una sesión")
                except (ValueError, TypeError, AttributeError) as error:
                    record("config_error", revision=revision, reason="invalid_configuration", error=str(error))
                else:
                    if not (requested_config.autonexus_enabled or requested_config.autoaim.enabled
                            or requested_config.debuffs.has_protection):
                        # No further poll or action can run; normal finalization
                        # stops every native module without requiring configure.
                        config = requested_config
                        record("config_applied", revision=revision, configuration=config_to_mapping(config))
                        record("stop_requested", reason="no_enabled_modules")
                        break
                    try:
                        result = script.exports_sync.configure(_agent_options(requested_config, active=active))
                        if not isinstance(result, dict) or result.get("configured") is not True:
                            raise RuntimeError("El agente no confirmó la configuración aplicada")
                        # The same core owns the map's one-request latch. Updating
                        # settings cannot create another map or rearm an escape.
                        controller.engine.threshold = requested_config.threshold
                        controller.engine.use_state_filters = requested_config.use_state_filters
                        controller.engine.enabled = requested_config.autonexus_enabled
                        next_prediction = (requested_config.autonexus_enabled
                                           and requested_config.prediction_enabled)
                        if next_prediction != prediction_enabled:
                            consumer.prediction.enabled = next_prediction
                            consumer.prediction.last_events = ()
                            consumer.prediction.invalidate("configuration_changed")
                        if active and requested_config.autonexus_enabled and actuator is None:
                            actuator = WindowsNexus(backend=input_backend)
                    except Exception as error:
                        # The native side may already have changed. Stop and own
                        # cleanup instead of reporting an old snapshot as applied.
                        record("config_error", revision=revision, reason="configure_failed", error=str(error))
                        raise RuntimeError(f"No se pudo aplicar la configuración en vivo: {error}") from error
                    config = requested_config
                    prediction_enabled = next_prediction
                    record("config_applied", revision=revision, configuration=config_to_mapping(config))
                    check_stop()
            payload = script.exports_sync.poll()
            check_stop()
            resolved_key, binding_status = resolve_game_nexus_key(payload, config)
            # Keep collecting damage/health while waiting for the canonical
            # binding; do not consume a map's one-shot request with a guessed key.
            controller.engine.enabled = config.autonexus_enabled and resolved_key is not None
            # Preserve every event batch, including a rejected/lost one. Decision
            # output is rate-limited independently and cannot be its only log.
            if isinstance(payload, dict) and (payload.get("events") or payload.get("events_lost")):
                record("prediction_transport", sequence=payload.get("sequence"),
                       age_ms=payload.get("age_ms"), events=payload.get("events"),
                       events_lost=payload.get("events_lost", False),
                       diagnostics=payload.get("diagnostics"),
                       prediction_enabled=prediction_enabled)
            sample, decision = consumer.consume(payload)
            if consumer.prediction.last_events:
                record("prediction_state", sequence=sample.sequence,
                       prediction=consumer.prediction.state())
            if decision is not None:
                now = time.monotonic()
                current_map = None if sample.frame is None else sample.frame.map_id
                map_changed = current_map is not None and current_map != last_map
                if current_map is not None:
                    last_map = current_map
                requested = decision.action is Action.ESCAPE
                delivery = None
                if requested:
                    check_stop()
                    identity.assert_alive()
                    check_stop()
                    delivery = deliver(decision, active=active, pid=pid,
                                       key=resolved_key, actuator=actuator)
                fields = dict(status=sample.status, sequence=sample.sequence, age_ms=sample.age_ms,
                              map_id=current_map, map_name=None if sample.frame is None else sample.frame.map_name,
                              frame=None if sample.frame is None else asdict(sample.frame),
                              use_state_filters=config.use_state_filters,
                              prediction=consumer.prediction.state(),
                              diagnostics=payload.get("diagnostics") if isinstance(payload, dict) else None,
                              autoaim=payload.get("autoaim") if isinstance(payload, dict) else None,
                              debuffs=payload.get("debuffs") if isinstance(payload, dict) else None,
                              game_bindings=payload.get("game_bindings") if isinstance(payload, dict) else None,
                              nexus_binding_status=binding_status,
                              decision=_decision_record(decision), delivery=delivery)
                if requested or map_changed or now >= next_telemetry:
                    try:
                        foreground_pid = input_backend.foreground_process_id()
                        foreground_error = None
                    except NexusInputError as error:
                        foreground_pid, foreground_error = None, str(error)
                    record("telemetry", **fields, foreground_pid=foreground_pid,
                           game_foreground=None if foreground_pid is None else foreground_pid == pid,
                           foreground_error=foreground_error)
                    next_telemetry = now + 0.1
                if requested or map_changed or now >= next_report:
                    record("decision", **fields)
                    label = delivery["status"] if delivery is not None else sample.status
                    print(f"{label}: {_console_health(decision, sample.frame)}, "
                          f"limite {decision.limit}; {decision.reason}")
                    if delivery is not None and "error" in delivery:
                        print(f"No se entregó la tecla: {delivery['error']}", file=sys.stderr)
                    next_report = now + 1
            if stop_event is None:
                time.sleep(POLL_SECONDS)
            else:
                stop_event.wait(POLL_SECONDS)
    except _RuntimeCancelled:
        record("stop_requested")
    except KeyboardInterrupt:
        code = 130
        print("MiRealmBot interrumpido; descargando la integración.")
        record("interrupted")
    except Exception as error:
        code = 1
        print(f"MiRealmBot detenido: {error}", file=sys.stderr)
        record("error", error_type=type(error).__name__, error=str(error))
    finally:
        record("lifecycle", status="stopping", active=active)
        # A created but never loaded script cannot install hooks. Detaching its
        # session releases it without calling unavailable agent RPC exports.
        errors = cleanup_runtime(script if script_load_attempted else None, session, identity)
        record("cleanup", complete=not errors, errors=errors)
        for error in errors:
            print(f"Error de limpieza: {error}", file=sys.stderr)
        if errors and code == 0:
            code = 1
        if log is not None:
            try:
                log.close()
            except Exception as error:
                disable_logging(error)
            log = None
        record("lifecycle", status="finished", active=active,
               cleanup_complete=not errors, errors=errors, code=code)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Archivo de configuración; por defecto mirealmbot.json")
    parser.add_argument("--pid", type=int, help="PID del juego ya abierto; siempre se verifica la ruta")
    parser.add_argument("--threshold", type=float, help="Umbral en porcentaje, mayor que 0 y menor que 100")
    parser.add_argument("--active", action="store_true", help="Solicitar la tecla Nexus; por defecto sólo observa")
    parser.add_argument("--duration", type=_positive_duration, help="Detener después de estos segundos")
    args = parser.parse_args(argv)
    try:
        config = load_config(ensure_default_config() if args.config is None else args.config, args.threshold)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    if sys.platform != "win32":
        parser.error("La integración interna requiere Windows")
    return run_runtime(config, active=args.active, pid=args.pid, duration=args.duration)


if __name__ == "__main__":
    raise SystemExit(main())

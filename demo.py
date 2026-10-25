"""Demostración local de la regla AutoNexus; no controla el juego."""

import argparse

from autonexus_core import Action, AutoNexus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=30,
                        help="Umbral mayor que 0 y menor que 100 por ciento")
    args = parser.parse_args()
    try:
        engine = AutoNexus(threshold=args.threshold / 100)
    except ValueError as error:
        parser.error(str(error))
    print(f"SIMULACION: umbral {args.threshold:g}%. No se envia ninguna accion al juego.\n")

    def show(label, decision):
        action = "SOLICITAR SALIDA" if decision.action is Action.ESCAPE else "esperar"
        print(f"{label}: {action}; HP jugador={decision.player_hp}, "
              f"HP seguido={decision.tracked_hp}, limite={decision.limit}")

    engine.new_map(excluded=False)
    engine.set_gates(False, False)
    engine.update_health(1000, 1000)
    limit = engine.evaluate().limit
    engine.update_health(limit, 1000)
    show("Justo en el limite", engine.evaluate())
    show("Un punto de daño efectivo", engine.apply_damage(1, cause="demo"))
    engine.update_health(1000, 1000)
    show("Recuperacion de HP", engine.evaluate())
    engine.update_health(0, 1000)
    show("Otra bajada en el mismo mapa", engine.evaluate())
    engine.new_map(excluded=False)
    engine.set_gates(False, False)
    engine.update_health(0, 1000)
    show("Nuevo mapa con HP bajo", engine.evaluate())


if __name__ == "__main__":
    main()

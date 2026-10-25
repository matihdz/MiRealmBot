# Registro de demostración

`demo-combat.jsonl` es un ejemplo **sintético**, creado para probar la interfaz y comparar parámetros sin abrir el juego. No contiene una captura de un jugador, direcciones de memoria, identificadores de procesos ni rutas de instalaciones.

La secuencia representa 100/100 HP, un golpe efectivo de 65 a los 1,00 segundos, la actualización de vida a 35, otro golpe de 65 a los 1,766946 segundos, una regeneración a 36 y una actualización final a 0. Sus horas UTC son arbitrarias. Cada lote de eventos incluye una observación del mismo ciclo en un registro `decision`, de modo que la reproducción conserva el frame completo sin aproximarlo.

Con predicción activada y sin filtros de estado, el umbral del 30 % solicita salir en el segundo golpe y el del 40 % en el primero: una diferencia simulada de aproximadamente 0,77 segundos. El motor conserva la conversión original a `float32`, la truncación del límite a entero y la comparación estricta `<`; por eso un umbral del 35 % sobre 100 HP produce un límite de 34.

La reproducción recalcula decisiones sin enviar teclas. La línea posterior de vida 0 pertenece al escenario fijo; cambiar el umbral no cambia los eventos posteriores del archivo. El resultado no demuestra que una salida real hubiera salvado a un personaje ni mide la latencia del juego.

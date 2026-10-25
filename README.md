# MiRealmBot

Interfaz local de AutoNexus para **RotMG Exalt en Windows x64**. Lee la vida del jugador, reconoce una ruta de impactos de proyectil y permite comparar parámetros con una demostración sintética.

**[Descargar MiRealmBot para Windows](https://github.com/matihdz/MiRealmBot/releases/latest)** · [Guía paso a paso](docs/USO.md)

## Para usarlo sin programar

1. En la última Release, abre **Assets** y descarga **MiRealmBot-Windows-x64.zip**.
2. Pulsa **Extraer todo** y abre **MiRealmBot.exe** dentro de la carpeta extraída.
3. Abre el juego mediante su launcher oficial y deja el personaje en Nexus.
4. En **Juego en vivo**, elige la misma tecla Nexus que tienes configurada en el juego.
5. Comprueba primero la lectura con **Observar → Iniciar observación**; luego pulsa **Detener**.
6. Ajusta tus parámetros, selecciona **Activo** y pulsa **Iniciar AutoNexus activo**.
7. Vuelve a la ventana del juego y mantenla en primer plano.

El ZIP incluye Python, Frida y Tkinter. Conserva la carpeta `_internal` junto al ejecutable. **Source code (zip)** es para desarrollo; el archivo preparado para usar es **MiRealmBot-Windows-x64.zip**.

Abrir el programa no inicia la protección. **Observar** y **Reproducir registro** no envían teclas. **Detener** cierra la sesión y retira los hooks. Las opciones quedan bloqueadas mientras una sesión está funcionando.

## Parámetros y simulación

- Umbral de vida configurable; la descarga comienza en **90 %** para las primeras pruebas.
- Predicción de daño de proyectiles activada en la configuración de ejemplo.
- Tecla Nexus configurable: `CTRL` significa Control izquierdo; se admiten Control derecho y letras.
- Reproducción y modo en vivo tienen borradores separados.
- Guardado con copia de seguridad. Configuración y registros quedan junto al ejecutable.

La demostración incluida es **sintética**, con dos golpes de 65: al configurar 30 % solicita salida en el segundo; con 40 %, en el primero. Sólo recalcula decisiones, sin conectarse al juego ni demostrar supervivencia. Puedes cargar tus propios registros JSONL desde la interfaz.

## Compatibilidad y alcance

Primera entrega: Windows de 64 bits, instalación del launcher oficial y el perfil exacto de GameAssembly indicado en `autonexus.example.json`. Steam y ubicaciones personalizadas no están admitidas. Si el juego se actualiza, la conexión se bloquea hasta verificar y publicar un perfil compatible.

La predicción implementada cubre la ruta de impactos de proyectil investigada. No cubre terreno ni daño continuo. Los filtros generales de inmunidad siguen sin bindings completos. Sólo intenta una salida por mapa; si la tecla se bloquea, lo muestra y no reintenta automáticamente. El envío de una tecla no garantiza supervivencia ni confirma por sí solo la llegada al Nexus.

La integración carga temporalmente un agente Frida e instala hooks; no cambia los archivos del juego. Requiere que el juego ya esté abierto. El usuario de la instalación de desarrollo informó una salida activa satisfactoria; no se ha probado todavía en el equipo de otra persona.

## Desarrollo

[Cómo ejecutar, probar y generar el ZIP](docs/DESARROLLO.md).

El repositorio contiene el código propio y una demostración sintética. No incluye archivos del juego, el bot original, capturas personales ni herramientas de ingeniería inversa. El paquete portable incluye los avisos y textos de licencias de sus dependencias.

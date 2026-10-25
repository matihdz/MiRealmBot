# MiRealmBot

Interfaz local de **AutoNexus y AutoAim para RotMG Exalt en Windows x64**. La versión **0.2.0** añade las pestañas **AutoAim** y **Actualizaciones**. Lee la vida del jugador, reconoce una ruta de impactos de proyectil y permite comparar parámetros con una demostración sintética.

**[Descargar MiRealmBot para Windows](https://github.com/matihdz/MiRealmBot/releases/latest)** · [Guía paso a paso](docs/USO.md)

## Para usarlo sin programar

1. En la última Release, abre **Assets** y descarga **MiRealmBot-Windows-x64.zip**.
2. Pulsa **Extraer todo** y abre **MiRealmBot.exe** dentro de la carpeta extraída.
3. Abre el juego mediante su launcher oficial y deja el personaje en Nexus.
4. En **AutoAim**, deja marcada **Leer teclas del juego** y revisa la tecla Nexus detectada. Si no se reconoce, elige la misma tecla manualmente en el panel izquierdo.
5. En **Juego en vivo**, comprueba primero la lectura con **Observar → Iniciar observación**; luego pulsa **Detener**.
6. Ajusta tus parámetros, selecciona **Activo** y pulsa **Iniciar AutoNexus activo**.
7. Vuelve a la ventana del juego y mantenla en primer plano.

El ZIP incluye Python, Frida y Tkinter. Conserva la carpeta `_internal` junto al ejecutable. **Source code (zip)** es para desarrollo; el archivo preparado para usar es **MiRealmBot-Windows-x64.zip**.

Abrir el programa no inicia la protección. **Observar** y **Reproducir registro** no envían teclas ni modifican la puntería. **Detener** cierra la sesión y retira los hooks. Las opciones quedan bloqueadas mientras una sesión está funcionando.

## Parámetros y simulación

- Umbral de vida configurable; la descarga comienza en **90 %** para las primeras pruebas.
- Predicción de daño de proyectiles activada en la configuración de ejemplo.
- Lectura de las teclas resueltas del juego, incluidos valores predeterminados. En modo automático Nexus espera una lectura reconocida; desmarca **Leer teclas del juego** para elegir manualmente Control izquierdo (`CTRL`), Control derecho o una letra.
- Reproducción y modo en vivo tienen borradores separados.
- Guardado con copia de seguridad. Configuración y registros quedan junto al ejecutable.

La demostración incluida es **sintética**, con dos golpes de 65: al configurar 30 % solicita salida en el segundo; con 40 %, en el primero. Sólo recalcula decisiones, sin conectarse al juego ni demostrar supervivencia. Puedes cargar tus propios registros JSONL desde la interfaz.

## AutoAim

En **AutoAim**, marca **Activar AutoAim** y usa el modo **Activo** de la sesión en vivo. El único modo de selección es **Close to Mouse**: elige un enemigo cercano al cursor dentro del radio configurado. Empieza desactivado; sólo actúa cuando el autofire del juego está encendido y el juego no está en pausa. Activa autofire con la tecla que tengas configurada en el juego; no necesitas mantener una tecla adicional pulsada.

**Extrapolation** empieza en **302**. Con 0 apunta a la posición actual; valores mayores adelantan más según el movimiento del enemigo. Es un coeficiente de la fórmula investigada, cuya unidad temporal no está confirmada. **Radio junto al cursor** empieza en **4 unidades del mundo del juego**.

El puente ajusta el punto de puntería utilizado por la ruta investigada de disparos de proyectil. No mueve el cursor, activa autofire ni inicia ataques por sí mismo. La prueba activa del perfil actual registró selección de enemigos y ajustes de puntería, y el usuario confirmó que apunta correctamente. Quedan por medir la precisión y el funcionamiento en otro equipo.

## Actualizar

Al abrir la versión 0.2.0 se consulta GitHub para buscar una versión nueva. En **Actualizaciones** puedes pulsar **Buscar actualizaciones**, **Descargar actualización** y **Instalar y reiniciar**. Primero detén la sesión y espera a que termine su limpieza. Se conservan configuración, registros y copias de seguridad, y la carpeta anterior queda como respaldo junto a la nueva.

La versión **0.1.0** necesita una descarga manual de 0.2.0 para incorporar este actualizador. Al ejecutar desde código fuente, la pestaña permite consultar novedades y abrir **Ver descarga en GitHub**; la instalación desde la interfaz corresponde al paquete portable.

## Compatibilidad y alcance

Windows de 64 bits, instalación del launcher oficial y el perfil exacto de GameAssembly indicado en `autonexus.example.json`. Steam y ubicaciones personalizadas no están admitidas. Si el juego se actualiza, la conexión se bloquea hasta verificar y publicar un perfil compatible.

La predicción implementada cubre la ruta de impactos de proyectil investigada. No cubre terreno ni daño continuo. Los filtros generales de inmunidad siguen sin bindings completos. Sólo intenta una salida por mapa; si la tecla se bloquea, lo muestra y no reintenta automáticamente. El envío de una tecla no garantiza supervivencia ni confirma por sí solo la llegada al Nexus.

La integración carga temporalmente un agente Frida e instala hooks; no cambia los archivos del juego. Requiere que el juego ya esté abierto. El usuario de la instalación de desarrollo informó una salida activa satisfactoria; no se ha probado todavía en el equipo de otra persona.

## Desarrollo

[Cómo ejecutar, probar y generar el ZIP](docs/DESARROLLO.md).

El repositorio contiene el código propio y una demostración sintética. No incluye archivos del juego, el bot original, capturas personales ni herramientas de ingeniería inversa. El paquete portable incluye los avisos y textos de licencias de sus dependencias.

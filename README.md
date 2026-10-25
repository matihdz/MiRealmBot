# MiRealmBot

Cliente modular para **RotMG Exalt en Windows x64**. Puedes activar AutoNexus, AutoAim y Debuffs por separado. La versión **0.4.4** incluye protección y registro para Confuse y Blind, bloqueo local experimental de Silenced y registro de Quiet para pruebas. **Iniciar bot** pone en marcha los módulos habilitados; los registros conservan sólo un historial reciente.

**[Descargar MiRealmBot para Windows](https://github.com/matihdz/MiRealmBot/releases/latest)** · [Guía paso a paso](docs/USO.md)

## Para usarlo sin programar

1. En la última Release, abre **Assets** y descarga **MiRealmBot-Windows-x64.zip**.
2. Pulsa **Extraer todo** y abre **MiRealmBot.exe** dentro de la carpeta extraída.
3. Abre el juego mediante su launcher oficial y deja el personaje en Nexus.
4. En **AutoNexus**, marca **Activar AutoNexus** si quieres usarlo y configura el porcentaje, la predicción y la tecla Nexus.
5. Si quieres apuntado automático, configura **AutoAim**. Para AntiConfuse, habilita **Debuffs** y **Confuse**.
6. En **Juego en vivo**, pulsa **Iniciar bot**.
7. Vuelve a la ventana del juego y mantenla en primer plano.

El ZIP incluye Python, Frida y Tkinter. Conserva la carpeta `_internal` junto al ejecutable. **Source code (zip)** contiene el código para desarrollo; el archivo preparado para usar es **MiRealmBot-Windows-x64.zip**.

Abrir el programa deja la sesión detenida. **Iniciar bot** activa los módulos que hayas habilitado. Puedes usar sólo AutoAim dejando AutoNexus desmarcado, o sólo AutoNexus dejando los otros módulos apagados. Los cambios válidos se guardan automáticamente al terminar de editar y se aplican con la sesión en marcha; la interfaz muestra cuándo el motor los confirma. **Detener** cierra la sesión y retira los hooks. Apagar todos los módulos también detiene la sesión; volver a activarlos requiere pulsar Iniciar bot.

## Las cinco pestañas

| Pestaña | Contenido |
| --- | --- |
| **Juego en vivo** | Inicio y detención del bot, vida, mapa, decisiones y actividad reciente. |
| **AutoNexus** | Activación, porcentaje de salida, predicción de daño y tecla de regreso al Nexus. |
| **AutoAim** | Activación, Close to Mouse, Extrapolation, radio, prioridad de bosses y filtro de murallas. |
| **Debuffs** | Confuse y Blind independientes, Silenced experimental y registro opcional de Quiet para pruebas. |
| **Actualizaciones** | Buscar, descargar e instalar nuevas versiones desde GitHub. |

La barra lateral, la reproducción de registros y los botones de guardado se retiraron. Las pestañas de configuración guardan y aplican los ajustes automáticamente; iniciar el bot se hace desde **Juego en vivo**.

## AutoNexus

**Activar AutoNexus** controla el módulo completo. Empieza marcado para conservar el comportamiento anterior. Desmarcarlo impide enviar la tecla Nexus y desactiva su predicción, sin impedir que funcionen los otros módulos. El resto de sus ajustes queda guardado para cuando vuelvas a activarlo.

La configuración inicial usa **90 %** y predicción de impactos de proyectil activada. El umbral solicita salir cuando la vida observada o prevista queda estrictamente por debajo del límite.

**Leer teclas del juego** usa la tecla Nexus resuelta dentro del juego, incluidos sus valores predeterminados. En modo automático espera una lectura reconocida antes de actuar. Si no se reconoce, desmarca la opción y selecciona manualmente la misma tecla del juego: Control izquierdo (`CTRL`), Control derecho o una letra.

## AutoAim

Empieza desactivado. Marca **Activar AutoAim**; si la sesión ya funciona se aplica en vivo, y si está detenida pulsa **Iniciar bot** en Juego en vivo. **Close to Mouse** elige un enemigo cercano al cursor dentro del radio configurado. Sólo actúa con el autofire del juego encendido y el juego sin pausa; usa la tecla de autofire que tengas configurada en el juego.

**Extrapolation** empieza en **302**. Con 0 apunta a la posición actual; valores mayores adelantan según el movimiento del enemigo. La unidad temporal de este coeficiente no está confirmada. **Radio junto al cursor** empieza en **4 unidades del mundo del juego**.

- **Priorizar bosses**: prefiere un boss reconocido dentro del radio. Entre varios bosses elige el más cercano al cursor; sin uno, mantiene la selección normal. La clasificación depende del marcador de boss del juego.
- **Ignorar murallas**: excluye las clases de muralla del juego como objetivos, incluidas las destructibles. Los proyectiles conservan sus colisiones habituales.

Ambas opciones son independientes y empiezan desmarcadas. El puente ajusta el punto de puntería de la ruta investigada de proyectiles. No mueve el cursor ni activa autofire o ataques por sí mismo. El usuario confirmó el apuntado activo en el perfil actual; la precisión y el funcionamiento en otro equipo siguen pendientes de medición.

## Debuffs

Marca **Activar módulo Debuffs** y **Confuse** para bloquear la transformación de dirección y rotación de los controles del jugador local y registrar el efecto. La implementación se basa en análisis estático de RealmBot y del perfil actual del juego, y se probó con interfaces nativas simuladas. El usuario informó una prueba satisfactoria en su instalación.

El hook afecta únicamente al resultado booleano de la llamada de movimiento verificada. No borra las condiciones, sus iconos ni otros consumidores del estado. Requiere el juego en primer plano y una asociación reciente y válida con tu personaje.

**Registro de Confuse**, en Juego en vivo, muestra si el estado se detecta y los contadores acumulados de comprobaciones, detecciones y bloqueos en la llamada de movimiento. También conserva el último intervalo con muestras antes, durante y después, o indica si la lectura se interrumpió. Detectar el estado y bloquear una llamada de movimiento son hechos distintos: ver el efecto mientras estás quieto o con el juego fuera del primer plano no confirma un bloqueo. Comprueba el movimiento cuando recibas el efecto y comunica el resultado junto con los logs recientes.

**Blind** es una casilla independiente, desactivada por defecto. Suprime el resultado que activa el oscurecimiento en las llamadas verificadas del controlador visual del jugador local. Requiere datos recientes y el juego en primer plano. Conserva el estado y su icono; no cambia los demás efectos del controlador. Su ruta tiene análisis estático y pruebas simuladas; el usuario informó una prueba satisfactoria en su instalación. **Registro de Blind**, en Juego en vivo, separa los intervalos del efecto de las comprobaciones, detecciones y bloqueos visuales.

**Silenced** es independiente, experimental y empieza desmarcado. Omite únicamente las consultas verificadas de Silenced que detienen el procesamiento local de la habilidad; mantiene el coste de maná, el enfriamiento y los demás filtros normales. Requiere el juego en primer plano y datos recientes del personaje. En una prueba manual con las fuentes de 0.4.4, el usuario confirmó que la habilidad funcionó durante Silenced y el registro mostró omisiones del filtro durante ese episodio. Es un caso comprobado por el usuario; la aceptación remota no se instrumentó. El icono puede permanecer.

**Registro de Silenced** muestra los intervalos del estado y las consultas, detecciones y omisiones del filtro local. Un contador de omisiones no equivale a una habilidad usada o aceptada. Al probarlo, comprueba el efecto práctico de la habilidad; una animación o un cambio de maná no bastan para confirmarlo.

La protección contra **Quiet** todavía no está disponible: no se verificó una ruta que evite su efecto sobre maná y habilidades. Esta versión no ofrece protección AntiQuiet ni recupera MP.

Para recoger una prueba desde el cliente, marca **Activar módulo Debuffs** y **Quiet**. Se guarda y aplica en vivo. Puedes dejar los demás módulos como prefieras, incluido Confuse desmarcado. Esta casilla activa el registro y puede funcionar por sí sola; no activa una protección contra Quiet.

En **Juego en vivo**, **Registro de Quiet** muestra si el efecto se detectó, el MP local y su respaldo, y el último intervalo observado. Conserva una muestra anterior disponible, el inicio, la última durante el efecto y la posterior. Si cambias de mapa, desactivas el registro o se pierden los datos, indica que el intervalo quedó interrumpido. Un dato desconocido o antiguo no se presenta como Quiet desactivado. El respaldo es otro valor local del cliente; no se etiqueta como maná del servidor.

Puedes jugar normalmente con Quiet marcado para registro. Cuando recibas el efecto, comprueba qué ocurre con el maná y si puedes usar una habilidad, y comunica el resultado junto con los logs recientes. La detección por sí sola no demuestra que una habilidad haya sido aceptada ni que Quiet se haya bloqueado.

## Guardado y cambios en vivo

Cada cambio válido se guarda tras una pausa de aproximadamente medio segundo. Si dejas un número vacío o inválido, aparece un aviso y se conserva la última configuración válida. Durante una sesión, el indicador distingue el guardado del archivo y la confirmación del motor. Si falla la aplicación nativa, se detiene y limpia la sesión, mostrando el error.

`mirealmbot.json.bak` conserva una configuración anterior; el guardado automático no crea un archivo de respaldo nuevo por cada cambio.

## Registros recientes

El historial de sesiones reutiliza **tres archivos de hasta 1 MiB** en `analysis`: el actual y dos anteriores, con un máximo combinado de **3 MiB**. Al llenarse, se elimina el contenido más antiguo. La interfaz también conserva sólo actividad reciente.

Al iniciar una sesión se retiran los archivos del formato antiguo de logs del bot; si alguno está ocupado o no puede borrarse, se conserva y se informa para intentarlo en un próximo inicio. Otros archivos de análisis y respaldos no forman parte de esta limpieza ni de la cuota. Un fallo al escribir el registro muestra un aviso y permite continuar el bot.

## Actualizar

En **Actualizaciones**, pulsa **Buscar actualizaciones**, **Descargar actualización** e **Instalar y reiniciar**. Primero detén la sesión y espera su limpieza. Se conserva la configuración y la carpeta anterior queda como respaldo junto a la nueva. Los respaldos pueden conservar registros de versiones anteriores.

Las versiones desde **0.2.0** incluyen el actualizador y consultan GitHub al abrir. Si usas **0.1.0**, descarga la versión actual manualmente una vez. Desde código fuente puedes consultar novedades y abrir la descarga; la instalación desde la interfaz corresponde al paquete portable.

La configuración compartida ahora es `mirealmbot.json`. En el primer inicio se copian los ajustes de `autonexus.json` si aún no existe el archivo nuevo, conservando el original y una copia `.bak`. El ZIP incluye dos archivos con los nombres antiguos para que los actualizadores de 0.3.1 y anteriores puedan instalar esta versión.

## Compatibilidad y alcance

Windows de 64 bits, instalación del launcher oficial y el perfil exacto de GameAssembly indicado en `mirealmbot.example.json`. Steam y ubicaciones personalizadas no están admitidas. Si el juego se actualiza, la conexión se bloquea hasta verificar y publicar un perfil compatible.

La predicción cubre la ruta de impactos de proyectil investigada; terreno y daño continuo no están cubiertos. Los filtros generales de inmunidad siguen sin bindings completos. Sólo intenta una salida por mapa; si la tecla se bloquea, lo muestra y no reintenta automáticamente. El envío de la tecla no garantiza supervivencia ni confirma por sí solo la llegada al Nexus.

La integración carga temporalmente un agente Frida e instala hooks sin cambiar archivos del juego. Requiere el juego abierto. El usuario informó pruebas satisfactorias de los módulos en su instalación. El portable 0.4.4 se extrajo mediante el actualizador en una ruta con espacios: los 1047 archivos del manifiesto coinciden en hash y tamaño, y el ejecutable pasó su comprobación oculta de interfaz, recursos y configuración sin acceder al juego, enviar entradas ni consultar la red. Esta comprobación del paquete no establece compatibilidad con otro equipo o versión del juego.

## Desarrollo

[Cómo ejecutar, probar y generar el ZIP](docs/DESARROLLO.md).

El repositorio contiene el código propio y herramientas de reproducción para desarrollo. No incluye archivos del juego, el bot original, capturas personales ni herramientas de ingeniería inversa. El portable incluye los avisos y licencias de sus dependencias.

# MiRealmBot

Cliente modular para **RotMG Exalt en Windows x64**. En **0.4.0** puedes activar AutoNexus, AutoAim y Debuffs por separado. **Iniciar bot** pone en marcha los módulos habilitados; los registros conservan sólo un historial reciente.

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
| **Debuffs** | Protección frente a efectos de estado, con opciones por efecto. |
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

Marca **Activar módulo Debuffs** y **Confuse** para bloquear la transformación de dirección y rotación de los controles del jugador local. La implementación se basa en análisis estático de RealmBot y del perfil actual del juego, y se probó con interfaces nativas simuladas. Falta una prueba real de Confuse en el juego.

El hook afecta únicamente al resultado booleano de la llamada de movimiento verificada. No borra las condiciones, sus iconos ni otros consumidores del estado. Requiere el juego en primer plano y una asociación reciente y válida con tu personaje.

**Quiet** aparece desmarcado y deshabilitado: todavía no se verificó una ruta que evite su efecto sobre maná y habilidades. Esta versión no ofrece protección AntiQuiet ni recupera MP.

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

La integración carga temporalmente un agente Frida e instala hooks sin cambiar archivos del juego. Requiere el juego abierto. El usuario informó una salida activa satisfactoria en la instalación de desarrollo. Las pruebas de la interfaz 0.4.0 se realizan sin combate; no establecen compatibilidad con otro equipo.

## Desarrollo

[Cómo ejecutar, probar y generar el ZIP](docs/DESARROLLO.md).

El repositorio contiene el código propio y herramientas de reproducción para desarrollo. No incluye archivos del juego, el bot original, capturas personales ni herramientas de ingeniería inversa. El portable incluye los avisos y licencias de sus dependencias.

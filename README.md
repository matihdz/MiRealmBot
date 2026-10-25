# MiRealmBot

Interfaz local de **AutoNexus y AutoAim para RotMG Exalt en Windows x64**. En **0.3.1**, **Iniciar bot** activa directamente los módulos configurados. Las configuraciones están organizadas por módulo y los registros conservan sólo un historial reciente.

**[Descargar MiRealmBot para Windows](https://github.com/matihdz/MiRealmBot/releases/latest)** · [Guía paso a paso](docs/USO.md)

## Para usarlo sin programar

1. En la última Release, abre **Assets** y descarga **MiRealmBot-Windows-x64.zip**.
2. Pulsa **Extraer todo** y abre **MiRealmBot.exe** dentro de la carpeta extraída.
3. Abre el juego mediante su launcher oficial y deja el personaje en Nexus.
4. En **AutoNexus**, configura el porcentaje de vida, la predicción y la detección de la tecla Nexus. Pulsa **Guardar configuración**.
5. Si quieres apuntado automático, configura **AutoAim** y guarda.
6. En **Juego en vivo**, pulsa **Iniciar bot**.
7. Vuelve a la ventana del juego y mantenla en primer plano.

El ZIP incluye Python, Frida y Tkinter. Conserva la carpeta `_internal` junto al ejecutable. **Source code (zip)** contiene el código para desarrollo; el archivo preparado para usar es **MiRealmBot-Windows-x64.zip**.

Abrir el programa deja la sesión detenida. **Iniciar bot** activa AutoNexus y el AutoAim que hayas habilitado. **Detener** cierra la sesión y retira los hooks. Para cambiar ajustes durante una sesión, detén el bot, guarda los cambios e inicia de nuevo.

## Las cuatro pestañas

| Pestaña | Contenido |
| --- | --- |
| **Juego en vivo** | Inicio y detención del bot, vida, mapa, decisiones y actividad reciente. |
| **AutoNexus** | Porcentaje de salida, predicción de daño y tecla de regreso al Nexus. |
| **AutoAim** | Activación, Close to Mouse, Extrapolation, radio, prioridad de bosses y filtro de murallas. |
| **Actualizaciones** | Buscar, descargar e instalar nuevas versiones desde GitHub. |

La barra lateral y la pestaña de reproducción de registros se retiraron. Las pestañas de configuración sólo guardan ajustes; iniciar el bot se hace desde **Juego en vivo**.

## AutoNexus

La configuración inicial usa **90 %** y predicción de impactos de proyectil activada. El umbral solicita salir cuando la vida observada o prevista queda estrictamente por debajo del límite.

**Leer teclas del juego** usa la tecla Nexus resuelta dentro del juego, incluidos sus valores predeterminados. En modo automático espera una lectura reconocida antes de actuar. Si no se reconoce, detén la sesión, desmarca la opción y selecciona manualmente la misma tecla del juego: Control izquierdo (`CTRL`), Control derecho o una letra.

## AutoAim

Empieza desactivado. Marca **Activar AutoAim**, guarda y pulsa **Iniciar bot** en Juego en vivo. **Close to Mouse** elige un enemigo cercano al cursor dentro del radio configurado. Sólo actúa con el autofire del juego encendido y el juego sin pausa; usa la tecla de autofire que tengas configurada en el juego.

**Extrapolation** empieza en **302**. Con 0 apunta a la posición actual; valores mayores adelantan según el movimiento del enemigo. La unidad temporal de este coeficiente no está confirmada. **Radio junto al cursor** empieza en **4 unidades del mundo del juego**.

- **Priorizar bosses**: prefiere un boss reconocido dentro del radio. Entre varios bosses elige el más cercano al cursor; sin uno, mantiene la selección normal. La clasificación depende del marcador de boss del juego.
- **Ignorar murallas**: excluye las clases de muralla del juego como objetivos, incluidas las destructibles. Los proyectiles conservan sus colisiones habituales.

Ambas opciones son independientes y empiezan desmarcadas. El puente ajusta el punto de puntería de la ruta investigada de proyectiles. No mueve el cursor ni activa autofire o ataques por sí mismo. El usuario confirmó el apuntado activo en el perfil actual; la precisión y el funcionamiento en otro equipo siguen pendientes de medición.

## Registros recientes

El historial de sesiones reutiliza **tres archivos de hasta 1 MiB** en `analysis`: el actual y dos anteriores, con un máximo combinado de **3 MiB**. Al llenarse, se elimina el contenido más antiguo. La interfaz también conserva sólo actividad reciente.

Al iniciar una sesión se retiran los archivos del formato antiguo de logs del bot; si alguno está ocupado o no puede borrarse, se conserva y se informa para intentarlo en un próximo inicio. Otros archivos de análisis y respaldos no forman parte de esta limpieza ni de la cuota. Un fallo al escribir el registro muestra un aviso y permite continuar el bot.

## Actualizar

En **Actualizaciones**, pulsa **Buscar actualizaciones**, **Descargar actualización** e **Instalar y reiniciar**. Primero detén la sesión y espera su limpieza. Se conserva la configuración y la carpeta anterior queda como respaldo junto a la nueva. Los respaldos pueden conservar registros de versiones anteriores.

Las versiones desde **0.2.0** incluyen el actualizador y consultan GitHub al abrir. Si usas **0.1.0**, descarga la versión actual manualmente una vez. Desde código fuente puedes consultar novedades y abrir la descarga; la instalación desde la interfaz corresponde al paquete portable.

## Compatibilidad y alcance

Windows de 64 bits, instalación del launcher oficial y el perfil exacto de GameAssembly indicado en `autonexus.example.json`. Steam y ubicaciones personalizadas no están admitidas. Si el juego se actualiza, la conexión se bloquea hasta verificar y publicar un perfil compatible.

La predicción cubre la ruta de impactos de proyectil investigada; terreno y daño continuo no están cubiertos. Los filtros generales de inmunidad siguen sin bindings completos. Sólo intenta una salida por mapa; si la tecla se bloquea, lo muestra y no reintenta automáticamente. El envío de la tecla no garantiza supervivencia ni confirma por sí solo la llegada al Nexus.

La integración carga temporalmente un agente Frida e instala hooks sin cambiar archivos del juego. Requiere el juego abierto. El usuario informó una salida activa satisfactoria en la instalación de desarrollo. Las pruebas de la interfaz 0.3.1 se realizan sin combate; no establecen compatibilidad con otro equipo.

## Desarrollo

[Cómo ejecutar, probar y generar el ZIP](docs/DESARROLLO.md).

El repositorio contiene el código propio y herramientas de reproducción para desarrollo. No incluye archivos del juego, el bot original, capturas personales ni herramientas de ingeniería inversa. El portable incluye los avisos y licencias de sus dependencias.

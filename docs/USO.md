# Guía de uso

[Volver al proyecto](https://github.com/matihdz/MiRealmBot)

MiRealmBot se distribuye como una aplicación portable para Windows de 64 bits. Requiere RotMG Exalt instalado mediante su launcher oficial; Steam y las ubicaciones personalizadas no están admitidas. Cada versión es compatible con un perfil concreto del juego.

## Descargar y abrir

1. Abre [la última versión en GitHub](https://github.com/matihdz/MiRealmBot/releases/latest).
2. En **Assets**, descarga **MiRealmBot-Windows-x64.zip**.
3. Haz clic derecho en el ZIP y elige **Extraer todo**.
4. Abre **MiRealmBot.exe** dentro de la carpeta extraída.

Conserva la carpeta completa, incluida `_internal`. No necesitas instalar Python ni otras dependencias. **Source code (zip)** es el código para desarrollo; las instrucciones para ejecutarlo están en [Desarrollo](https://github.com/matihdz/MiRealmBot/blob/HEAD/docs/DESARROLLO.md).

## Primera sesión

1. Abre el juego y deja al personaje en Nexus.
2. Configura los módulos que quieras usar en **AutoNexus**, **AutoAim** y **Debuffs**. Puedes activar cada módulo por separado.
3. En **Juego en vivo**, pulsa **Iniciar bot** y comprueba que aparezcan tu vida y el mapa.
4. Vuelve al juego y mantenlo en primer plano. Si usas AutoAim, enciende el autofire con la tecla configurada en el juego.

Abrir MiRealmBot deja el bot detenido. El inicio y la parada se controlan únicamente desde **Juego en vivo**. Para terminar, pulsa **Detener** y espera a que concluya la limpieza de la sesión.

## AutoNexus

Marca **Activar AutoNexus** y ajusta **Salir bajo**. El valor inicial es **90 %**: la salida se solicita cuando la vida observada o prevista queda **estrictamente por debajo** del límite calculado.

**Prever daño de proyectiles** considera los impactos de proyectil detectados. No cubre daño de terreno ni daño continuo, y no garantiza salir a tiempo ante todos los golpes.

Deja **Leer teclas del juego** activado para detectar la tecla Nexus al iniciar. Si no se reconoce, desmarca la opción y elige manualmente la misma tecla que usas en el juego. `CTRL` corresponde a Control izquierdo; también se admite Control derecho.

Nexus está excluido. Se hace un intento de salida por mapa; si el envío queda bloqueado, no hay un reintento automático en ese mapa. **Tecla enviada** significa que Windows aceptó el envío: comprueba dentro del juego que llegaste al Nexus.

## AutoAim

Marca **Activar AutoAim** para usar **Close to Mouse**. Con el autofire del juego encendido, selecciona al enemigo más cercano al cursor dentro del radio configurado. No necesitas mantener una tecla adicional pulsada.

| Ajuste | Comportamiento |
| --- | --- |
| **Extrapolation** | Ajusta el adelanto según el movimiento del enemigo. Empieza en **302**; **0** apunta a su posición actual y valores mayores adelantan más. |
| **Radio junto al cursor** | Distancia máxima de selección alrededor del cursor, en unidades del mundo del juego. Empieza en **4**. |
| **Priorizar bosses** | Prefiere un boss reconocido dentro del radio. Entre varios, elige el más cercano al cursor; sin uno, usa la selección normal. |
| **Ignorar murallas** | Excluye las murallas como objetivos, incluidas las destructibles. Los proyectiles conservan sus colisiones habituales. |

AutoAim y ambos filtros empiezan desactivados. Un jefe sin el marcador de boss del juego se trata como un enemigo normal. AutoAim ajusta la puntería; no mueve el cursor ni enciende el autofire.

## Debuffs

Marca **Activar módulo Debuffs** y selecciona los efectos que quieras tratar. Las casillas son independientes y también habilitan su registro en **Juego en vivo**.

| Casilla | Función |
| --- | --- |
| **Confuse** | Mantiene la dirección normal de los controles de movimiento. |
| **Blind** | Evita el oscurecimiento de la visión. |
| **Silenced** | Intenta permitir el uso de la habilidad mediante un bloqueo local. Sigue siendo experimental; conserva el coste de maná y el enfriamiento normales. |
| **Quiet** | **Sólo registra el efecto.** No bloquea Quiet ni recupera maná. |

Mantén el juego en primer plano. El icono del efecto puede permanecer aunque la protección actúe. Los contadores del registro describen detecciones e intervenciones locales; no equivalen a habilidades utilizadas ni confirman por sí solos su resultado.

## Guardado y cambios en vivo

Los cambios válidos se guardan en `mirealmbot.json` después de una pausa de aproximadamente **medio segundo**. Con el bot iniciado, también se aplican a la sesión sin reiniciarla. El estado de la interfaz confirma el guardado y la aplicación.

Un campo vacío o inválido mantiene la última configuración válida. Si falla el guardado, el cambio queda pendiente; si falla su aplicación al juego, el bot se detiene y muestra el error. `mirealmbot.json.bak` conserva el ajuste anterior.

Apagar todos los módulos detiene la sesión. Para volver a usarla, activa alguno y pulsa **Iniciar bot**.

## Actualizar

MiRealmBot busca novedades en GitHub al abrir. También puedes hacerlo desde **Actualizaciones**:

1. Pulsa **Buscar actualizaciones** y, si hay una versión nueva, **Descargar actualización**.
2. En **Juego en vivo**, pulsa **Detener** y espera la limpieza.
3. Pulsa **Instalar y reiniciar**. La versión nueva se abrirá con el bot detenido.

Se conservan la configuración y los registros. La instalación anterior queda en una carpeta como `MiRealmBot.backup-…`. Esas copias ocupan espacio aparte; puedes eliminarlas cuando hayas comprobado que la actualización funciona.

Las versiones antiguas que usan `autonexus.json` migran sus ajustes a `mirealmbot.json` cuando este último todavía no existe, conservando el original y un respaldo. Si tu versión no incluye actualizador, descarga y extrae el paquete nuevo; con ambos programas cerrados, copia tu configuración a la carpeta nueva.

## Registros y resolución de problemas

El historial reciente está en `analysis`, junto al ejecutable. Usa `mirealmbot-session.jsonl` y dos archivos anteriores, con un máximo de **1 MiB por archivo y 3 MiB en total**. Lo más antiguo se sobrescribe y la actividad visible también se limita a mensajes recientes. Otros archivos y las carpetas de respaldo quedan fuera de esa cuota.

| Situación | Qué hacer |
| --- | --- |
| **El perfil no coincide** | Busca una versión de MiRealmBot compatible con la actualización del juego. No cambies el hash manualmente. |
| **Tecla bloqueada** | Mantén el juego al frente y comprueba la tecla Nexus. Detén y vuelve a iniciar para habilitar un nuevo intento. |
| AutoAim no apunta | Comprueba que el módulo y el bot estén activos, el autofire encendido, el juego sin pausa y el objetivo dentro del radio. |
| Falta `_internal` o el programa no abre desde el ZIP | Extrae de nuevo el paquete completo y abre el ejecutable desde la carpeta extraída. |
| **Registro en disco desactivado** | Revisa el aviso y los permisos de escritura de la carpeta. El bot puede continuar sin guardar el historial. |

Para informar de un problema, abre un [issue en GitHub](https://github.com/matihdz/MiRealmBot/issues) con la versión, los pasos para reproducirlo y el mensaje de error. Añade los registros recientes cuando ayuden a entender el fallo; recógelos pronto, antes de que roten. Conserva tu configuración al cambiar de versión.

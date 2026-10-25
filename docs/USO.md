# Cómo usar MiRealmBot

## Descargar y abrir

1. Abre [la última versión](https://github.com/matihdz/MiRealmBot/releases/latest).
2. En **Assets**, descarga **MiRealmBot-Windows-x64.zip**.
3. Haz clic derecho sobre el ZIP y elige **Extraer todo**. Guarda la carpeta, por ejemplo, en el Escritorio.
4. Entra a la carpeta extraída **MiRealmBot** y abre **MiRealmBot.exe**.

Conserva toda la carpeta, incluido `_internal`. El ejecutable lleva Python, Frida y Tkinter; no necesitas instalarlos. **Source code (zip)** contiene el código para desarrollo.

Requiere Windows de 64 bits y RotMG Exalt instalado mediante el launcher oficial. El perfil corresponde a una versión concreta de `GameAssembly.dll`. Steam y ubicaciones personalizadas no están admitidas.

## Configurar AutoNexus

Abre **AutoNexus**:

1. Marca **Activar AutoNexus** si quieres usar este módulo. Desmárcalo para usar AutoAim u otros módulos sin salidas automáticas al Nexus.
2. Ajusta el porcentaje de vida. El valor inicial es **90 %** para solicitar salir pronto durante las primeras pruebas.
3. Deja marcada la predicción si quieres considerar los impactos de proyectil detectados.
4. Deja marcada **Leer teclas del juego** para usar tu tecla Nexus efectiva, incluidos valores predeterminados. Se confirma dentro del juego al iniciar la sesión.
5. Si no se reconoce la tecla, desmarca esa opción y selecciona manualmente **la misma tecla** del juego. `CTRL` es Control izquierdo; también se admiten Control derecho y letras.

Los cambios válidos se guardan y aplican automáticamente; iniciar la sesión se hace desde Juego en vivo. La predicción reconoce la ruta de impactos de proyectil implementada; terreno y daño continuo no están cubiertos. El umbral solicita salir estrictamente por debajo del límite calculado.

## Configurar AutoAim

En **AutoAim**:

1. Marca **Activar AutoAim** si quieres usarlo.
2. Deja inicialmente **Extrapolation: 302** y **Radio junto al cursor: 4**.
3. Activa **Priorizar bosses** para preferir un boss reconocido dentro del radio. Entre varios bosses elige el más cercano al cursor; sin boss, usa la selección normal. Un jefe sin el marcador de boss del juego se trata como un enemigo normal.
4. Activa **Ignorar murallas** para excluir murallas, incluidas las destructibles, como objetivos. Los proyectiles conservan sus colisiones habituales.

El modo disponible es **Close to Mouse**. Selecciona al enemigo más cercano al cursor dentro del radio, medido en unidades del mundo del juego. AutoAim y ambos filtros empiezan desactivados.

**Extrapolation** controla el adelanto según el movimiento del enemigo: 0 usa su posición actual y un valor mayor adelanta más. La unidad temporal del coeficiente no está confirmada.

La pestaña muestra la tecla de autofire detectada como información. AutoAim usa el estado de autofire del juego: enciéndelo con tu tecla configurada; no hay que mantener una tecla adicional pulsada. Requiere el bot iniciado y el juego sin pausa.

El puente modifica el punto de puntería de la ruta investigada de proyectiles. No mueve el cursor ni enciende autofire o ataques. El usuario confirmó el apuntado activo en el perfil actual; la precisión y la compatibilidad con otro equipo siguen pendientes de medición.

## Configurar Debuffs

En **Debuffs**, marca **Activar módulo Debuffs** y **Confuse**. La protección bloquea la transformación de dirección y rotación de tus controles en la ruta de movimiento verificada del jugador local. Los iconos y las condiciones conservan su representación normal. Requiere el juego en primer plano y datos recientes del personaje.

**Quiet** queda desmarcado y deshabilitado: no se verificó una ruta que evite sus efectos sobre maná y habilidades. Esta versión no bloquea Quiet ni recupera MP. AntiConfuse tiene validación estática y pruebas con interfaces nativas simuladas; falta comprobarlo con el efecto real en el juego.

## Cambiar ajustes y guardado automático

Ya no hay botón Guardar configuración. Tras un cambio válido y una pausa de aproximadamente medio segundo, se guarda `mirealmbot.json`. Si el bot está funcionando, también recibe la configuración nueva sin reiniciar la sesión. El estado muestra cuándo se guardó y cuándo el motor confirmó su aplicación.

Si un número está vacío o no es válido, aparece un aviso y se mantiene la última configuración válida. Si no se puede guardar, el cambio queda pendiente y la sesión conserva los ajustes confirmados. Si falla la aplicación nativa, el bot se detiene, completa la limpieza y muestra el error.

El archivo `mirealmbot.json.bak` conserva el ajuste anterior sin acumular un respaldo por cada edición. Apagar todos los módulos detiene la sesión; si después activas alguno, vuelve a pulsar **Iniciar bot**.

## Iniciar y detener el bot

Todos los controles de sesión están en **Juego en vivo**:

1. Abre RotMG Exalt y deja al personaje en **Nexus**.
2. Pulsa **Iniciar bot**. Inicia los módulos que hayas habilitado; deben aparecer tu vida y el mapa. Si todos están apagados, activa al menos uno para poder iniciar.
3. Vuelve a la ventana del juego y mantenla en primer plano. Para AutoAim, enciende el autofire del juego y acerca el cursor al objetivo.
4. Usa **Detener** en MiRealmBot para cerrar toda la sesión y retirar la integración.

Abrir el programa deja la sesión detenida. **Iniciar bot** siempre inicia una sesión activa. Puedes editar los ajustes durante la sesión; el inicio también valida y guarda cualquier edición válida que hayas terminado justo antes de pulsarlo. Durante el cierre y la instalación de actualizaciones los controles quedan bloqueados.

En 0.3.0 se retiraron la pestaña de reproducción de registros y la barra lateral. AutoNexus y AutoAim contienen sus ajustes; la actividad y el control del bot están en Juego en vivo.

## Leer el estado

- **Activo**: la sesión está iniciada y aplica los módulos configurados cuando se cumplen sus condiciones.
- **Tecla enviada**: Windows aceptó el envío; comprueba dentro del juego que llegaste al Nexus.
- **Tecla bloqueada**: el juego no tenía el foco o no estaba disponible. No se reintenta automáticamente en el mismo mapa; detén y vuelve a iniciar cuando puedas mantener el foco del juego.
- **El perfil no coincide**: el juego cambió. Hace falta una versión cuyo perfil se haya verificado para esa actualización; no cambies el hash manualmente.
- **Registro en disco desactivado**: falló la escritura del historial. El bot continúa; revisa el aviso de la sesión.

El programa no garantiza supervivencia frente a todos los golpes.

## Registros y espacio usado

El historial reciente está junto al ejecutable, dentro de `analysis`:

- `mirealmbot-session.jsonl`: archivo actual, hasta **1 MiB**.
- `mirealmbot-session.1.jsonl` y `mirealmbot-session.2.jsonl`: dos archivos anteriores, hasta **1 MiB** cada uno.

Se reutilizan entre sesiones y se elimina lo más antiguo al llenarse. El máximo combinado es **3 MiB**. Las entradas individuales tienen un límite de 64 KiB; si una lo supera se guarda un aviso de omisión. La actividad visible también conserva sólo mensajes recientes.

Los logs con el formato antiguo del bot se limpian al iniciar una sesión. Si un archivo está ocupado o no se puede borrar, se informa y se vuelve a intentar en otro inicio. La limpieza no afecta otros archivos de análisis ni respaldos; esos archivos quedan fuera de la cuota.

Si necesitas comunicar un problema, comparte pronto el mensaje de error y los registros recientes: el historial va sobrescribiendo lo más antiguo.

## Actualizar desde el programa

Desde 0.2.0 se buscan novedades en GitHub al abrir. En **Actualizaciones**:

1. Pulsa **Buscar actualizaciones**.
2. Si hay una versión nueva, pulsa **Descargar actualización** y espera.
3. Pulsa **Detener** en Juego en vivo y espera su limpieza.
4. Pulsa **Instalar y reiniciar**. MiRealmBot se cerrará y abrirá la versión nueva, con la sesión detenida.

Se conservan `mirealmbot.json`, los registros de `analysis` y sus copias. Si vienes de 0.3.1 o anterior, al abrir se copian tus ajustes de `autonexus.json` al archivo nuevo cuando éste aún no existe. Se conserva el original y un respaldo `.bak`; después el programa usa `mirealmbot.json`. La carpeta anterior completa queda junto a la instalación, con un nombre como `MiRealmBot.backup-…`. Esos respaldos pueden conservar logs antiguos y no forman parte del límite de 3 MiB del historial activo.

Si usas **0.1.0**, descarga y extrae la versión actual manualmente una vez. Con ambos programas cerrados puedes copiar tu `autonexus.json` y la carpeta `analysis` a la carpeta nueva; el primer inicio migra la configuración. Después podrás usar el actualizador.

Desde código fuente, **Ver descarga en GitHub** abre la descarga manual. La instalación desde esta pestaña requiere el paquete portable.

## Si algo falla

Envía a quien te pasó el programa una captura del mensaje de error y el número de versión. La configuración está en `mirealmbot.json`; las copias terminan en `.bak`. Conserva esos archivos al cambiar de versión.

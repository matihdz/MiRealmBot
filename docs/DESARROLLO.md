# Desarrollo y construcción

## Ejecutar desde el código

Requisitos de desarrollo: Windows x64, Python 3.12 x64 y el juego instalado con su launcher oficial. Las personas que descargan el ZIP portable no necesitan este entorno.

Puedes ejecutar `Preparar entorno.bat` para crear `.venv`, instalar las dependencias y copiar `autonexus.example.json` a `autonexus.json` si aún no existe. Después abre `MiRealmBot.bat`.

Equivalente en PowerShell, desde la raíz:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item autonexus.example.json autonexus.json
.\.venv\Scripts\python.exe -B client_ui.py
```

El comando de copia es para un checkout nuevo; conserva tu configuración existente si ya la tienes. La interfaz siempre abre sin iniciar una sesión con el juego.

## Configuración e interfaz

`app_version.py` comparte la versión entre ventana, constructor y actualizador. Hay cuatro pestañas: Juego en vivo, AutoNexus, AutoAim y Actualizaciones. Desde 0.3.1, Juego en vivo sólo ofrece Iniciar bot y Detener; al pulsar Iniciar bot llama al runtime con `active=True`. Abrir la interfaz deja la sesión detenida. Las configuraciones de módulo se guardan sin iniciar el runtime; se bloquean durante la sesión y la limpieza. `ConfigDraft` posee los valores editables y los valida juntos; el worker recibe una instantánea inmutable.

La observación se conserva para desarrollo mediante `live_autonexus.py`: sin `--active` observa y con `--active` habilita la intervención. No es una opción del cliente público. Por ejemplo, con el juego abierto, `.\.venv\Scripts\python.exe -B live_autonexus.py --duration 30` recoge lecturas durante 30 segundos sin enviar teclas ni modificar la puntería.

La configuración de ejemplo mantiene AutoNexus en 90 %, predicción activada y `detect_game_keys=true`. AutoAim comienza desactivado, con `mode="close_to_mouse"`, `activation="autofire"`, `extrapolation=302` y `mouse_radius=4`.

La lectura inicial de teclas consulta valores guardados. El puente consulta `SettingsManager.GetKeyCode` para obtener la configuración efectiva, incluidos valores predeterminados y cambios personalizados. Una tecla Nexus reconocida se utiliza con **Leer teclas del juego**; la selección manual queda como alternativa explícita. AutoAim usa el estado de autofire del juego y muestra su tecla como información.

AutoAim selecciona cerca del cursor dentro del radio en unidades del mundo. La extrapolación sigue la fórmula investigada `posición del objetivo + coeficiente × velocidad`; la unidad temporal del coeficiente no está confirmada. El valor 0 conserva la posición actual. La modificación se limita al resultado utilizado por la llamada investigada de cámara/puntería de proyectiles, con AutoAim habilitado, modo Activo, autofire encendido y juego sin pausa. No mueve el cursor ni habilita autofire o ataques. El usuario confirmó el apuntado activo en el perfil actual; las pruebas de la nueva interfaz no sustituyen una prueba de combate ni acreditan otro equipo.

`autoaim.prioritize_bosses` y `autoaim.ignore_walls` son booleanos independientes, ambos `false` por defecto. Se descartan murallas antes de aplicar la prioridad de boss, conservando el radio y resolviendo por distancia al cursor. Boss procede de `ObjectProperties.HealthBarBossElement`, con cobertura limitada a enemigos que lo tienen. Murallas usa las clases `CaveWall`, `ConnectedWall`, `Wall` y `DoubleWall`; no se deriva de vida, tamaño o nombre.

La pestaña Actualizaciones consulta Releases públicas de GitHub. El inicio normal programa la primera consulta en segundo plano aproximadamente 1,5 segundos después de abrir la ventana; descargar e instalar requieren sus botones. Desde fuentes se pueden consultar novedades y abrir la descarga. La sustitución y el reinicio corresponden al portable, una vez detenida la sesión y confirmada su limpieza.

## Historial acotado

`runtime_logs.py` posee el formato, la cuota y la rotación del registro. Conserva `live-autonexus.jsonl` y dos archivos rotados, como máximo 1 MiB por archivo y 3 MiB combinados entre sesiones. Serializa entradas completas de hasta 64 KiB; las mayores dejan un marcador de omisión. Un bloqueo de archivo evita escritores simultáneos dentro de la misma instalación.

Al abrir el historial se retiran únicamente nombres que coinciden con el formato antiguo exacto de logs del runtime. Windows conserva archivos ocupados; la limpieza se reintenta en otro inicio. Otros archivos de análisis no pertenecen a este almacén. Los respaldos del actualizador pueden retener historial anterior y quedan fuera de su cuota.

Un fallo de apertura, escritura o cierre del log desactiva el registro en disco y emite `logging_warning`; no interrumpe el runtime ni su limpieza. La telemetría reemplazable de la interfaz y la actividad visible también tienen retención limitada. `session_id` distingue sesiones en el historial compartido.

## Comprobar

```powershell
.\.venv\Scripts\python.exe -B -m unittest -q
node --test test_autoaim_agent.cjs
```

Las pruebas de interfaz usan un runtime falso y Tk oculto. Las del historial usan carpetas temporales para verificar cuotas, rotación, limpieza de nombres antiguos y fallos de disco. No envían teclas ni se adjuntan al juego.

`replay_autonexus.py` y `examples/` se conservan como herramientas de desarrollo con datos sintéticos. La interfaz ya no los importa y el portable no los incluye. Las pruebas de reproducción no requieren capturas personales.

## Generar el ZIP portable

Usa Python 3.12 x64 en Windows y el entorno virtual. Instala el conjunto de construcción fijado:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\build_portable.py --build-id mi-construccion
```

El script no instala software. Reserva directorios nuevos bajo `build/` y `dist/`; no sustituye construcciones previas. El resultado es `dist/mi-construccion/MiRealmBot-Windows-x64.zip`, con un archivo de sumas SHA256 y un manifiesto de los archivos incluidos.

Se usa PyInstaller en modo carpeta, con ventana y sin UPX. Los recursos viven en `_internal`; `autonexus.json` y los registros se guardan junto al ejecutable.

El constructor copia una lista explícita de código y recursos. La configuración sale de `autonexus.example.json`, nunca de la configuración personal. No incluye `analysis/`, `.venv/`, registros, archivos del juego ni de otro bot. Incluye las licencias de las dependencias instaladas y los textos que figuran en `packaging/`.

## Comprobar el ejecutable construido

Extrae el ZIP completo en otra carpeta. Ejecuta allí:

```powershell
.\MiRealmBot.exe --portable-self-test
```

Esta comprobación valida el manifiesto de recursos, Frida, Tkinter, configuración, las cuatro pestañas, los controles de inicio/detención y las constantes del historial. Mantiene la ventana oculta y bloquea el runtime. No consulta las preferencias del juego, no se adjunta al juego, no envía teclas, no accede a la red ni ejecuta actualizaciones. Guarda el resultado en `analysis/portable-self-test.json`.

La compatibilidad con el juego, el aspecto de una ventana visible y el funcionamiento en otro equipo requieren comprobaciones distintas. Documenta por separado la evidencia del perfil, del punto de enganche y de cualquier prueba real realizada.

## Publicar una versión

Ejecuta las pruebas, construye y comprueba el ZIP antes de crear una Release. Publica `MiRealmBot-Windows-x64.zip` y `SHA256SUMS.txt` como assets, sin añadir ejecutables al historial Git.

0.1.0 necesita instalar una versión actual manualmente para incorporar el actualizador. Las siguientes actualizaciones conservan configuración, registros y copias, y mantienen la carpeta anterior como respaldo en una carpeta hermana. La instalación espera al cierre de MiRealmBot y a la limpieza de la sesión con el juego. No se publican datos locales.

Actualizar el juego requiere verificar el nuevo perfil y sus bindings. No cambies únicamente el hash para sortear la comprobación de compatibilidad.

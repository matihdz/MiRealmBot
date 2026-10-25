# Desarrollo y mantenimiento

Esta guía cubre el código fuente, las pruebas y la distribución. Para instalar y usar el cliente, consulta [Uso](USO.md).

## Entorno local

Se requiere Windows x64, Python 3.12 x64 con Tkinter y un entorno virtual. Node.js se utiliza para las pruebas de los agentes JavaScript. La integración en vivo requiere la instalación compatible de RotMG Exalt desde su launcher oficial; las pruebas automatizadas y la reproducción sintética no necesitan el juego.

Desde la raíz del repositorio, ejecuta `Preparar entorno.bat` y luego `MiRealmBot.bat`. El equivalente en PowerShell es:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -B -c "from app_paths import ensure_default_config; ensure_default_config()"
.\.venv\Scripts\python.exe -B client_ui.py
```

`requirements.txt` fija Frida. Para empaquetar, `requirements-build.txt` fija también las herramientas de construcción; evita actualizar dependencias de forma aislada.

## Organización del código

| Componente | Responsabilidad |
| --- | --- |
| `client_ui.py`, `client_updates.py` | Interfaz Tk, borrador de configuración, estado de sesión y actualizaciones. |
| `bot_runtime.py`, `runtime_control.py` | Validación y persistencia de ajustes, revisiones en vivo, conexión, ejecución y limpieza. |
| `autonexus_core.py`, `autonexus_controller.py`, `damage_prediction.py` | Decisiones de AutoNexus y predicción sobre observaciones aceptadas. |
| `autoaim_settings.py`, `debuffs_settings.py` | Contratos de configuración de los módulos. |
| `bot_agent.js` | Supervisor nativo: captura, identidad local, hooks y ciclo de vida compartido. |
| `autoaim_agent.js`, `debuffs_agent.js`, `game_bindings_agent.js` | Integraciones nativas de módulos y lectura de teclas. |
| `probe_game.py`, `game_settings.py`, `windows_nexus.py` | Identidad del proceso, preferencias del juego y entrega de la tecla Nexus. |
| `app_paths.py`, `runtime_logs.py` | Rutas, migración de configuración e historial acotado. |
| `update_core.py`, `updater_worker.ps1`, `tools/build_portable.py`, `packaging/` | Validación de descargas, instalación y construcción portable. |

### Configuración y sesión

`ConfigDraft` posee los valores editables. La interfaz guarda automáticamente una instantánea válida tras 500 ms sin cambios; `save_config` sustituye el archivo de forma atómica, conserva claves ajenas y respalda la versión anterior en `.bak`. Los analizadores de cada módulo definen los valores admitidos y sus predeterminados.

`RuntimeControl` conserva sólo la revisión pendiente más reciente. El worker recibe instantáneas inmutables, sin acceder a variables Tk, y confirma `config_applied` después de la respuesta del agente. Guardar un borrador y aplicarlo a la sesión son estados distintos. Desactivar todos los módulos detiene la sesión normalmente.

Abrir el cliente deja el bot detenido. Sólo Juego en vivo inicia o detiene la sesión; las demás pestañas configuran módulos independientes. El CLI conserva la observación para desarrollo. Este ejemplo se adjunta al juego abierto durante 30 segundos, sin habilitar acciones:

```powershell
.\.venv\Scripts\python.exe -B bot_runtime.py --duration 30
```

Consulta `bot_runtime.py --help` para los argumentos. `--active` habilita la intervención de los módulos configurados. Los accesos `Observar bot.bat` e `Iniciar bot.bat` llaman a esas dos variantes del CLI y admiten sus argumentos.

### Integración y limpieza

El runtime comprueba la ruta e identidad del proceso y el hash del perfil compatible. Los agentes poseen sus bindings y verifican firmas, metadatos y condiciones de aplicación antes de intervenir. Un cambio del juego requiere revisar ese perfil y sus consumidores; cambiar únicamente el hash no establece compatibilidad.

Al detener, el supervisor invalida asociaciones y retira sus hooks. El host intenta detener el agente, descargar el script, desconectar la sesión y cerrar la identidad del proceso, conservando los errores de limpieza. La interfaz y el instalador esperan a que termine este ciclo.

Los estados desconocidos, las lecturas históricas y los contadores de consultas deben conservar su significado. Una detección o una sustitución local no demuestra que el juego haya ejecutado una acción. Quiet usa `record_quiet` para diagnóstico; el flag de protección reservado `quiet` continúa sin admitir `true`.

### Rutas e historial

En fuentes, los datos se guardan en la raíz; en el portable, junto al ejecutable. Los recursos empaquetados se resuelven desde `_internal` mediante `resource_path`.

La configuración canónica es `mirealmbot.json`. Si no existe, `ensure_default_config` copia los bytes de `autonexus.json` y conserva un respaldo de migración; sin archivo antiguo, utiliza `mirealmbot.example.json`. Un archivo seleccionado explícitamente mantiene su ruta.

`runtime_logs.py` posee `analysis/mirealmbot-session.jsonl` y sus dos rotaciones: hasta 1 MiB por archivo y 3 MiB en total. La limpieza de registros antiguos sólo alcanza los nombres reconocidos por ese almacén. Los errores de disco generan avisos sin interrumpir las protecciones ni su limpieza. La actividad visible también tiene retención limitada.

## Pruebas

Desde la raíz:

```powershell
.\.venv\Scripts\python.exe -B -m unittest -q
node --test test_autoaim_agent.cjs test_debuffs_agent.cjs test_bot_agent.cjs
```

Las pruebas de interfaz usan Tk oculto y un runtime falso. Las pruebas de agentes simulan el entorno nativo; las de archivos usan directorios temporales. Estas suites no se adjuntan al juego ni envían entradas.

Al describir una validación, separa las pruebas automatizadas, la comprobación del portable y los resultados observados en el juego. Indica el perfil y alcance comprobados, y las limitaciones pendientes. Conserva la investigación específica de bindings junto al código y la evidencia local, sin convertir esta guía en un historial de sesiones personales.

### Reproducción sintética

[`examples/demo-combat.jsonl`](../examples/demo-combat.jsonl) representa un escenario artificial de 100 HP, dos golpes efectivos de 65 y una regeneración intermedia. `replay_autonexus.py` ofrece una API Python, sin interfaz de línea de comandos. Para comparar los umbrales del 30 % y 40 %:

```powershell
.\.venv\Scripts\python.exe -B -c "import json; from replay_autonexus import load_recording, compare_thresholds; recording = load_recording('examples/demo-combat.jsonl'); print(json.dumps(compare_thresholds(recording, thresholds=(30, 40)), ensure_ascii=False, indent=2))"
```

Con la predicción predeterminada y sin filtros de estado, el 30 % solicita salir en el segundo golpe y el 40 % en el primero. La reproducción usa los motores de producción y conserva las observaciones registradas: cambiar el umbral no altera los eventos posteriores. No envía teclas ni demuestra supervivencia o latencia real. El ejemplo no contiene datos de un jugador y no se incluye en el portable.

`Probar simulacion.bat` ejecuta otra demostración, `demo.py`, centrada en la regla de AutoNexus; no reproduce el JSONL. Ambas herramientas funcionan sin abrir el juego.

## Construir y comprobar el portable

Instala las dependencias de construcción en el mismo entorno y elige un identificador nuevo para cada intento:

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-build.txt
.\.venv\Scripts\python.exe tools\build_portable.py --build-id mi-construccion
```

El constructor verifica plataforma, Python, versiones fijadas y las versiones de Tcl y OpenSSL correspondientes a sus licencias. Reserva directorios nuevos en `build/` y `dist/`, sin sobrescribir construcciones anteriores. Produce:

- `dist/mi-construccion/MiRealmBot-Windows-x64.zip`.
- `dist/mi-construccion/SHA256SUMS.txt`.
- `BUILD-MANIFEST.json` dentro de la aplicación, con versión, dependencias, archivos y hashes.

PyInstaller genera una carpeta con ventana, sin UPX. La lista pública de fuentes y recursos pertenece a `tools/build_portable.py`; el spec consume esa selección. Al añadir un recurso, actualiza su contrato y las validaciones de construcción, arranque y actualización que lo consumen.

Extrae el ZIP completo en otra carpeta y ejecuta desde allí:

```powershell
.\MiRealmBot.exe --portable-self-test
```

El resultado queda en `analysis/portable-self-test.json`. Comprueba recursos, dependencias, configuración e interfaz con la ventana oculta y el runtime bloqueado; no se adjunta al juego, envía entradas ni consulta actualizaciones. La apariencia de la ventana visible y la compatibilidad con el juego requieren verificaciones separadas.

## Distribución y compatibilidad

`app_version.py` es la fuente compartida de versión. Antes de publicar, ejecuta las pruebas, construye el ZIP y comprueba el ejecutable extraído. Publica una Release estable `vX.Y.Z` en GitHub con `MiRealmBot-Windows-x64.zip` y `SHA256SUMS.txt`; el actualizador exige esos nombres. Describe cambios y validación en las notas de esa Release.

El actualizador valida versión, descarga, SHA256, extracción y manifiesto antes de instalar. La sustitución corresponde al portable y espera al cierre del cliente tras la limpieza de la sesión. Conserva la configuración, los registros y archivos personales admitidos, y respalda la carpeta anterior. Desde fuentes se pueden consultar versiones y abrir su descarga. Las instalaciones 0.1.0 requieren una actualización manual para incorporar el actualizador.

La distribución incluye `mirealmbot.example.json` y una copia idéntica llamada `autonexus.json`, pero nunca precrea `mirealmbot.json`: así la migración respeta ajustes conservados por actualizadores antiguos. `autonexus_agent.js` es un marcador sin código ejecutable. Ambos nombres antiguos forman parte del contrato de los actualizadores hasta 0.3.1; no los elimines por su nombre histórico.

Los binarios se distribuyen como assets de Release, fuera del historial Git. No publiques `analysis/`, `build/`, `dist/`, `.venv/`, capturas, configuraciones personales ni archivos del juego u otros bots. Usa exclusivamente la configuración pública de ejemplo y conserva los avisos y licencias de las dependencias incluidos por el constructor.

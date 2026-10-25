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

## Configuración de la versión 0.2.0

`app_version.py` es la versión compartida por la ventana, el constructor y el actualizador. La configuración de ejemplo mantiene AutoNexus en 90 %, la predicción de impactos activada y `detect_game_keys=true`. AutoAim comienza desactivado, con `mode="close_to_mouse"`, `activation="autofire"`, `extrapolation=302` y `mouse_radius=4`.

La lectura inicial de teclas consulta los valores guardados del juego. El puente de la sesión consulta `SettingsManager.GetKeyCode` para obtener la configuración efectiva, incluidos valores predeterminados y cambios personalizados. Una tecla Nexus reconocida se utiliza si está marcada **Leer teclas del juego**; la selección manual de Control izquierdo, Control derecho o letras queda como alternativa explícita cuando no se reconoce. AutoAim usa el estado de autofire del juego; no depende de una tecla fija ni de mantener una tecla adicional.

AutoAim selecciona el enemigo más cercano al cursor dentro del radio, expresado en unidades del mundo. La extrapolación sigue la fórmula investigada `posición del objetivo + coeficiente × velocidad`; la unidad temporal del coeficiente no está confirmada. El valor 0 conserva la posición actual. La modificación se limita al resultado utilizado por la llamada investigada de cámara/puntería de proyectiles, con AutoAim habilitado, modo Activo, autofire encendido y juego sin pausa. No mueve el cursor ni habilita autofire o ataques. La verificación de esa integración en combate sigue pendiente para este perfil.

La pestaña **Actualizaciones** consulta Releases públicas de GitHub. El inicio normal programa la primera consulta en segundo plano aproximadamente 1,5 segundos después de abrir la ventana; descargar e instalar requieren sus respectivos botones. Desde fuentes se pueden consultar novedades y abrir la descarga en GitHub. La sustitución y el reinicio desde la interfaz se ofrecen en el paquete portable, una vez detenida la sesión y confirmada su limpieza.

## Comprobar

```powershell
.\.venv\Scripts\python.exe -B -m unittest -q
node --test test_autoaim_agent.cjs
```

Las pruebas de interfaz usan un runtime falso. Las pruebas de reproducción usan el ejemplo público; no necesitan capturas personales ni que esté abierto el juego. No envían teclas.

## Generar el ZIP portable

Usa Python 3.12 x64 en Windows y el entorno virtual. Instala el conjunto de construcción fijado:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\build_portable.py --build-id mi-construccion
```

El script no instala software. Reserva directorios nuevos bajo `build/` y `dist/`; no sustituye construcciones previas. El resultado es `dist/mi-construccion/MiRealmBot-Windows-x64.zip`, con un archivo de sumas SHA256 y un manifiesto de los archivos incluidos.

Se usa PyInstaller en modo carpeta, con ventana y sin UPX. Los recursos viven en `_internal`; `autonexus.json` y los registros se guardan junto al ejecutable. Esta separación sigue la [documentación de rutas en ejecución de PyInstaller](https://pyinstaller.org/en/stable/runtime-information.html).

El constructor copia una lista explícita de código y recursos. La configuración sale de `autonexus.example.json`; nunca de la configuración de la máquina de desarrollo. No incluye `analysis/`, `.venv/`, archivos del juego ni de otro bot. Se incluyen las licencias de las dependencias instaladas y los textos que figuran en `packaging/`.

## Comprobar el ejecutable construido

Extrae el ZIP completo en otra carpeta. Ejecuta allí:

```powershell
.\MiRealmBot.exe --portable-self-test
```

Esta comprobación valida recursos, Frida, Tkinter, configuración y la demostración; no se adjunta al juego ni envía teclas. Después abre el ejecutable normalmente y revisa la interfaz. La compatibilidad real con el juego y con otra máquina es una comprobación distinta.

La comprobación portable y las pruebas con dobles no acreditan el funcionamiento de AutoAim en combate. Documenta por separado la evidencia del perfil, del punto de enganche de puntería y de cualquier prueba real que se haya realizado.

## Publicar una versión

Ejecuta las pruebas, construye y comprueba el ZIP antes de crear una Release. Publica `MiRealmBot-Windows-x64.zip` y `SHA256SUMS.txt` como assets, sin añadir ejecutables al historial Git. GitHub distingue los assets subidos de los archivos de código fuente: [documentación de Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

La versión 0.1.0 necesita instalar 0.2.0 manualmente para incorporar el actualizador. En las siguientes actualizaciones portables, el instalador conserva configuración, registros y copias de seguridad y mantiene la carpeta anterior completa como respaldo en una carpeta hermana. No incluye datos locales en el ZIP publicado. La instalación debe esperar al cierre del proceso de MiRealmBot y a la limpieza de la sesión con el juego.

Actualizar el juego requiere verificar el nuevo perfil y sus bindings. No cambies únicamente el hash para sortear la comprobación de compatibilidad.

Desde 0.2.1, las opciones de selección `autoaim.prioritize_bosses` y `autoaim.ignore_walls` son booleanos independientes, ambos `false` por defecto. La prioridad conserva el radio y la elegibilidad anteriores; descarta murallas antes de aplicar la prioridad y resuelve empates de prioridad por distancia al cursor. La clasificación de boss procede del marcador `ObjectProperties.HealthBarBossElement`, con cobertura limitada a los enemigos que lo tienen. La clasificación de murallas usa las clases de entidad `CaveWall`, `ConnectedWall`, `Wall` y `DoubleWall`; no deriva de vida, tamaño o nombre.

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

## Comprobar

```powershell
.\.venv\Scripts\python.exe -B -m unittest -q
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

## Publicar una versión

Ejecuta las pruebas, construye y comprueba el ZIP antes de crear una Release. Publica `MiRealmBot-Windows-x64.zip` y `SHA256SUMS.txt` como assets, sin añadir ejecutables al historial Git. GitHub distingue los assets subidos de los archivos de código fuente: [documentación de Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

Actualizar el juego requiere verificar el nuevo perfil y sus bindings. No cambies únicamente el hash para sortear la comprobación de compatibilidad.

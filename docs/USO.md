# Cómo usar MiRealmBot

## Descargar y abrir

1. Abre [la última versión](https://github.com/matihdz/MiRealmBot/releases/latest).
2. En **Assets**, descarga **MiRealmBot-Windows-x64.zip**.
3. Haz clic derecho sobre el ZIP y elige **Extraer todo**. Guarda la carpeta, por ejemplo, en el Escritorio.
4. Entra a la carpeta extraída **MiRealmBot** y abre **MiRealmBot.exe**.

Conserva toda la carpeta, incluido `_internal`. El ejecutable lleva Python, Frida y Tkinter; no necesitas instalarlos. La descarga **Source code (zip)** contiene el código para desarrollo y no es el programa preparado para usar.

## Conectarlo al juego

Esta primera versión requiere Windows de 64 bits y RotMG Exalt instalado mediante el launcher oficial. El perfil corresponde a una versión concreta de `GameAssembly.dll`. Steam y ubicaciones personalizadas no están admitidas en esta entrega.

1. Abre RotMG Exalt y deja al personaje en **Nexus**.
2. En los controles del juego, revisa la tecla para volver al Nexus.
3. En MiRealmBot abre **Juego en vivo** y elige **la misma tecla**. `CTRL` es Control izquierdo; también puedes elegir `R`, otra letra o Control derecho.
4. Para la primera comprobación selecciona **Observar** y pulsa **Iniciar observación**. Deben aparecer tu vida y el mapa. Este modo no envía teclas.
5. Pulsa **Detener** y espera a que la sesión quede cerrada.
6. Ajusta el porcentaje. El valor inicial es **90 %** para una prueba que solicite salir pronto, y la predicción de proyectiles está marcada. Puedes guardar tus parámetros con **Guardar parámetros**.
7. Selecciona **Activo** y pulsa **Iniciar AutoNexus activo**. El indicador debe mostrar **ACTIVO · PUEDE ENVIAR** la tecla elegida.
8. Vuelve a la ventana del juego y mantenla en primer plano mientras juegas.

Abrir MiRealmBot no inicia el bot. Al volver a abrir el programa tendrás que iniciar la sesión desde la interfaz. **Detener** retira la integración; para modificar parámetros durante una sesión, detén, cambia e inicia de nuevo.

## Leer el estado

- **Sólo observación**: recoge lecturas y decisiones sin enviar la tecla.
- **Tecla enviada**: Windows aceptó el envío; comprueba dentro del juego que llegaste al Nexus.
- **Tecla bloqueada**: el juego no tenía el foco o no estaba disponible. No se reintenta automáticamente en el mismo mapa; detén y vuelve a iniciar cuando puedas mantener el foco del juego.
- **El perfil no coincide**: el juego cambió. Hace falta una versión de MiRealmBot cuyo perfil se haya verificado para esa actualización; no cambies el hash manualmente.

La predicción reconoce la ruta de impactos de proyectil implementada. Terreno y daño continuo no están cubiertos. El umbral dispara estrictamente por debajo del límite calculado. El programa no garantiza supervivencia frente a todos los golpes.

## Probar parámetros sin jugar

La primera pestaña contiene una **demostración sintética** de dos golpes de 65. Puedes cambiar porcentaje y predicción, reproducir los eventos y comparar siete umbrales. La simulación no se conecta al juego ni envía teclas.

**Usar en vivo →** copia el borrador al panel del juego; no lo inicia ni lo guarda automáticamente.

## Si algo falla

Envía a quien te pasó el programa una captura del mensaje de error y el número de versión descargada. Los registros se guardan en `analysis`, junto al ejecutable. La configuración está en `autonexus.json`; las copias de seguridad terminan en `.bak`. Puedes conservar esos archivos al cambiar de versión.

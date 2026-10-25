# Cómo usar MiRealmBot

## Descargar y abrir

1. Abre [la última versión](https://github.com/matihdz/MiRealmBot/releases/latest).
2. En **Assets**, descarga **MiRealmBot-Windows-x64.zip**.
3. Haz clic derecho sobre el ZIP y elige **Extraer todo**. Guarda la carpeta, por ejemplo, en el Escritorio.
4. Entra a la carpeta extraída **MiRealmBot** y abre **MiRealmBot.exe**.

Conserva toda la carpeta, incluido `_internal`. El ejecutable lleva Python, Frida y Tkinter; no necesitas instalarlos. La descarga **Source code (zip)** contiene el código para desarrollo y no es el programa preparado para usar. La versión **0.2.0** incluye **AutoAim** y **Actualizaciones**.

## Conectarlo al juego

Requiere Windows de 64 bits y RotMG Exalt instalado mediante el launcher oficial. El perfil corresponde a una versión concreta de `GameAssembly.dll`. Steam y ubicaciones personalizadas no están admitidas.

1. Abre RotMG Exalt y deja al personaje en **Nexus**.
2. En los controles del juego, revisa la tecla para volver al Nexus.
3. En MiRealmBot abre **AutoAim** y deja marcada **Leer teclas del juego**. Puedes pulsar **Volver a leer teclas** para previsualizar lo guardado. Al iniciar la sesión se leen las teclas resueltas dentro del juego, incluidos sus valores predeterminados. Nexus espera esa lectura antes de actuar. Si la detección no se reconoce, detén la sesión, desmarca **Leer teclas del juego** y elige manualmente **la misma tecla** del juego. `CTRL` es Control izquierdo; también se admiten Control derecho y letras.
4. Abre **Juego en vivo**, selecciona **Observar** y pulsa **Iniciar observación**. Deben aparecer tu vida y el mapa. Este modo no envía teclas.
5. Pulsa **Detener** y espera a que la sesión quede cerrada.
6. Ajusta el porcentaje. El valor inicial es **90 %** para una prueba que solicite salir pronto, y la predicción de proyectiles está marcada. Puedes guardar tus parámetros con **Guardar parámetros**.
7. Selecciona **Activo** y pulsa **Iniciar AutoNexus activo**. El indicador debe mostrar **ACTIVO · PUEDE ENVIAR** la tecla elegida.
8. Vuelve a la ventana del juego y mantenla en primer plano mientras juegas.

Abrir MiRealmBot no inicia el bot. Al volver a abrir el programa tendrás que iniciar la sesión desde la interfaz. **Detener** retira la integración; para modificar parámetros durante una sesión, detén, cambia e inicia de nuevo.

## Usar AutoAim

1. Con la sesión detenida, abre **AutoAim** y marca **Activar AutoAim**.
2. Deja inicialmente **Extrapolation: 302** y **Radio junto al cursor: 4**.
3. Selecciona **Activo** en el panel izquierdo y pulsa **Iniciar sesión activa**. Es la misma sesión que usa AutoNexus.
4. Vuelve al juego, activa su autofire con tu tecla configurada y acerca el cursor al enemigo que quieres seleccionar.
5. Para dejar de apuntar automáticamente, apaga el autofire del juego. **Detener** en MiRealmBot cierra toda la sesión.

El modo disponible es **Close to Mouse**: selecciona el enemigo más cercano al cursor dentro del radio indicado, medido en unidades del mundo del juego. AutoAim empieza desactivado. Requiere modo Activo, autofire encendido y juego sin pausa; no hay que mantener una tecla adicional pulsada. En **Observar** calcula sin alterar los disparos.

**Extrapolation** controla cuánto se adelanta la puntería según el movimiento del enemigo: 0 usa su posición actual y un valor mayor adelanta más. La unidad temporal de este coeficiente no está confirmada. Los cambios se aplican al volver a iniciar la sesión; **Guardar parámetros** los conserva para la próxima vez.

En **0.2.1** puedes activar dos opciones independientes, desmarcadas por defecto:

- **Priorizar bosses**: si hay un boss reconocido y otros enemigos dentro del radio, elige al boss. Entre varios bosses elige el más cercano al cursor. Sin boss, mantiene el enemigo más cercano. Reconoce el marcador de boss de los datos del juego; un jefe sin ese marcador se trata como un enemigo normal.
- **Ignorar murallas**: excluye las murallas, incluidas las destructibles, como objetivos del AutoAim. Los proyectiles conservan sus colisiones habituales.

Para cambiarlas: **Detener → marcar las opciones → Guardar parámetros → Iniciar sesión activa**.

El puente modifica el punto de puntería de la ruta de proyectiles investigada. No mueve el cursor ni enciende autofire ni dispara por sí mismo. La prueba activa del perfil actual registró selección de enemigos y ajustes de puntería; el usuario confirmó que apunta correctamente. La precisión y la compatibilidad con otro equipo siguen pendientes de medición.

## Actualizar desde el programa

La versión 0.2.0 busca novedades en GitHub al abrirse. Puedes volver a comprobarlas en **Actualizaciones**:

1. Pulsa **Buscar actualizaciones**.
2. Si hay una versión nueva, pulsa **Descargar actualización** y espera a que termine.
3. Si tienes una sesión en vivo abierta, pulsa **Detener** y espera a que finalice su limpieza.
4. Pulsa **Instalar y reiniciar**. MiRealmBot se cerrará y abrirá la versión nueva.

Se conservan `autonexus.json`, los registros de `analysis` y sus copias de seguridad. La carpeta anterior completa queda junto a la instalación, con un nombre como `MiRealmBot.backup-…`. Después del reinicio debes iniciar la sesión en vivo de nuevo.

Si tienes **0.1.0**, descarga y extrae **0.2.0** manualmente siguiendo los primeros pasos de esta guía; 0.1.0 aún no incluye el actualizador. Con ambos programas cerrados puedes copiar tu `autonexus.json` y la carpeta `analysis` a la carpeta nueva. Desde 0.2.0 podrás usar **Actualizaciones** para las entregas siguientes.

Si ejecutas el código fuente, usa **Ver descarga en GitHub** para abrir la descarga manual. La instalación desde esta pestaña requiere el paquete portable.

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

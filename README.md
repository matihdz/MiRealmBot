# MiRealmBot

Cliente modular para **RotMG Exalt en Windows**. Configura AutoNexus, AutoAim y Debuffs desde una interfaz con ajustes que se guardan y aplican en vivo.

**[Descargar para Windows](https://github.com/matihdz/MiRealmBot/releases/latest)** · [Guía de uso](docs/USO.md) · [Desarrollo](docs/DESARROLLO.md)

## Funciones

| Módulo | Opciones |
| --- | --- |
| **AutoNexus** | Salida por porcentaje de vida, predicción de impactos de proyectil y detección de la tecla Nexus. |
| **AutoAim** | Close to Mouse, extrapolación, prioridad de bosses y exclusión de murallas como objetivos. |
| **Debuffs** | Protección para Confuse y Blind, protección experimental para Silenced y registro de Quiet. |

Puedes activar los módulos por separado. **Quiet sólo registra el efecto; su protección todavía no está disponible.** Silenced conserva el coste normal de maná y el enfriamiento de la habilidad.

## Inicio rápido

1. Descarga **MiRealmBot-Windows-x64.zip** desde [Releases](https://github.com/matihdz/MiRealmBot/releases/latest).
2. Extrae toda la carpeta y abre **MiRealmBot.exe**.
3. Abre RotMG Exalt con el launcher oficial y deja el personaje en Nexus.
4. Configura los módulos que quieras usar.
5. En **Juego en vivo**, pulsa **Iniciar bot** y vuelve al juego.

El portable incluye sus dependencias: no necesitas instalar Python. Conserva la carpeta `_internal` junto al ejecutable. La descarga **Source code** corresponde al entorno de desarrollo.

Abrir MiRealmBot deja el bot detenido. Los cambios válidos se guardan automáticamente y se aplican durante la sesión. **Detener** cierra la sesión y retira la integración.

## Compatibilidad

- Windows de 64 bits e instalación del juego mediante el launcher oficial.
- Perfil del juego compatible: MiRealmBot lo comprueba antes de conectar. Steam y rutas personalizadas no están admitidas.
- Juego en primer plano para que actúen las protecciones. AutoAim requiere autofire encendido y el juego sin pausa.

AutoNexus contempla la ruta de impactos de proyectil implementada; terreno y daño continuo quedan fuera de esa predicción. Una solicitud de salida no garantiza la llegada al Nexus.

## Actualizar y obtener ayuda

Desde **Actualizaciones**, busca y descarga la nueva versión. Detén el bot, espera su cierre y pulsa **Instalar y reiniciar**. La configuración se conserva.

La [guía de uso](docs/USO.md) explica los ajustes, los estados y los problemas frecuentes. Para informar un problema, abre un [issue](https://github.com/matihdz/MiRealmBot/issues) con la versión, los pasos para reproducirlo y el mensaje de error.

## Desarrollo

Consulta [Desarrollo y construcción](docs/DESARROLLO.md) para preparar el entorno, ejecutar las pruebas y generar el portable. Las novedades de cada versión están en [Releases](https://github.com/matihdz/MiRealmBot/releases).

El repositorio contiene el código propio y ejemplos sintéticos. Los archivos del juego, capturas personales y configuraciones locales quedan fuera de la distribución. Las dependencias incluidas en el portable se detallan en [los avisos de terceros](packaging/THIRD-PARTY-NOTICES.txt).

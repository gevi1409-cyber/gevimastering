# GeVi Mastering

Aplicación web local para Windows que masteriza una canción y exporta únicamente el
formato seleccionado: WAV, FLAC o MP3. Incluye comparación A/B sincronizada de un
minuto, ecualizador manual de 10 bandas, presets, objetivo de loudness y metadata
esencial: título, artista, álbum, número de pista, año, portada y género.

Los presets guardan la cadena completa: preamp, EQ, objetivo LUFS, true peak,
compresión, punto de inicio A/B y plantilla del nombre de archivo. Los presets
antiguos que sólo contienen las 10 bandas se migran automáticamente al cargarse.

El flujo de álbum permite cargar varios tracks, reutilizar portada y datos comunes,
editar título/número en una cola y exportar todo el lote ordenado por número de pista.
La carpeta de destino se elige con el selector nativo de Windows.
El nombre de cada archivo se construye con las piezas elegidas de la metadata
(pista, título, álbum y artista) y recuerda la plantilla para futuros álbumes.
La medición de consistencia compara el loudness de todos los tracks con el promedio
del álbum después de aplicar el mastering y reutiliza una caché local cuando el audio
y los ajustes no cambiaron. La exportación valida metadata y nombres, muestra avance
por track y puede cancelarse sin dejar archivos incompletos. Cada lote terminado queda
registrado en un historial local con sus archivos, formato y ajustes de mastering.

La sesión se guarda automáticamente y recupera tracks, portada, metadata, mastering
y salida al volver a abrir la aplicación. El historial permite ver los archivos,
abrir su carpeta y restaurar los ajustes de una entrega.

Los audios de la sesión activa se conservan hasta pulsar `Nuevo álbum / Limpiar`.
Los previews y temporales huérfanos se eliminan automáticamente. Esta rutina nunca
recorre `exports/` ni borra presets.

## Requisito

Instala FFmpeg y asegúrate de que `ffmpeg.exe` esté en el `PATH`, o colócalo en
`tools/ffmpeg.exe` dentro de este proyecto.

## Ejecutar

```powershell
python web_app.py
```

En Windows abre `iniciar.bat` con doble clic. Se iniciará el servidor local y la
interfaz se abrirá automáticamente en el navegador. Ningún archivo sale del equipo.

## Distribuir sin instalar Python

El paquete para usuarios finales incluye Python, la interfaz, FFmpeg y FFprobe. Para
generarlo desde una máquina de desarrollo con Windows y Python 3.14:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_release.ps1
```

El script descarga FFmpeg, crea un entorno aislado con PyInstaller y produce
`release\GeViMastering-portable.zip`. Si Inno Setup 6 está instalado también genera
`release\GeViMastering-Setup.exe`. El usuario final sólo necesita descomprimir el ZIP
y abrir `GeViMastering.exe`, o ejecutar el instalador; no necesita Python ni FFmpeg.
La versión empaquetada se abre en una ventana nativa de Windows y mantiene una sola
instancia activa; un segundo lanzamiento enfoca la ventana existente.

En la versión empaquetada, configuración, historial y caché se guardan en
`%LOCALAPPDATA%\GeViMastering`, y los masters se exportan por defecto a
`Documentos\GeViMastering\exports`.

La aplicación no sube audio ni metadata a internet. Los presets se guardan
localmente dentro de `.suno-mastering/`.

## Formatos

- WAV: PCM de 24 bits, con metadata textual cuando el reproductor la reconoce.
- FLAC: 24 bits, etiquetas Vorbis y portada incrustada.
- MP3: 320 kbps, etiquetas ID3v2.3 y portada incrustada.

Aunque el archivo lleve etiquetas, distribuidores como DistroKid normalmente
solicitan nuevamente artista, título, álbum y portada durante la publicación.

# 🎮 GTA 6 Bot — Shorts y videos largos automáticos

Bot que funciona solo en **GitHub Actions** (gratis) y crea videos sobre GTA 6:

| Tipo | Formato | Cuándo | Qué hace |
|---|---|---|---|
| **Shorts** | Vertical 9:16, ~40 s | 3 veces al día | Toma la noticia más caliente de GTA 6, escribe un guion, le pone voz, subtítulos animados y lo sube a YouTube |
| **Videos largos** | Horizontal 16:9, 6–10 min | Mar, jue y sáb | Rota entre **Trucos, Secretos/easter eggs, Consejos y Resumen semanal**. Junta varias guías publicadas, arma un video por secciones con capítulos y miniatura |

Todo con herramientas gratis: guiones con **GitHub Models** (IA incluida en tu cuenta de GitHub), voz neural con **edge-tts**, video con **ffmpeg**.

Cada video también queda en **Releases** del repo y (opcional) te llega por **Telegram** al celular, con el título y la descripción listos para pegarlo en TikTok o Reels.

---

## ⚠️ Lee esto primero (3 cosas importantes)

1. **YouTube bloquea en "privado" los videos subidos por API hasta que apruebe tu proyecto.** Es una regla de Google para todos los proyectos nuevos. Hay que pedir la auditoría (formulario gratis, paso 4.6). Mientras la aprueban, usa los videos de Telegram/Releases y súbelos a mano (te toma 30 segundos desde el celular).
2. **Los trucos nunca se inventan.** La IA solo puede usar lo que aparece en artículos publicados, y cita las fuentes en la descripción. Los primeros días después del lanzamiento habrá pocas guías; si no hay suficientes fuentes, el bot cambia de tema solo.
3. **YouTube no monetiza canales "en serie" sin valor propio.** Para que el canal sea monetizable, revisa de vez en cuando los guiones, mete tus propios clips de gameplay (paso 6) y responde comentarios. Los videos ya dicen que la voz es sintética y que el canal no está afiliado a Rockstar.

---

## 1. Crear el repositorio

1. Entra a <https://github.com/new>, ponle un nombre (ej. `gta6-bot`) y créalo (puede ser **privado**).
2. Sube todos los archivos de esta carpeta (botón **Add file → Upload files**, arrastra todo, incluida la carpeta `.github`).
   - Si `.github` no se ve en tu explorador, actívalo en "mostrar archivos ocultos".
3. Ve a **Settings → Actions → General → Workflow permissions** y marca **Read and write permissions**. Guarda.

> Con eso el bot ya puede crear videos y dejarlos en **Releases**. Lo siguiente conecta YouTube.

## 2. Probar sin publicar

**Actions → GTA 6 Shorts automáticos → Run workflow**, marca **Solo generar** y dale correr. En ~5 min verás el video en la sección **Artifacts** de esa ejecución.

## 3. (Opcional, recomendado) Claves gratis extra

En **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Para qué | Dónde se saca |
|---|---|---|
| `PEXELS_API_KEY` | Fondos de video reales (Miami, autos, neón) en vez del fondo animado | <https://www.pexels.com/api/> (gratis) |
| `GEMINI_API_KEY` | IA de respaldo si GitHub Models falla | <https://aistudio.google.com/apikey> (gratis) |
| `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` | Que te llegue cada video al celular | Ver paso 5 |

## 4. Conectar YouTube (una sola vez, ~15 min)

1. Entra a <https://console.cloud.google.com/>, crea un proyecto nuevo.
2. **APIs y servicios → Biblioteca** → busca **YouTube Data API v3** → **Habilitar**.
3. **APIs y servicios → Pantalla de consentimiento OAuth**: tipo **Externo**, llena nombre y correo. En "Usuarios de prueba" agrega tu correo.
   Luego pulsa **Publicar app / Pasar a producción** (si la dejas en "Prueba", el permiso se vence cada 7 días).
4. **Credenciales → Crear credenciales → ID de cliente OAuth → App de escritorio**. Descarga el JSON y guárdalo en la carpeta del repo en tu PC con un nombre que empiece por `client_secret`.
5. En tu PC (con Python instalado), dentro de la carpeta:
   ```
   pip install google-auth-oauthlib
   python scripts/get_youtube_token.py
   ```
   Se abre el navegador → entra con la cuenta del canal → si dice "app no verificada", pulsa *Configuración avanzada → Ir a…*. Copia los 3 valores que salen (`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`) como **Secrets** en GitHub.
   ⚠️ **No subas el archivo `client_secret…json` a GitHub** (el `.gitignore` ya lo excluye).
6. Pide la auditoría para poder publicar en público: <https://support.google.com/youtube/contact/yt_api_form> (describe que es una herramienta interna que sube videos a tu propio canal).
7. Para miniaturas en videos largos, verifica tu canal por teléfono: <https://www.youtube.com/verify>.

## 5. Telegram (para TikTok/Reels a mano)

1. En Telegram habla con **@BotFather** → `/newbot` → copia el token → secret `TELEGRAM_BOT_TOKEN`.
2. Escríbele cualquier cosa a tu bot, luego abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia el número de `"chat":{"id": ...}` → secret `TELEGRAM_CHAT_ID`.

Los Shorts te llegan como video con el caption listo; los largos (muy pesados para Telegram) te llegan como miniatura + enlace.

## 6. Mejorar la calidad (cuando tengas 5 minutos)

- **Tus clips de gameplay**: pon `.mp4` en `assets/backgrounds/` (graba con el botón Share/Create de tu consola). Si hay clips ahí, el bot los usa en vez de Pexels. Es lo que más ayuda a que el canal se vea propio.
- **Música**: pon `.mp3` libres de derechos en `assets/music/` (la Biblioteca de audio de YouTube Studio es gratis y segura). Nunca música comercial.
- **Personaliza** `config.yaml`: nombre del canal, colores, horarios, temas de los videos largos.

## 🎙️ Cambiar la voz

1. **Escúchalas:** Actions → **Probar voces** → Run workflow. En ~2 min descarga el artefacto `muestras-de-voz` (un MP3 por estilo).
2. **Elige:** en `config.yaml` cambia `voice → preset` por el nombre que te guste:

| Preset | Cómo suena | Ideal para |
|---|---|---|
| `trailer` (por defecto) | Narrador grave con eco corto, tipo tráiler de cine | Shorts sobre clips del juego, noticias grandes |
| `hype` | YouTuber gamer rápido, brillante y al frente | Trucos, consejos, top 10 |
| `parcero` | Acento colombiano con energía | Si quieres sonar local |
| `misterio` | Muy grave con eco | Secretos, easter eggs |
| `locutora` | Voz femenina enérgica | Variar el canal |

Los videos largos usan su propia voz por tema (`voice_preset` en cada tema): trucos → `hype`, secretos → `misterio`.

**Sonido del juego:** si pones tus clips en `assets/backgrounds/`, su sonido (disparos, motores, ambiente) se mezcla debajo de la voz y **baja solo cada vez que la voz habla** (`game_audio_volume` en `config.yaml`; 0 para apagarlo).
⚠️ Graba tus clips con la **radio del juego apagada** (o el volumen de música en 0 en los ajustes de audio): las emisoras de GTA tienen canciones con licencia que YouTube detecta con Content ID y te reclama el video.

## 🎥 Clips desde directos (tú mandas el link)

1. En el repo ve a **Issues → New issue → "🎥 Sacar clips de un directo"** (funciona igual desde la app de GitHub en el celular).
2. Pega el link (Twitch, Kick, YouTube…). Opcional: desde qué minuto y cuántos minutos analizar.
3. Marca **#mio** solo si el directo es tuyo o tienes permiso del streamer.
4. Crea el Issue. El bot responde "Recibido ✅" y, cuando termina (20-60 min), te deja ahí mismo los clips y cierra el Issue.

Qué hace: descarga el tramo (si está **en vivo**, graba 45 min), lo transcribe con Whisper, busca los momentos con más volumen y reacciones ("no puede ser", risas, gritos), la IA elige los mejores 4 y les pone título, y los recorta en vertical con subtítulos y el crédito del streamer.

- **Con #mio:** se suben solos a YouTube como Shorts.
- **Sin #mio:** quedan en Releases/Telegram para que los revises. Subir clips de otros sin permiso trae reclamos de copyright y strikes; lo más seguro es pedirle permiso al streamer (muchos lo dan a cambio del crédito).
- YouTube a veces bloquea las descargas desde servidores de GitHub; Twitch y Kick funcionan mejor.

También puedes lanzarlo desde **Actions → Clips de directos → Run workflow**.

## Horarios

| Workflow | Horario (hora Colombia) | Archivo |
|---|---|---|
| Shorts | 8:17, 13:17 y 19:17 todos los días | `.github/workflows/shorts.yml` |
| Largos | 15:37 martes, jueves y sábado | `.github/workflows/largos.yml` |

Puedes lanzar cualquiera a mano desde **Actions → Run workflow** (en los largos puedes elegir tema).

## Costos

$0. GitHub da 2.000 min/mes de Actions en repos privados (este bot usa ~700) y es ilimitado si el repo es público. GitHub Models, edge-tts, Pexels, Gemini y Telegram tienen planes gratis suficientes.

## Estructura

```
bot/news.py       noticias de Google News, Bing y Reddit; evita repetir temas
bot/script.py     guion de Shorts con IA (GitHub Models → Gemini → plantilla)
bot/longform.py   videos largos por secciones, capítulos y miniatura
bot/tts.py        voz (edge-tts, respaldo espeak-ng) con tiempos por palabra
bot/visuals.py    fondos (tus clips → Pexels → animado) y tarjetas de texto
bot/render.py     subtítulos karaoke + montaje final con ffmpeg
bot/publish.py    YouTube, Telegram
data/published.json  historial (no borrar: evita repetir noticias)
```

Prueba local: `pip install -r requirements.txt` y `python -m bot.main --sample --dry-run` (Short) o `python -m bot.main --long --dry-run` (largo).

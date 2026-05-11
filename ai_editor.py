"""
ai_editor.py
Ejecuta las acciones de edición sobre el video usando MoviePy y pydub.
Compatibilidad: moviepy==1.0.3, Railway/Linux (sin ImageMagick obligatorio).
"""

from faster_whisper import WhisperModel
import os
import tempfile
import numpy as np

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    concatenate_videoclips,
    CompositeVideoClip,
    TextClip,
)
from moviepy.video.fx.all import blackwhite as bw_fx, speedx, fadein, fadeout
from moviepy.audio.fx.all  import audio_fadein, audio_fadeout, audio_loop
from moviepy.audio.AudioClip import CompositeAudioClip
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

# ── ImageMagick solo si está disponible (subtítulos) ─────────────────────
_MAGICK = os.environ.get("IMAGEMAGICK_BINARY", "")
if _MAGICK:
    from moviepy.config import change_settings
    change_settings({"IMAGEMAGICK_BINARY": _MAGICK})


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def procesar_video(ruta: str, acciones: dict, ruta_salida: str = "uploads/video_editado.mp4") -> str:
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No se encontró el archivo de video: {ruta}")

    # Normalizar valores
    if acciones.get("fade_in")  is None: acciones["fade_in"]  = 1.0
    if acciones.get("fade_out") is None: acciones["fade_out"] = 1.0
    if acciones.get("speed")  in (None, 1.0):       acciones["speed"]  = None
    if acciones.get("volume") in (None, 1.0):        acciones["volume"] = None
    if acciones.get("zoom")   in (None, False, 1.0): acciones["zoom"]   = None
    if acciones.get("blackwhite") and (
        not isinstance(acciones["blackwhite"], (list, tuple)) or
        None in acciones["blackwhite"] or
        len(acciones["blackwhite"]) != 2
    ):
        acciones["blackwhite"] = None

    video = VideoFileClip(ruta)
    print(f"📹 Video cargado: {video.duration:.1f}s — {video.w}x{video.h}px")

    # ── 1. Recortar duración ─────────────────────────────────────────────
    if acciones.get("duration"):
        limite = min(float(acciones["duration"]), video.duration)
        video  = video.subclip(0, limite)
        print(f"✂️  Recortado a {limite}s")

    # ── 2. Cambiar velocidad ─────────────────────────────────────────────
    if acciones.get("speed"):
        video = cambiar_velocidad(video, acciones["speed"])

    # ── 3. Eliminar silencios ────────────────────────────────────────────
    if acciones.get("remove_silence"):
        video = eliminar_silencios(video)

    # ── 4. Insertar meme ─────────────────────────────────────────────────
    if acciones.get("meme"):
        cfg   = acciones["meme"]
        video = insertar_meme(video, path=cfg.get("path"), tiempo=cfg.get("time", 0))

    # ── 5. Blanco y negro en segmento ────────────────────────────────────
    if acciones.get("blackwhite") and None not in acciones["blackwhite"]:
        inicio, fin = acciones["blackwhite"]
        video = aplicar_blanco_y_negro(video, inicio, fin)

    # ── 6. Zoom progresivo ───────────────────────────────────────────────
    if acciones.get("zoom"):
        escala_max = acciones["zoom"] if isinstance(acciones["zoom"], float) else 1.3
        video = aplicar_zoom_progresivo(video, escala_max)

    # ── 7. Volumen ───────────────────────────────────────────────────────
    if acciones.get("volume"):
        video = ajustar_volumen(video, acciones["volume"])

    # ── 8. Subtítulos ────────────────────────────────────────────────────
    if acciones.get("subtitles"):
        video = agregar_subtitulos(video)

    # ── 9. Fade in / fade out ────────────────────────────────────────────
    video = aplicar_fade(video,
        fade_in=acciones.get("fade_in",  1.0),
        fade_out=acciones.get("fade_out", 1.0),
    )

    # ── 10. Música de fondo ──────────────────────────────────────────────
    if acciones.get("music_path"):
        video = agregar_musica(video, acciones["music_path"], volumen=acciones.get("music_volume", 0.3))

    # ── 11. Highlights ───────────────────────────────────────────────────
    if acciones.get("highlights"):
        video = detectar_highlights(video, duracion_total=acciones.get("highlights_duration") or 30.0)

    # ── Guardar ──────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(ruta_salida) or "uploads", exist_ok=True)
    video.write_videofile(
        ruta_salida,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="uploads/temp_audio_out.m4a",
        remove_temp=True,
        logger=None,
    )
    video.close()
    print(f"✅ Video guardado en: {ruta_salida}")
    return ruta_salida


# ============================================================================
# FUNCIONES DE EDICIÓN
# ============================================================================

def cambiar_velocidad(video: VideoFileClip, factor: float) -> VideoFileClip:
    if not (0.1 <= factor <= 10):
        raise ValueError(f"Factor de velocidad inválido: {factor}")
    resultado = speedx(video, factor)
    print(f"⚡ Velocidad ajustada a {factor}x")
    return resultado


def eliminar_silencios(
    video: VideoFileClip,
    min_silence_len: int = 700,
    silence_thresh: int = -38,
    padding_ms: int = 150,
) -> VideoFileClip:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        audio     = AudioSegment.from_wav(ruta_wav)
        segmentos = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
        if not segmentos:
            print("⚠️  No se detectaron segmentos con audio.")
            return video
        duracion_ms = len(audio)
        clips = []
        for s, e in segmentos:
            s = max(0, s - padding_ms)
            e = min(duracion_ms, e + padding_ms)
            clips.append(video.subclip(s / 1000, e / 1000))
        resultado = concatenate_videoclips(clips)
        print(f"🔇 Silencios eliminados: {len(clips)} segmento(s).")
        return resultado
    finally:
        if os.path.exists(ruta_wav):
            os.remove(ruta_wav)


def aplicar_blanco_y_negro(video: VideoFileClip, inicio: float, fin: float) -> VideoFileClip:
    duracion = video.duration
    inicio   = max(0.0, float(inicio))
    fin      = min(float(fin), duracion)
    if inicio >= fin:
        print("⚠️  Rango B&N inválido. Se omite.")
        return video
    antes  = video.subclip(0, inicio)       if inicio > 0   else None
    medio  = bw_fx(video.subclip(inicio, fin))
    despues= video.subclip(fin, duracion)   if fin < duracion else None
    partes = [p for p in [antes, medio, despues] if p is not None]
    resultado = concatenate_videoclips(partes) if len(partes) > 1 else partes[0]
    print(f"🎞️  B&N aplicado: {inicio}s → {fin}s")
    return resultado


def aplicar_zoom_progresivo(video: VideoFileClip, escala_max: float = 1.3) -> VideoFileClip:
    def zoom(t):
        factor = 1 + (escala_max - 1) * (t / video.duration)
        return factor
    resultado = video.resize(zoom)
    resultado.fps = video.fps
    print(f"🔍 Zoom progresivo aplicado (hasta {escala_max}x)")
    return resultado


def ajustar_volumen(video: VideoFileClip, factor: float) -> VideoFileClip:
    if factor < 0:
        raise ValueError("El factor de volumen no puede ser negativo.")
    if video.audio is None:
        print("⚠️  El video no tiene audio.")
        return video
    resultado = video.volumex(factor)
    print(f"🔊 Volumen ajustado a {factor}x")
    return resultado


def insertar_meme(video: VideoFileClip, path: str, tiempo: float) -> VideoFileClip:
    if not path or not os.path.exists(path):
        print(f"⚠️  Meme no encontrado: '{path}'. Se omite.")
        return video
    if tiempo < 0 or tiempo >= video.duration:
        print(f"⚠️  Tiempo {tiempo}s fuera de rango. Se omite.")
        return video
    try:
        meme = VideoFileClip(path)
    except Exception as e:
        print(f"⚠️  No se pudo cargar el meme: {e}.")
        return video
    if meme.size != video.size:
        meme = meme.resize(video.size)
    partes    = [video.subclip(0, tiempo), meme, video.subclip(tiempo, video.duration)]
    resultado = concatenate_videoclips(partes)
    print(f"😂 Meme insertado en {tiempo}s")
    return resultado


def agregar_subtitulos(video: VideoFileClip) -> VideoFileClip:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        print("🎙️  Transcribiendo con Whisper...")
        model    = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(ruta_wav, language="es")
        subs     = [(seg.start, seg.end, seg.text.strip()) for seg in segments]
        if not subs:
            print("⚠️  Sin texto para subtitular.")
            return video
        sub_clips = []
        for start, end, txt in subs:
            clip = (
                TextClip(txt, fontsize=38, color="white", stroke_color="black",
                         stroke_width=1.5, method="caption",
                         size=(int(video.w * 0.85), None), font="DejaVu-Sans")
                .set_position(("center", 0.82), relative=True)
                .set_start(start).set_end(end)
            )
            sub_clips.append(clip)
        resultado = CompositeVideoClip([video] + sub_clips).set_audio(video.audio)
        resultado.fps = video.fps
        print(f"💬 Subtítulos: {len(subs)} segmento(s).")
        return resultado
    finally:
        if os.path.exists(ruta_wav):
            os.remove(ruta_wav)


def aplicar_fade(video: VideoFileClip, fade_in: float = 1.0, fade_out: float = 1.0) -> VideoFileClip:
    if fade_in > 0:
        video = fadein(video, fade_in)
        if video.audio:
            video = video.set_audio(audio_fadein(video.audio, fade_in))
    if fade_out > 0:
        video = fadeout(video, fade_out)
        if video.audio:
            video = video.set_audio(audio_fadeout(video.audio, fade_out))
    print(f"🎬 Fade: in={fade_in}s, out={fade_out}s")
    return video


def agregar_musica(video: VideoFileClip, ruta_musica: str, volumen: float = 0.3) -> VideoFileClip:
    if not os.path.exists(ruta_musica):
        print(f"⚠️  Música no encontrada: {ruta_musica}.")
        return video
    musica = AudioFileClip(ruta_musica).volumex(volumen)
    if musica.duration < video.duration:
        musica = audio_loop(musica, duration=video.duration)
    else:
        musica = musica.subclip(0, video.duration)
    audio_final = CompositeAudioClip([video.audio, musica]) if video.audio else musica
    resultado   = video.set_audio(audio_final)
    resultado.fps = video.fps
    print(f"🎵 Música agregada al {int(volumen*100)}%")
    return resultado


def detectar_highlights(video: VideoFileClip, duracion_total: float = 30.0, ventana: float = 3.0) -> VideoFileClip:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        audio      = AudioSegment.from_wav(ruta_wav)
        ventana_ms = int(ventana * 1000)
        energias   = []
        for i in range(0, len(audio) - ventana_ms, ventana_ms // 2):
            energias.append((audio[i:i + ventana_ms].rms, i / 1000))
        if not energias:
            return video
        energias.sort(reverse=True)
        n        = max(1, int(duracion_total / ventana))
        mejores  = sorted(energias[:n], key=lambda x: x[1])
        clips    = []
        ultimo   = -ventana
        for _, inicio in mejores:
            if inicio >= ultimo:
                fin = min(inicio + ventana, video.duration)
                clips.append(video.subclip(inicio, fin))
                ultimo = fin
        if not clips:
            return video
        resultado = concatenate_videoclips(clips)
        print(f"⭐ Highlights: {len(clips)} segmento(s) — {resultado.duration:.1f}s")
        return resultado
    finally:
        if os.path.exists(ruta_wav):
            os.remove(ruta_wav)

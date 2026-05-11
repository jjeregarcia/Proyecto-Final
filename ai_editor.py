"""
ai_editor.py - Robusto para Railway/Linux. Maneja videos con y sin audio.
"""
import os
import tempfile
import subprocess
import shutil
import numpy as np
from faster_whisper import WhisperModel
from moviepy.editor import (
    VideoFileClip, AudioFileClip,
    concatenate_videoclips, CompositeVideoClip, TextClip,
)
from moviepy.video.fx.all import blackwhite as bw_fx, speedx, fadein, fadeout
from moviepy.audio.fx.all  import audio_fadein, audio_fadeout, audio_loop
from moviepy.audio.AudioClip import CompositeAudioClip
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

_MAGICK = os.environ.get("IMAGEMAGICK_BINARY", "")
if _MAGICK:
    from moviepy.config import change_settings
    change_settings({"IMAGEMAGICK_BINARY": _MAGICK})


# ── Excepción personalizada ───────────────────────────────────────────────
class VideoProcessingError(Exception):
    pass


# ── Helper: verificar si el video tiene audio ────────────────────────────
def _tiene_audio(ruta: str) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", ruta],
            capture_output=True, text=True, timeout=15
        )
        return "audio" in result.stdout
    except Exception:
        return False


# ── Helper: reparar video con ffmpeg ─────────────────────────────────────
def _reparar_video(ruta_in: str, ruta_out: str) -> str:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", ruta_in, "-c", "copy", ruta_out],
            capture_output=True, timeout=60
        )
        if os.path.exists(ruta_out) and os.path.getsize(ruta_out) > 0:
            print("🔧 Video reparado OK")
            return ruta_out
    except Exception:
        pass
    return ruta_in


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def procesar_video(ruta: str, acciones: dict, ruta_salida: str = "uploads/video_editado.mp4") -> str:
    if not os.path.exists(ruta):
        raise VideoProcessingError("No se encontró el archivo de video. Por favor subilo nuevamente.")

    # Reparar video antes de procesar
    ruta_rep = ruta.replace("_input.", "_repaired.")
    ruta = _reparar_video(ruta, ruta_rep)

    # Detectar audio
    tiene_audio = _tiene_audio(ruta)

    # Normalizar acciones
    if acciones.get("fade_in")  is None: acciones["fade_in"]  = 0
    if acciones.get("fade_out") is None: acciones["fade_out"] = 0
    if acciones.get("speed")  in (None, 1.0):       acciones["speed"]  = None
    if acciones.get("volume") in (None, 1.0):        acciones["volume"] = None
    if acciones.get("zoom")   in (None, False, 1.0): acciones["zoom"]   = None
    if acciones.get("blackwhite") and (
        not isinstance(acciones["blackwhite"], (list, tuple)) or
        None in acciones["blackwhite"] or len(acciones["blackwhite"]) != 2
    ):
        acciones["blackwhite"] = None

    try:
        video = VideoFileClip(ruta)
    except Exception as e:
        raise VideoProcessingError(f"No se pudo abrir el video. Asegurate de que sea un formato válido (mp4, mov, avi).")

    print(f"📹 Duración: {video.duration:.1f}s")

    try:
        # ── 1. Recortar duración ─────────────────────────────────────────
        if acciones.get("duration"):
            limite = min(float(acciones["duration"]), video.duration)
            video  = video.subclip(0, limite)

        # ── 2. Velocidad ─────────────────────────────────────────────────
        if acciones.get("speed"):
            factor = float(acciones["speed"])
            if not (0.1 <= factor <= 10):
                raise VideoProcessingError(f"La velocidad {factor}x está fuera del rango permitido (0.1x a 10x).")
            video = speedx(video, factor)

        # ── 3. Eliminar silencios ─────────────────────────────────────────
        if acciones.get("remove_silence"):
            if not tiene_audio:
                print("⚠️  Sin audio: se omite eliminación de silencios.")
            else:
                video = _eliminar_silencios(video)

        # ── 4. Blanco y negro ─────────────────────────────────────────────
        if acciones.get("blackwhite") and None not in acciones["blackwhite"]:
            video = _blanco_y_negro(video, *acciones["blackwhite"])

        # ── 5. Zoom ───────────────────────────────────────────────────────
        if acciones.get("zoom"):
            escala = float(acciones["zoom"]) if isinstance(acciones["zoom"], (int, float)) else 1.3
            if escala > 1.0:
                video = _zoom_progresivo(video, escala)

        # ── 6. Volumen ────────────────────────────────────────────────────
        if acciones.get("volume"):
            if not tiene_audio:
                print("⚠️  Sin audio: se omite ajuste de volumen.")
            else:
                video = video.volumex(float(acciones["volume"]))

        # ── 7. Subtítulos ─────────────────────────────────────────────────
        if acciones.get("subtitles"):
            if not tiene_audio:
                print("⚠️  Sin audio: no se pueden generar subtítulos.")
            else:
                video = _agregar_subtitulos(video)

        # ── 8. Fade in / out ──────────────────────────────────────────────
        if acciones.get("fade_in", 0) > 0:
            video = fadein(video, acciones["fade_in"])
            if video.audio:
                video = video.set_audio(audio_fadein(video.audio, acciones["fade_in"]))

        if acciones.get("fade_out", 0) > 0:
            video = fadeout(video, acciones["fade_out"])
            if video.audio:
                video = video.set_audio(audio_fadeout(video.audio, acciones["fade_out"]))

        # ── 9. Música de fondo ────────────────────────────────────────────
        if acciones.get("music_path"):
            video = _agregar_musica(video, acciones["music_path"],
                                    float(acciones.get("music_volume") or 0.3),
                                    tiene_audio)

        # ── 10. Highlights ────────────────────────────────────────────────
        if acciones.get("highlights"):
            if not tiene_audio:
                print("⚠️  Sin audio: highlights no disponible, se usa el video completo.")
            else:
                video = _detectar_highlights(video, float(acciones.get("highlights_duration") or 30.0))

        # ── Guardar ───────────────────────────────────────────────────────
        os.makedirs(os.path.dirname(ruta_salida) or "uploads", exist_ok=True)
        video.write_videofile(
            ruta_salida,
            codec="libx264",
            audio_codec="aac" if video.audio else None,
            temp_audiofile="uploads/temp_audio_out.m4a" if video.audio else None,
            remove_temp=True,
            logger=None,
        )
        video.close()
        print(f"✅ Guardado: {ruta_salida}")
        return ruta_salida

    except VideoProcessingError:
        raise
    except Exception as e:
        raise VideoProcessingError(f"Ocurrió un error al procesar el video: {str(e)[:120]}")


# ============================================================================
# FUNCIONES INTERNAS
# ============================================================================

def _eliminar_silencios(video, min_silence_len=700, silence_thresh=-38, padding_ms=150):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        audio = AudioSegment.from_wav(ruta_wav)
        segs  = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
        if not segs:
            return video
        dur = len(audio)
        clips = [video.subclip(max(0, s-padding_ms)/1000, min(dur, e+padding_ms)/1000) for s, e in segs]
        return concatenate_videoclips(clips)
    except Exception:
        return video
    finally:
        if os.path.exists(ruta_wav): os.remove(ruta_wav)


def _blanco_y_negro(video, inicio, fin):
    d = video.duration
    inicio = max(0.0, float(inicio))
    fin    = min(float(fin), d)
    if inicio >= fin: return video
    try:
        partes = []
        if inicio > 0:  partes.append(video.subclip(0, inicio))
        partes.append(bw_fx(video.subclip(inicio, fin)))
        if fin < d:     partes.append(video.subclip(fin, d))
        return concatenate_videoclips(partes) if len(partes) > 1 else partes[0]
    except Exception:
        return video


def _zoom_progresivo(video, escala_max=1.3):
    try:
        resultado = video.resize(lambda t: 1 + (escala_max - 1) * (t / video.duration))
        resultado.fps = video.fps
        return resultado
    except Exception:
        return video


def _agregar_subtitulos(video):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(ruta_wav, language="es")
        subs = [(s.start, s.end, s.text.strip()) for s in segments]
        if not subs: return video
        sub_clips = []
        for start, end, txt in subs:
            try:
                clip = (TextClip(txt, fontsize=38, color="white", stroke_color="black",
                            stroke_width=1.5, method="caption",
                            size=(int(video.w * 0.85), None), font="DejaVu-Sans")
                        .set_position(("center", 0.82), relative=True)
                        .set_start(start).set_end(end))
                sub_clips.append(clip)
            except Exception:
                continue
        if not sub_clips: return video
        resultado = CompositeVideoClip([video] + sub_clips).set_audio(video.audio)
        resultado.fps = video.fps
        return resultado
    except Exception:
        return video
    finally:
        if os.path.exists(ruta_wav): os.remove(ruta_wav)


def _agregar_musica(video, ruta_musica, volumen=0.3, tiene_audio=True):
    if not os.path.exists(ruta_musica):
        print("⚠️  Archivo de música no encontrado, se omite.")
        return video
    try:
        musica = AudioFileClip(ruta_musica).volumex(volumen)
        if musica.duration < video.duration:
            musica = audio_loop(musica, duration=video.duration)
        else:
            musica = musica.subclip(0, video.duration)

        if tiene_audio and video.audio:
            audio_final = CompositeAudioClip([video.audio, musica])
        else:
            audio_final = musica

        resultado = video.set_audio(audio_final)
        resultado.fps = video.fps
        return resultado
    except Exception as e:
        print(f"⚠️  Error al agregar música: {e}. Se omite.")
        return video


def _detectar_highlights(video, duracion_total=30.0, ventana=3.0):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        audio = AudioSegment.from_wav(ruta_wav)
        vm = int(ventana * 1000)
        if len(audio) < vm:
            return video
        energias = [(audio[i:i+vm].rms, i/1000) for i in range(0, len(audio)-vm, vm//2)]
        if not energias: return video
        energias.sort(reverse=True)
        mejores = sorted(energias[:max(1, int(duracion_total/ventana))], key=lambda x: x[1])
        clips, ultimo = [], -ventana
        for _, inicio in mejores:
            if inicio >= ultimo:
                fin = min(inicio + ventana, video.duration)
                clips.append(video.subclip(inicio, fin))
                ultimo = fin
        return concatenate_videoclips(clips) if clips else video
    except Exception:
        return video
    finally:
        if os.path.exists(ruta_wav): os.remove(ruta_wav)

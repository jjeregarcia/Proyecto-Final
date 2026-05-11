"""
ai_editor.py - Compatible con Python 3.11 y Railway/Linux.
"""
from faster_whisper import WhisperModel
import os, tempfile
import numpy as np
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


def procesar_video(ruta: str, acciones: dict, ruta_salida: str = "uploads/video_editado.mp4") -> str:
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No se encontró: {ruta}")

    if acciones.get("fade_in")  is None: acciones["fade_in"]  = 1.0
    if acciones.get("fade_out") is None: acciones["fade_out"] = 1.0
    if acciones.get("speed")  in (None, 1.0):       acciones["speed"]  = None
    if acciones.get("volume") in (None, 1.0):        acciones["volume"] = None
    if acciones.get("zoom")   in (None, False, 1.0): acciones["zoom"]   = None
    if acciones.get("blackwhite") and (
        not isinstance(acciones["blackwhite"], (list, tuple)) or
        None in acciones["blackwhite"] or len(acciones["blackwhite"]) != 2
    ):
        acciones["blackwhite"] = None

    video = VideoFileClip(ruta)
    print(f"📹 {video.duration:.1f}s — {video.w}x{video.h}px")

    if acciones.get("duration"):
        limite = min(float(acciones["duration"]), video.duration)
        video = video.subclip(0, limite)

    if acciones.get("speed"):
        video = cambiar_velocidad(video, acciones["speed"])

    if acciones.get("remove_silence"):
        video = eliminar_silencios(video)

    if acciones.get("meme"):
        cfg = acciones["meme"]
        video = insertar_meme(video, path=cfg.get("path"), tiempo=cfg.get("time", 0))

    if acciones.get("blackwhite") and None not in acciones["blackwhite"]:
        video = aplicar_blanco_y_negro(video, *acciones["blackwhite"])

    if acciones.get("zoom"):
        escala = acciones["zoom"] if isinstance(acciones["zoom"], float) else 1.3
        video = aplicar_zoom_progresivo(video, escala)

    if acciones.get("volume"):
        video = ajustar_volumen(video, acciones["volume"])

    if acciones.get("subtitles"):
        video = agregar_subtitulos(video)

    video = aplicar_fade(video, acciones.get("fade_in", 1.0), acciones.get("fade_out", 1.0))

    if acciones.get("music_path"):
        video = agregar_musica(video, acciones["music_path"], acciones.get("music_volume", 0.3))

    if acciones.get("highlights"):
        video = detectar_highlights(video, acciones.get("highlights_duration") or 30.0)

    os.makedirs(os.path.dirname(ruta_salida) or "uploads", exist_ok=True)
    video.write_videofile(ruta_salida, codec="libx264", audio_codec="aac",
        temp_audiofile="uploads/temp_audio_out.m4a", remove_temp=True, logger=None)
    video.close()
    print(f"✅ Guardado: {ruta_salida}")
    return ruta_salida


def cambiar_velocidad(video, factor):
    if not (0.1 <= factor <= 10):
        raise ValueError(f"Factor inválido: {factor}")
    return speedx(video, factor)

def eliminar_silencios(video, min_silence_len=700, silence_thresh=-38, padding_ms=150):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        audio = AudioSegment.from_wav(ruta_wav)
        segs  = detect_nonsilent(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh)
        if not segs:
            return video
        dur = len(audio)
        clips = []
        for s, e in segs:
            clips.append(video.subclip(max(0, s-padding_ms)/1000, min(dur, e+padding_ms)/1000))
        return concatenate_videoclips(clips)
    finally:
        if os.path.exists(ruta_wav): os.remove(ruta_wav)

def aplicar_blanco_y_negro(video, inicio, fin):
    d = video.duration
    inicio = max(0.0, float(inicio))
    fin    = min(float(fin), d)
    if inicio >= fin: return video
    partes = []
    if inicio > 0:   partes.append(video.subclip(0, inicio))
    partes.append(bw_fx(video.subclip(inicio, fin)))
    if fin < d:      partes.append(video.subclip(fin, d))
    return concatenate_videoclips(partes) if len(partes) > 1 else partes[0]

def aplicar_zoom_progresivo(video, escala_max=1.3):
    resultado = video.resize(lambda t: 1 + (escala_max - 1) * (t / video.duration))
    resultado.fps = video.fps
    return resultado

def ajustar_volumen(video, factor):
    if video.audio is None: return video
    return video.volumex(factor)

def insertar_meme(video, path, tiempo):
    if not path or not os.path.exists(path): return video
    if tiempo < 0 or tiempo >= video.duration: return video
    try: meme = VideoFileClip(path)
    except: return video
    if meme.size != video.size: meme = meme.resize(video.size)
    return concatenate_videoclips([video.subclip(0, tiempo), meme, video.subclip(tiempo)])

def agregar_subtitulos(video):
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
            clip = (TextClip(txt, fontsize=38, color="white", stroke_color="black",
                        stroke_width=1.5, method="caption",
                        size=(int(video.w * 0.85), None), font="DejaVu-Sans")
                    .set_position(("center", 0.82), relative=True)
                    .set_start(start).set_end(end))
            sub_clips.append(clip)
        resultado = CompositeVideoClip([video] + sub_clips).set_audio(video.audio)
        resultado.fps = video.fps
        return resultado
    finally:
        if os.path.exists(ruta_wav): os.remove(ruta_wav)

def aplicar_fade(video, fade_in=1.0, fade_out=1.0):
    if fade_in > 0:
        video = fadein(video, fade_in)
        if video.audio: video = video.set_audio(audio_fadein(video.audio, fade_in))
    if fade_out > 0:
        video = fadeout(video, fade_out)
        if video.audio: video = video.set_audio(audio_fadeout(video.audio, fade_out))
    return video

def agregar_musica(video, ruta_musica, volumen=0.3):
    if not os.path.exists(ruta_musica): return video
    musica = AudioFileClip(ruta_musica).volumex(volumen)
    musica = audio_loop(musica, duration=video.duration) if musica.duration < video.duration else musica.subclip(0, video.duration)
    audio_final = CompositeAudioClip([video.audio, musica]) if video.audio else musica
    resultado = video.set_audio(audio_final)
    resultado.fps = video.fps
    return resultado

def detectar_highlights(video, duracion_total=30.0, ventana=3.0):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        ruta_wav = tmp.name
    try:
        video.audio.write_audiofile(ruta_wav, logger=None)
        audio = AudioSegment.from_wav(ruta_wav)
        vm = int(ventana * 1000)
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
    finally:
        if os.path.exists(ruta_wav): os.remove(ruta_wav)

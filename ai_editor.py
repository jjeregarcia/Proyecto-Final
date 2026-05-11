"""
ai_editor.py — FFmpeg puro, sin moviepy. Compatible con Railway/Linux.
Maneja videos de cualquier tamaño, con o sin audio, con o sin filtros.
"""
import os
import subprocess
import tempfile
import json
from faster_whisper import WhisperModel


class VideoProcessingError(Exception):
    pass


# ── Helpers FFmpeg ────────────────────────────────────────────────────────

def _run(cmd, timeout=300):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            print(f"FFmpeg stderr: {result.stderr[-300:]}")
        return result
    except subprocess.TimeoutExpired:
        raise VideoProcessingError("El procesamiento tardó demasiado. Intentá con un video más corto.")


def _probe(ruta):
    """Obtiene info del video como dict."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", ruta],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise VideoProcessingError("No se pudo leer el video. Asegurate de que sea un formato válido (mp4, mov, avi, mkv).")
    return json.loads(result.stdout)


def _tiene_audio(info):
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def _duracion(info):
    try:
        return float(info["format"]["duration"])
    except Exception:
        return 0.0


def _reparar(ruta_in, ruta_out):
    r = _run(["ffmpeg", "-y", "-i", ruta_in, "-c", "copy", "-movflags", "+faststart", ruta_out])
    if os.path.exists(ruta_out) and os.path.getsize(ruta_out) > 0:
        return ruta_out
    return ruta_in


# ── Función principal ─────────────────────────────────────────────────────

def procesar_video(ruta: str, acciones: dict, ruta_salida: str = "uploads/video_editado.mp4") -> str:
    if not os.path.exists(ruta):
        raise VideoProcessingError("No se encontró el archivo. Por favor subilo nuevamente.")

    os.makedirs(os.path.dirname(ruta_salida) or "uploads", exist_ok=True)

    # Reparar video
    ruta_rep = ruta.replace("_input.", "_rep.")
    ruta = _reparar(ruta, ruta_rep)

    # Info del video
    try:
        info = _probe(ruta)
    except VideoProcessingError:
        raise
    except Exception as e:
        raise VideoProcessingError(f"No se pudo analizar el video: {e}")

    tiene_audio = _tiene_audio(info)
    duracion    = _duracion(info)
    print(f"📹 Duración: {duracion:.1f}s | Audio: {tiene_audio}")

    # Normalizar acciones
    speed    = acciones.get("speed")
    if speed in (None, 1.0, 0): speed = None
    volume   = acciones.get("volume")
    if volume in (None, 1.0, 0): volume = None
    zoom     = acciones.get("zoom")
    if zoom in (None, False, 1.0, 0): zoom = None
    dur_max  = acciones.get("duration")
    fade_in  = acciones.get("fade_in")  or 0
    fade_out = acciones.get("fade_out") or 0
    bw       = acciones.get("blackwhite")
    if bw and (not isinstance(bw, (list, tuple)) or len(bw) != 2 or None in bw):
        bw = None

    # Archivo temporal de trabajo
    tmp_dir  = os.path.dirname(ruta_salida)
    uid      = os.path.basename(ruta_salida).split("_")[0]

    try:
        # ── PASO 1: Eliminar silencios ────────────────────────────────────
        ruta_actual = ruta
        if acciones.get("remove_silence") and tiene_audio:
            ruta_actual = _eliminar_silencios(ruta_actual, tmp_dir, uid)

        # ── PASO 2: Highlights ────────────────────────────────────────────
        if acciones.get("highlights") and tiene_audio:
            hl_dur = float(acciones.get("highlights_duration") or 30.0)
            ruta_actual = _detectar_highlights(ruta_actual, tmp_dir, uid, hl_dur)

        # ── PASO 3: Recorte de duración ───────────────────────────────────
        if dur_max:
            limite = min(float(dur_max), duracion)
            out = os.path.join(tmp_dir, f"{uid}_cut.mp4")
            _run(["ffmpeg", "-y", "-i", ruta_actual, "-t", str(limite), "-c", "copy", out])
            if os.path.exists(out) and os.path.getsize(out) > 0:
                ruta_actual = out

        # ── PASO 4: Construir filtros de video y audio ────────────────────
        vf_parts = []
        af_parts = []

        # Velocidad
        if speed:
            vf_parts.append(f"setpts={1.0/speed:.4f}*PTS")
            if tiene_audio:
                af_parts.append(f"atempo={min(max(speed, 0.5), 100.0):.4f}")

        # Zoom progresivo
        if zoom and isinstance(zoom, (int, float)) and zoom > 1.0:
            vf_parts.append(f"zoompan=z='min(zoom+0.0015,{zoom:.2f})':d=1:s=iw'x'ih")

        # Blanco y negro parcial (solo si se aplica a todo — simplificado)
        if bw:
            inicio_bw, fin_bw = float(bw[0]), float(bw[1])
            vf_parts.append(
                f"hue=enable='between(t,{inicio_bw},{fin_bw})':s=0"
            )

        # Fade in video
        if fade_in > 0:
            vf_parts.append(f"fade=t=in:st=0:d={fade_in}")
        # Fade out video
        if fade_out > 0:
            # necesitamos duración real para el fade out
            info2 = _probe(ruta_actual)
            dur2  = _duracion(info2)
            vf_parts.append(f"fade=t=out:st={max(0, dur2-fade_out):.3f}:d={fade_out}")

        # Volumen
        if volume and tiene_audio:
            af_parts.append(f"volume={volume:.2f}")

        # Fade in/out audio
        if fade_in > 0 and tiene_audio:
            af_parts.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0 and tiene_audio:
            info2 = _probe(ruta_actual)
            dur2  = _duracion(info2)
            af_parts.append(f"afade=t=out:st={max(0, dur2-fade_out):.3f}:d={fade_out}")

        # ── PASO 5: Aplicar filtros con ffmpeg ────────────────────────────
        if vf_parts or af_parts:
            out = os.path.join(tmp_dir, f"{uid}_filt.mp4")
            cmd = ["ffmpeg", "-y", "-i", ruta_actual]

            filter_args = []
            if vf_parts:
                filter_args += ["-vf", ",".join(vf_parts)]
            if af_parts and tiene_audio:
                filter_args += ["-af", ",".join(af_parts)]
            elif not tiene_audio:
                filter_args += ["-an"]

            cmd += filter_args
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
            if tiene_audio and af_parts:
                cmd += ["-c:a", "aac", "-b:a", "128k"]
            elif tiene_audio:
                cmd += ["-c:a", "copy"]
            else:
                cmd += ["-an"]
            cmd.append(out)

            r = _run(cmd, timeout=300)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                ruta_actual = out

        # ── PASO 6: Subtítulos ────────────────────────────────────────────
        if acciones.get("subtitles") and tiene_audio:
            ruta_actual = _agregar_subtitulos(ruta_actual, tmp_dir, uid)

        # ── PASO 7: Música de fondo ───────────────────────────────────────
        if acciones.get("music_path") and os.path.exists(acciones["music_path"]):
            vol_musica = float(acciones.get("music_volume") or 0.3)
            ruta_actual = _agregar_musica(ruta_actual, acciones["music_path"], tmp_dir, uid, vol_musica, tiene_audio)

        # ── Mover al destino final ────────────────────────────────────────
        if ruta_actual != ruta_salida:
            _run(["ffmpeg", "-y", "-i", ruta_actual, "-c", "copy", ruta_salida])

        if not os.path.exists(ruta_salida) or os.path.getsize(ruta_salida) == 0:
            raise VideoProcessingError("El video procesado está vacío. Revisá el archivo original.")

        print(f"✅ Guardado: {ruta_salida}")
        return ruta_salida

    except VideoProcessingError:
        raise
    except Exception as e:
        raise VideoProcessingError(f"Error al procesar el video: {str(e)[:200]}")


# ── Funciones auxiliares ──────────────────────────────────────────────────

def _eliminar_silencios(ruta, tmp_dir, uid):
    """Usa silencedetect de ffmpeg para cortar silencios."""
    # Detectar silencios
    r = _run(["ffmpeg", "-i", ruta, "-af", "silencedetect=n=-38dB:d=0.7",
              "-f", "null", "-"], timeout=120)
    lines = (r.stderr or "").split("\n")

    periodos = []
    inicio_silencio = None
    for line in lines:
        if "silence_start" in line:
            try: inicio_silencio = float(line.split("silence_start: ")[1])
            except: pass
        if "silence_end" in line and inicio_silencio is not None:
            try:
                fin = float(line.split("silence_end: ")[1].split(" ")[0])
                periodos.append((inicio_silencio, fin))
                inicio_silencio = None
            except: pass

    if not periodos:
        return ruta

    # Construir segmentos de no-silencio
    info = _probe(ruta)
    dur  = _duracion(info)
    segmentos = []
    pos = 0.0
    padding = 0.15
    for s, e in periodos:
        seg_fin = max(pos, s - padding)
        if seg_fin - pos > 0.1:
            segmentos.append((pos, seg_fin))
        pos = min(dur, e + padding)
    if dur - pos > 0.1:
        segmentos.append((pos, dur))

    if not segmentos:
        return ruta

    # Crear lista de segmentos con concat
    lista_file = os.path.join(tmp_dir, f"{uid}_segs.txt")
    clips = []
    for i, (s, e) in enumerate(segmentos):
        clip = os.path.join(tmp_dir, f"{uid}_seg{i}.mp4")
        _run(["ffmpeg", "-y", "-ss", str(s), "-to", str(e), "-i", ruta,
              "-c", "copy", clip], timeout=60)
        if os.path.exists(clip) and os.path.getsize(clip) > 0:
            clips.append(clip)

    if not clips:
        return ruta

    with open(lista_file, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")

    out = os.path.join(tmp_dir, f"{uid}_nosilence.mp4")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lista_file,
          "-c", "copy", out], timeout=180)

    return out if os.path.exists(out) and os.path.getsize(out) > 0 else ruta


def _detectar_highlights(ruta, tmp_dir, uid, duracion_total=30.0):
    """Extrae los segmentos más ruidosos (highlights) del video."""
    ventana = 3.0
    r = _run(["ffmpeg", "-i", ruta, "-af",
               f"astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
               "-f", "null", "-"], timeout=120)

    # Parsear energías
    energias = []
    tiempo = 0.0
    for line in (r.stderr or "").split("\n"):
        if "RMS_level" in line:
            try:
                val = float(line.split("=")[1])
                if val > -100:
                    energias.append((abs(val), tiempo))
                tiempo += ventana
            except: pass

    if not energias:
        info = _probe(ruta)
        dur  = _duracion(info)
        # fallback: tomar los primeros N segundos
        out = os.path.join(tmp_dir, f"{uid}_hl.mp4")
        _run(["ffmpeg", "-y", "-i", ruta, "-t", str(duracion_total), "-c", "copy", out])
        return out if os.path.exists(out) and os.path.getsize(out) > 0 else ruta

    energias.sort()  # menor RMS = más ruidoso (RMS negativo)
    n = max(1, int(duracion_total / ventana))
    mejores = sorted(energias[:n], key=lambda x: x[1])

    clips = []
    ultimo = -ventana
    for _, inicio in mejores:
        if inicio >= ultimo:
            info = _probe(ruta)
            dur  = _duracion(info)
            fin  = min(inicio + ventana, dur)
            clip = os.path.join(tmp_dir, f"{uid}_hl{int(inicio)}.mp4")
            _run(["ffmpeg", "-y", "-ss", str(inicio), "-to", str(fin),
                  "-i", ruta, "-c", "copy", clip], timeout=30)
            if os.path.exists(clip) and os.path.getsize(clip) > 0:
                clips.append(clip)
            ultimo = fin

    if not clips:
        return ruta

    lista = os.path.join(tmp_dir, f"{uid}_hl_list.txt")
    with open(lista, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")

    out = os.path.join(tmp_dir, f"{uid}_highlights.mp4")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lista,
          "-c", "copy", out], timeout=120)
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else ruta


def _agregar_subtitulos(ruta, tmp_dir, uid):
    """Transcribe con Whisper y quema subtítulos en el video."""
    wav = os.path.join(tmp_dir, f"{uid}_audio.wav")
    _run(["ffmpeg", "-y", "-i", ruta, "-vn", "-acodec", "pcm_s16le",
          "-ar", "16000", "-ac", "1", wav], timeout=120)

    if not os.path.exists(wav) or os.path.getsize(wav) == 0:
        print("⚠️  No se pudo extraer audio para subtítulos.")
        return ruta

    try:
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(wav, language="es")
        subs = list(segments)
    except Exception as e:
        print(f"⚠️  Error en transcripción: {e}")
        return ruta
    finally:
        if os.path.exists(wav): os.remove(wav)

    if not subs:
        return ruta

    # Generar archivo SRT
    srt_path = os.path.join(tmp_dir, f"{uid}_subs.srt")
    def fmt_time(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(subs, 1):
            f.write(f"{i}\n{fmt_time(seg.start)} --> {fmt_time(seg.end)}\n{seg.text.strip()}\n\n")

    out = os.path.join(tmp_dir, f"{uid}_subtitled.mp4")
    r = _run(["ffmpeg", "-y", "-i", ruta,
              "-vf", f"subtitles={srt_path}:force_style='FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=1'",
              "-c:a", "copy", out], timeout=300)

    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    print("⚠️  No se pudieron quemar subtítulos (falta libass). Se devuelve video sin subtítulos.")
    return ruta


def _agregar_musica(ruta, ruta_musica, tmp_dir, uid, volumen=0.3, tiene_audio=True):
    out = os.path.join(tmp_dir, f"{uid}_music.mp4")
    info = _probe(ruta)
    dur  = _duracion(info)

    if tiene_audio:
        cmd = [
            "ffmpeg", "-y",
            "-i", ruta,
            "-stream_loop", "-1", "-i", ruta_musica,
            "-filter_complex",
            f"[1:a]volume={volumen:.2f},apad[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(dur),
            out
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", ruta,
            "-stream_loop", "-1", "-i", ruta_musica,
            "-filter_complex",
            f"[1:a]volume={volumen:.2f},apad[music]",
            "-map", "0:v",
            "-map", "[music]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(dur),
            out
        ]

    r = _run(cmd, timeout=300)
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else ruta

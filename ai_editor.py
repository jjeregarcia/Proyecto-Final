"""
ai_editor.py - 100% ffmpeg, sin MoviePy. Compatible con Railway/Linux.
Soporta cualquier formato de video. Rápido y robusto.
"""
import os, tempfile, subprocess, json
from faster_whisper import WhisperModel
from pydub import AudioSegment
from pydub.silence import detect_nonsilent


# ── Helper ffmpeg ─────────────────────────────────────────────────────────

def _run(cmd: list, timeout=600) -> subprocess.CompletedProcess:
    """Ejecuta un comando ffmpeg y lanza excepción si falla."""
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr.decode()[-500:]}")
    return result


def _duracion(ruta: str) -> float:
    """Obtiene la duración del video en segundos usando ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", ruta],
        capture_output=True, timeout=30
    )
    data = json.loads(r.stdout)
    return float(data["format"]["duration"])


def _reparar(ruta: str) -> str:
    """Re-muxea para corregir moov atom y otros problemas de corrupción."""
    ruta_fixed = ruta.replace("_input.", "_fixed.")
    try:
        _run(["ffmpeg", "-y", "-i", ruta, "-c", "copy",
              "-movflags", "+faststart", ruta_fixed])
        print(f"🔧 Video reparado OK")
        return ruta_fixed
    except Exception as e:
        print(f"⚠️ Reparación falló, usando original: {e}")
        return ruta


# ── Procesador principal ──────────────────────────────────────────────────

def procesar_video(ruta: str, acciones: dict, ruta_salida: str = "uploads/video_editado.mp4") -> str:
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No se encontró: {ruta}")

    os.makedirs(os.path.dirname(ruta_salida) or "uploads", exist_ok=True)

    # 1. Reparar video primero
    ruta = _reparar(ruta)
    duracion = _duracion(ruta)
    print(f"📹 Duración: {duracion:.1f}s")

    # 2. Normalizar acciones
    speed  = acciones.get("speed")
    volume = acciones.get("volume")
    zoom   = acciones.get("zoom")
    if speed  in (1.0, 0.0): speed  = None
    if volume in (1.0, 0.0): volume = None
    if zoom   in (1.0, 0.0, False): zoom = None

    duration   = acciones.get("duration")
    bw         = acciones.get("blackwhite")   # [inicio, fin] o None
    fade_in    = acciones.get("fade_in")  or 0
    fade_out   = acciones.get("fade_out") or 0
    subtitles  = acciones.get("subtitles", False)
    rm_silence = acciones.get("remove_silence", False)
    music_path = acciones.get("music_path")
    music_vol  = acciones.get("music_volume") or 0.3
    highlights = acciones.get("highlights", False)
    hl_dur     = acciones.get("highlights_duration") or 30.0

    # Validar blackwhite
    if bw and (not isinstance(bw, (list, tuple)) or len(bw) != 2 or None in bw):
        bw = None

    # 3. Si piden highlights → recortar primero los mejores momentos
    if highlights:
        ruta = _highlights(ruta, duracion, hl_dur)
        duracion = _duracion(ruta)

    # 4. Eliminar silencios
    if rm_silence:
        ruta = _eliminar_silencios(ruta)
        duracion = _duracion(ruta)

    # 5. Recortar duración
    if duration and float(duration) < duracion:
        tmp = _tmp("mp4")
        _run(["ffmpeg", "-y", "-i", ruta, "-t", str(duration),
              "-c", "copy", tmp])
        ruta = tmp
        duracion = float(duration)

    # 6. Construir filtros de video y audio
    vf_parts = []
    af_parts = []

    # Zoom progresivo
    if zoom:
        z = float(zoom)
        vf_parts.append(
            f"zoompan=z='min(zoom+0.0005,{z})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=hd720"
        )

    # Blanco y negro parcial
    if bw:
        inicio_bw = float(bw[0])
        fin_bw    = float(bw[1])
        vf_parts.append(
            f"hue=enable='between(t,{inicio_bw},{fin_bw})':s=0"
        )

    # Velocidad (video)
    if speed:
        vf_parts.append(f"setpts={1/float(speed):.4f}*PTS")
        # Audio: atempo soporta 0.5-2.0, encadenar si hace falta
        s = float(speed)
        if 0.5 <= s <= 2.0:
            af_parts.append(f"atempo={s:.4f}")
        elif s > 2.0:
            af_parts.append(f"atempo=2.0,atempo={s/2:.4f}")
        elif s < 0.5:
            af_parts.append(f"atempo=0.5,atempo={s*2:.4f}")

    # Volumen
    if volume:
        af_parts.append(f"volume={float(volume):.2f}")

    # Fade in/out video
    if fade_in:
        vf_parts.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out:
        vf_parts.append(f"fade=t=out:st={max(0, duracion-fade_out):.2f}:d={fade_out}")

    # Fade in/out audio
    if fade_in:
        af_parts.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out:
        af_parts.append(f"afade=t=out:st={max(0, duracion-fade_out):.2f}:d={fade_out}")

    # 7. Aplicar filtros de video/audio
    tmp_filtros = _tmp("mp4")
    cmd = ["ffmpeg", "-y", "-i", ruta]
    extra_args = []

    if vf_parts or af_parts or music_path:
        if music_path and os.path.exists(music_path):
            cmd += ["-i", music_path]
            # Mezclar música con audio original
            music_af = f"[1:a]volume={music_vol:.2f},aloop=loop=-1:size=2e+09[music]"
            orig_af  = "[0:a]" + (",".join(af_parts) if af_parts else "") + "[origa]" if af_parts else "[0:a][origa]"
            if af_parts:
                filter_complex = f"[0:a]{','.join(af_parts)}[origa];{music_af};[origa][music]amix=inputs=2:duration=first[aout]"
            else:
                filter_complex = f"{music_af};[0:a][music]amix=inputs=2:duration=first[aout]"
            extra_args += ["-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]"]
            if vf_parts:
                extra_args += ["-vf", ",".join(vf_parts)]
        else:
            if vf_parts:
                extra_args += ["-vf", ",".join(vf_parts)]
            if af_parts:
                extra_args += ["-af", ",".join(af_parts)]

        cmd += extra_args + ["-c:v", "libx264", "-c:a", "aac",
                              "-preset", "fast", tmp_filtros]
    else:
        # Sin filtros → solo re-encodear para garantizar compatibilidad
        cmd += ["-c:v", "libx264", "-c:a", "aac", "-preset", "fast", tmp_filtros]

    _run(cmd)
    ruta = tmp_filtros

    # 8. Subtítulos
    if subtitles:
        ruta = _agregar_subtitulos(ruta)

    # 9. Mover a salida final
    _run(["ffmpeg", "-y", "-i", ruta, "-c", "copy",
          "-movflags", "+faststart", ruta_salida])

    print(f"✅ Guardado: {ruta_salida}")
    return ruta_salida


# ── Funciones auxiliares ──────────────────────────────────────────────────

def _tmp(ext: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False, dir="uploads")
    f.close()
    return f.name


def _eliminar_silencios(ruta: str, min_silence_ms=700, thresh=-38, padding_ms=150) -> str:
    """Detecta silencios con pydub y concatena los segmentos con voz."""
    tmp_wav = _tmp("wav")
    _run(["ffmpeg", "-y", "-i", ruta, "-vn", "-ar", "16000", "-ac", "1", tmp_wav])

    audio = AudioSegment.from_wav(tmp_wav)
    segs  = detect_nonsilent(audio, min_silence_len=min_silence_ms, silence_thresh=thresh)
    os.remove(tmp_wav)

    if not segs:
        return ruta

    dur_ms = len(audio)
    # Generar lista de segmentos con padding
    partes = []
    for s, e in segs:
        inicio = max(0, s - padding_ms) / 1000
        fin    = min(dur_ms, e + padding_ms) / 1000
        partes.append((inicio, fin))

    # Crear archivo concat
    clips = []
    for i, (inicio, fin) in enumerate(partes):
        tmp_clip = _tmp("mp4")
        _run(["ffmpeg", "-y", "-i", ruta,
              "-ss", str(inicio), "-to", str(fin),
              "-c:v", "libx264", "-c:a", "aac", "-preset", "fast", tmp_clip])
        clips.append(tmp_clip)

    return _concatenar(clips)


def _concatenar(clips: list) -> str:
    """Concatena una lista de archivos mp4 usando ffmpeg concat."""
    lista = _tmp("txt")
    with open(lista, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    salida = _tmp("mp4")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
          "-i", lista, "-c", "copy", salida])
    os.remove(lista)
    for c in clips:
        if os.path.exists(c): os.remove(c)
    return salida


def _highlights(ruta: str, duracion: float, hl_dur: float = 30.0, ventana: float = 3.0) -> str:
    """Selecciona los segmentos de mayor energía de audio."""
    tmp_wav = _tmp("wav")
    _run(["ffmpeg", "-y", "-i", ruta, "-vn", "-ar", "16000", "-ac", "1", tmp_wav])

    audio   = AudioSegment.from_wav(tmp_wav)
    os.remove(tmp_wav)
    vm      = int(ventana * 1000)
    energias = [(audio[i:i+vm].rms, i / 1000)
                for i in range(0, len(audio) - vm, vm // 2)]
    if not energias:
        return ruta

    energias.sort(reverse=True)
    n_segs  = max(1, int(hl_dur / ventana))
    mejores = sorted(energias[:n_segs], key=lambda x: x[1])

    clips, ultimo = [], -ventana
    for _, inicio in mejores:
        if inicio >= ultimo:
            fin = min(inicio + ventana, duracion)
            tmp_clip = _tmp("mp4")
            _run(["ffmpeg", "-y", "-i", ruta,
                  "-ss", str(inicio), "-to", str(fin),
                  "-c:v", "libx264", "-c:a", "aac", "-preset", "fast", tmp_clip])
            clips.append(tmp_clip)
            ultimo = fin

    return _concatenar(clips) if clips else ruta


def _agregar_subtitulos(ruta: str) -> str:
    """Transcribe con Whisper y quema subtítulos con ffmpeg."""
    tmp_wav = _tmp("wav")
    tmp_srt = _tmp("srt")

    try:
        _run(["ffmpeg", "-y", "-i", ruta, "-vn",
              "-ar", "16000", "-ac", "1", tmp_wav])

        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(tmp_wav, language="es")
        subs = [(s.start, s.end, s.text.strip()) for s in segments]

        if not subs:
            print("⚠️ Sin texto detectado, video sin subtítulos.")
            return ruta

        with open(tmp_srt, "w", encoding="utf-8") as f:
            for i, (start, end, txt) in enumerate(subs, 1):
                f.write(f"{i}\n{_ts(start)} --> {_ts(end)}\n{txt}\n\n")

        salida = _tmp("mp4")
        _run(["ffmpeg", "-y", "-i", ruta,
              "-vf", f"subtitles={tmp_srt}:force_style='FontSize=18,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Alignment=2'",
              "-c:v", "libx264", "-c:a", "copy", salida])
        return salida

    except Exception as e:
        print(f"⚠️ Error subtítulos: {e}. Devolviendo sin subtítulos.")
        return ruta
    finally:
        for f in [tmp_wav, tmp_srt]:
            if os.path.exists(f): os.remove(f)


def _ts(seg: float) -> str:
    """Segundos → HH:MM:SS,mmm (formato SRT)."""
    h  = int(seg // 3600)
    m  = int((seg % 3600) // 60)
    s  = int(seg % 60)
    ms = int((seg - int(seg)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

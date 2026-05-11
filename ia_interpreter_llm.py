"""
ia_interpreter_llm.py
Intérprete con memoria conversacional usando Groq.
Soporta mensajes conversacionales además de comandos de edición de video.
"""
import json
import os
from groq import Groq

def _get_client():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY no está definida.")
    return Groq(api_key=api_key)

CLASSIFIER_PROMPT = """Sos un clasificador de intenciones. Analizá el mensaje del usuario y respondé SOLO con una de estas dos palabras:
- "VIDEO" si el mensaje es un comando de edición de video (acelerar, subtítulos, silencio, recortar, zoom, fade, música, blanco y negro, tiktok, reel, highlights, etc.)
- "CHAT" si es un saludo, pregunta general, conversación casual, agradecimiento, o cualquier cosa que NO sea editar un video.
Respondé únicamente "VIDEO" o "CHAT", sin nada más."""

CHAT_SYSTEM_PROMPT = """Sos un asistente amigable integrado en CutAI, una app de edición de video con IA.
Respondé de forma breve y cordial a saludos, preguntas generales o conversación casual.
Si el usuario pregunta qué podés hacer, explicá que podés editar videos con comandos en lenguaje natural:
acelerar, recortar, agregar subtítulos, eliminar silencios, efectos de color, música de fondo, zoom, fade in/out, highlights, y más.
Respondé siempre en el mismo idioma que el usuario. Sé conciso (máximo 3 oraciones)."""

VIDEO_SYSTEM_PROMPT = """Sos un intérprete de comandos de edición de video con memoria conversacional.
El usuario puede referirse a acciones anteriores con frases como "ahora aceleralo", "además agregá subtítulos", "sacale eso último".
Tu tarea es devolver el JSON FINAL con TODAS las acciones acumuladas, modificadas o removidas según el nuevo comando.

El JSON tiene exactamente estas claves:
{
  "remove_silence": bool,
  "subtitles": bool,
  "duration": number | null,
  "speed": float | null,
  "blackwhite": [float, float] | null,
  "zoom": float | null,
  "volume": float | null,
  "fade_in": float | null,
  "fade_out": float | null,
  "music_volume": float | null,
  "highlights": bool,
  "highlights_duration": float | null
}

Reglas:
- Si el usuario dice "sacá eso" o "sin subtítulos" → poné esa clave en false/null.
- Si dice "además" / "también" / "y" → sumá al estado anterior.
- duration en segundos, speed multiplicador (1.0=normal), volume multiplicador (1.0=normal).
- blackwhite: [inicio_seg, fin_seg] o null.
- fade_in / fade_out: duración en segundos. null si no se menciona.
- music_volume: volumen música de fondo (0.3 = 30%). null si no se menciona.
- Plataformas: tiktok/reel = duration:30 + remove_silence:true. stories = duration:15. youtube = subtitles:true + remove_silence:true.
- Respondé SOLO el JSON, sin texto extra ni backticks.
- highlights: true si pide resumen/lo mejor/momentos clave. highlights_duration: segundos (default 30)."""


def _clasificar_intencion(texto: str) -> str:
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user",   "content": texto},
            ],
            max_tokens=5,
            temperature=0.0,
        )
        resultado = response.choices[0].message.content.strip().upper()
        return "VIDEO" if "VIDEO" in resultado else "CHAT"
    except Exception as e:
        print(f"⚠️  Error en clasificador: {e}. Asumiendo VIDEO.")
        return "VIDEO"


def _responder_chat(texto: str, historial: list) -> str:
    client = _get_client()
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    if historial:
        messages.extend(historial)
    messages.append({"role": "user", "content": texto})
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=150,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def interpretar_comando(texto: str, historial: list = None):
    fallback = {
        "remove_silence": False, "subtitles": False, "duration": None,
        "speed": None, "blackwhite": None, "zoom": None, "volume": None,
        "fade_in": None, "fade_out": None, "music_volume": None,
        "highlights": False, "highlights_duration": None,
    }
    if not texto.strip():
        return fallback
    historial = historial or []
    intencion = _clasificar_intencion(texto)
    if intencion == "CHAT":
        try:
            return _responder_chat(texto, historial)
        except Exception as e:
            print(f"⚠️  Error en chat: {e}.")
            return "¡Hola! Estoy aquí para ayudarte a editar tus videos. ¿Qué querés hacer?"
    client = _get_client()
    messages = [{"role": "system", "content": VIDEO_SYSTEM_PROMPT}]
    messages.extend(historial)
    messages.append({"role": "user", "content": texto})
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=300,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        raw = raw.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
        result = json.loads(raw)
        if result.get("speed")  == 1.0: result["speed"]  = None
        if result.get("volume") == 1.0: result["volume"] = None
        return result
    except Exception as e:
        print(f"⚠️  Error LLM: {e}. Usando fallback.")
        return fallback

# Imagen de Centinela — seguimiento posoperatorio por voz.
#
# Principio: TODO lo descargable se descarga en el build, no en el arranque.
# La compuerta G2 del reto cronometra el levantamiento en la máquina del jurado,
# y cada descarga en runtime es un riesgo de red que corre contra ese reloj.
# La imagen sale con la voz de Piper, el modelo de embeddings y el índice
# vectorial adentro; al arrancar solo se calientan.

FROM python:3.12-slim

WORKDIR /app

# Dependencias del sistema: libgomp la necesita onnxruntime (Piper).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencias de Python, fijadas. El índice extra sirve la rueda CPU de torch:
# 200 MB en lugar de los 2.5 GB de la variante CUDA que nadie usaría acá.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

# La voz de Piper (63 MB) no se versiona en git; se hornea en la imagen.
RUN python -m piper.download_voices es_MX-claude-high --data-dir /app/models/piper

# El modelo de embeddings (~120 MB) también queda en la imagen: sin esto, el
# primer arranque lo bajaría de Hugging Face con el cronómetro corriendo.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('intfloat/multilingual-e5-small')"

# Código, interfaz e índice vectorial pre-construido (6.512 fragmentos).
# Regenerar el índice cuesta ~9 minutos de CPU; G2 da 15 en total.
COPY src/ src/
COPY web/ web/
COPY chroma_data/ chroma_data/
COPY scripts/ scripts/

EXPOSE 8080

# El arranque carga y calienta los modelos (~40 s) antes de aceptar tráfico;
# /api/salud responde solo cuando todo está en pie, así que sirve de healthcheck
# honesto: verde significa listo de verdad.
HEALTHCHECK --interval=10s --timeout=5s --start-period=180s --retries=20 \
    CMD curl -sf http://localhost:8080/api/salud || exit 1

CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8080"]

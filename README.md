# Agente de Voz Posoperatorio — Tech Sphere Challenge 2026

Agente conversacional de voz para seguimiento posoperatorio de pacientes
colombianos: interpreta síntomas descritos en lenguaje cotidiano contra
documentación clínica (RAG con trazabilidad) y decide de forma determinista
cuándo escalar a personal médico.

> **Estado:** en construcción — ventana del reto 7–10 de agosto de 2026.
> Este README se completará con instrucciones de levantamiento (≤15 min),
> métricas de latencia/consumo/costo y matriz de confusión antes de la entrega.

## Arquitectura (resumen)

| Capa | Pieza |
|---|---|
| Razonamiento | Llama 3.1 70B vía Groq (permitido por las bases) |
| Fallback | Llama 3.2 3B local vía Ollama (degradación automática) |
| STT | Whisper Large V3 en Groq |
| TTS | Piper |
| RAG | ChromaDB + BGE-M3 |
| Superficies | Consola de administración + interfaz de llamada (navegador) |

## Seguridad

Las revisiones de seguridad por fase se documentan en [`docs/security/`](docs/security/).

## Licencia

MIT — ver [LICENSE](LICENSE).

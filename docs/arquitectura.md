# Arquitectura y flujo de decisión — Centinela

> **Entregable 02.** El jurado toma elementos de este diagrama al azar y los
> busca en el código. Cada caja de aquí nombra un archivo real del repositorio,
> y las cifras son medidas, no estimadas.

---

## 1. Arquitectura de la solución

```mermaid
flowchart TB
    subgraph NAV["🖥️ Navegador — las dos superficies exigidas"]
        LL["<b>Interfaz de llamada</b><br/>web/index.html · consola.js<br/>escucha automática · sin botones"]
        CK["<b>Consola de conocimiento</b><br/>subir · listar · eliminar<br/>estado de procesamiento visible"]
    end

    subgraph API["⚙️ FastAPI — src/api/app.py"]
        EP["POST /api/llamada · /turno · /colgar<br/>GET·POST·DELETE /api/documentos<br/>GET /api/salud"]
    end

    subgraph CODIGO["🔴 DECISIÓN CLÍNICA — 100% código determinista"]
        direction TB
        AL["<b>alarmas.py</b><br/>7 síntomas de alarma<br/>por habla real del paciente"]
        FI["<b>fiebre.py</b><br/>cifras en dígitos y palabras<br/>negaciones · rango fisiológico"]
        HE["<b>herida.py</b><br/>secreción › eritema › normal<br/>con manejo de negación"]
        DO["<b>dolor.py</b><br/>escala 0-10 y verbal<br/>movilidad"]
        ESC["<b>escalamiento.py</b><br/>umbrales validados<br/>contra 160 casos"]
        AL --> ESC
        FI --> ESC
        HE --> ESC
        DO --> ESC
    end

    subgraph CONV["🔀 Máquina de turnos — src/conversacion/"]
        TU["<b>turno.py</b><br/>qué preguntar · cuándo repreguntar<br/>cuándo escalar sin colgar"]
        PR["<b>preguntas.py</b><br/>detecta preguntas del paciente<br/>y a los cuidadores"]
    end

    subgraph VOZ["🎙️ Voz — src/voz/"]
        ST["<b>stt.py</b><br/>Whisper Large V3 · Groq<br/>~1,5 s"]
        TT["<b>tts.py</b><br/>Piper es_MX local<br/>primer audio ~400 ms"]
    end

    subgraph RAG["📚 Conocimiento — src/rag/"]
        GR["<b>grounding.py</b><br/>compuerta en 4 capas"]
        IX["<b>index.py</b><br/>ChromaDB · e5-small · 384 dim<br/>6.512 fragmentos citables"]
        SA["<b>saneamiento.py</b><br/>defensa contra inyección"]
        GR --> IX
        SA --> GR
    end

    MOD["🤖 <b>llama3.2:3b</b> local vía Ollama<br/>src/modelo/cliente.py<br/>SOLO extracción · SEGUNDO PLANO<br/>validado contra el texto crudo"]

    OBS["📊 <b>metricas.py</b><br/>latencia · tokens · costo<br/>registro por turno en JSONL"]

    LL <--> EP
    CK <--> EP
    EP --> ST
    ST --> CODIGO
    CODIGO --> CONV
    CONV --> TT
    TT --> LL
    CONV -.->|"asíncrono, fuera<br/>del camino crítico"| MOD
    MOD -.->|"solo puede AGREGAR<br/>lo que el paciente dijo"| CODIGO
    CONV <--> RAG
    CK --> IX
    EP --> OBS

    style CODIGO fill:#1a0b0b,stroke:#E02424,stroke-width:4px,color:#fff
    style MOD fill:#12181f,stroke:#64748B,stroke-dasharray:6 4,color:#94A3B8
    style RAG fill:#0b1220,stroke:#19C6E6,color:#E8EDF6
    style VOZ fill:#0b1220,stroke:#A3E635,color:#E8EDF6
    style CONV fill:#0b1220,stroke:#D946EF,color:#E8EDF6
    style OBS fill:#0b1220,stroke:#F59E0B,color:#E8EDF6
```

**La caja roja es la tesis del proyecto.** Todo lo que decide sobre la salud del
paciente vive ahí, en código auditable. El modelo (caja punteada) trabaja fuera
del camino crítico y **no puede introducir un dato que el paciente no dijo**: su
salida se valida contra el texto crudo antes de tocar el cuadro clínico.

---

## 2. Flujo de decisión del agente

```mermaid
flowchart TD
    A(["🎙️ El paciente habla<br/>y hace una pausa"]) --> B["Whisper transcribe<br/><i>~1,5 s</i>"]
    B --> C{"¿Transcripción<br/>con contenido?"}
    C -->|"'.', 'Gracias.'<br/>ruido de subtítulos"| REP["🔁 «No le escuché bien,<br/>¿me lo repite?»<br/><i>no consume el turno</i>"]
    REP --> A

    C -->|sí| D["<b>Detectores en código · ~30 ms</b><br/>alarmas · fiebre · herida · dolor · movilidad<br/><i>corren TODOS, no solo el tema preguntado</i>"]

    D --> E{"¿Síntoma<br/>de alarma?"}
    E -->|"no respira · se desmayó<br/>se abrió la herida"| ROJO

    E -->|no| F{"¿Cifra de fiebre<br/>imposible?"}
    F -->|"58 °C = error<br/>de transcripción"| CONF["🔁 Pide confirmación<br/><i>máximo 2 veces, después sigue</i>"]
    CONF --> A

    F -->|no| G{"<b>escalamiento.py</b><br/>evaluar(cuadro)"}

    G -->|"fiebre ≥ 38 · secreción purulenta<br/>dolor ≥ 8 · fiebre referida<br/>+ hallazgo en herida"| ROJO["🔴 <b>ROJO</b><br/>alerta al equipo<br/><i>y la llamada CONTINÚA</i>"]
    G -->|"febrícula · eritema · dolor 4-7<br/>fiebre sin medir · temp < 35,5"| AMAR["🟡 <b>AMARILLO</b><br/>queda para revisión"]
    G -->|sin hallazgos| VER["🟢 <b>VERDE</b>"]

    VER --> H{"¿Faltan campos<br/>por preguntar?"}
    H -->|"sí — verde sin datos<br/>NO es un alta"| SIG
    H -->|no| CIERRE

    ROJO --> SIG["Siguiente pregunta<br/>desde plantilla · <i>0 ms</i><br/>eco de lo escuchado"]
    AMAR --> SIG
    SIG --> I{"¿La respuesta<br/>aportó el dato?"}
    I -->|no| RE["🔁 «No le entendí lo de X»<br/><i>reintenta una vez</i>"]
    RE --> A
    I -->|sí| TTS["Piper sintetiza<br/><i>primer audio ~400 ms</i>"]
    TTS --> J(["🔊 <b>Latencia percibida<br/>P50: 4,2 s</b>"])
    J --> A

    CIERRE(["📋 Resumen estructurado<br/>semáforo · motivos · cuadro<br/>qué quedó sin preguntar · costo"])

    D -.->|"asíncrono"| MOD["🤖 llama3.2:3b<br/>extrae detalle · ~17 s"]
    MOD -.-> D

    style D fill:#1a0b0b,stroke:#E02424,stroke-width:3px,color:#fff
    style G fill:#1a0b0b,stroke:#E02424,stroke-width:3px,color:#fff
    style ROJO fill:#2a0f0f,stroke:#F87171,stroke-width:3px,color:#fff
    style AMAR fill:#2a1f0a,stroke:#F5A524,color:#fff
    style VER fill:#0f2a1a,stroke:#22C55E,color:#fff
    style MOD fill:#12181f,stroke:#64748B,stroke-dasharray:6 4,color:#94A3B8
    style CIERRE fill:#0b1220,stroke:#19C6E6,color:#E8EDF6
```

---

## 3. La compuerta de grounding, capa por capa

Se construyó en capas porque **se midió que un umbral solo no alcanza**: la
similitud coseno de lo respondible (0,853–0,927) **se superpone** con la de lo
clínicamente ausente (0,871–0,891).

```mermaid
flowchart LR
    P(["Pregunta del<br/>paciente"]) --> L1{"1 · Tema<br/>restringido"}
    L1 -->|"dosis · cambio<br/>de medicación"| NO["🚫 <b>«No lo sé»</b><br/>y se anota para el equipo"]
    L1 -->|sigue| L2{"2 · Procedimiento<br/>fuera del corpus"}
    L2 -->|"corazón · hígado<br/>cesárea · cataratas"| NO
    L2 -->|sigue| L3{"3 · Filtro por el<br/>procedimiento<br/>del paciente"}
    L3 -->|"sin documentación<br/>de ese escenario"| NO
    L3 -->|sigue| L4{"4 · Verificación<br/>léxica por raíz"}
    L4 -->|"el material no<br/>trata el tema"| NO
    L4 -->|sigue| L5{"5 · Umbral<br/>de similitud"}
    L5 -->|"< 0,85"| NO
    L5 -->|sustentado| SI["✅ <b>Cita textual</b><br/>archivo y página<br/><i>no genera: cita</i>"]

    style NO fill:#2a1f0a,stroke:#F5A524,color:#fff
    style SI fill:#0f2a1a,stroke:#22C55E,color:#fff
```

---

## 4. Cómo se decidió la arquitectura — cada falla movió una responsabilidad

| Señal | Dónde vive | La falla medida que lo decidió |
|---|---|---|
| Síntomas de alarma | código | el 3B inventó `dolor_toracico` al ver la lista en el prompt |
| Temperatura dicha | código | *"creo que como 38"* escalaba un turno tarde |
| Números alucinados | validación vs. texto crudo | el 3B extrajo `fiebre_c=38` de un texto **sin cifra** |
| Cifras imposibles | código | Whisper transcribió *"38"* como *"58"* y pasó de largo |
| Estado de la herida | código | *"roja, hinchada y le sale líquido"* quedaba como eritema |
| Negación de hallazgos | código | *"nada de pus"* se registraba como **secreción purulenta** |
| Dolor y movilidad | código | el eco decía *"un 7 anotado"* y el resumen cerraba *"sin dato"* |
| Decisión de escalar | código | no se negocia con un prompt |

**Resultado:** el modelo de lenguaje quedó reducido a lo que hace bien —entender
cómo habla la gente— y ninguna de sus fallas puede llegar al registro clínico.

---

## 5. Verificación

| Qué | Cómo se comprueba | Resultado |
|---|---|---|
| Decisión clínica (motor aislado) | `pytest tests/test_escalamiento.py` | **12/12** en casos rojo |
| Decisión clínica (pipeline completo) | `python scripts/banco_conversacional.py` | **10/12**, ninguno cae a verde |
| Conocimiento vivo (G5) | `pytest tests/test_conocimiento_vivo.py` | alta · uso · baja · olvido |
| Defensa contra inyección | `pytest tests/test_saneamiento.py` | 14 ataques bloqueados |
| Dataset contra el kit | `pytest tests/test_dataset.py` | 20 afirmaciones verificadas |
| Levantamiento (G2) | `docker compose up` | **6 min 07 s** de 15 |
| Suite completa | `pytest tests/` | **284 pruebas** |

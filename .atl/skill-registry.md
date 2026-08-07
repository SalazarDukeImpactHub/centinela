# Skill Registry — postop-voice-agent

Generado por sdd-init · 2026-08-07 · Fuente: `~/.claude/skills/`
Registro compacto para inyección en sub-agentes. Los sub-agentes NO leen SKILL.md — reciben estas reglas pre-digeridas.

## Compact Rules

### verification-before-completion
- NUNCA afirmar "listo/funciona/tests pasan" sin evidencia de ESTA sesión
- Prohibido "debería funcionar", "probably", "parece que" sin verificar
- Gate: identificar comando → ejecutarlo → leer output + exit code → recién afirmar
- Sin acceso a verificación → declarar "implementado, pendiente de verificación" + lista exacta de checks

### work-unit-commits
- Commit = unidad de trabajo entregable (comportamiento + sus tests + sus docs juntos)
- Prohibido commitear por tipo de archivo (models, luego services, luego tests)
- El repo debe quedar coherente con solo ese commit aplicado
- Mensaje explica el resultado, no la lista de archivos; conventional commits
- Este proyecto: directo a main, sin PRs — el jurado evalúa historial de commits

### testing-strategy
- Clasificar módulos por riesgo: High (lógica clínica/escalamiento — tests bloqueantes), Medium (flujos principales), Low (helpers)
- Sin framework configurado → declararlo prerequisito antes del executor (aplica en F1: pytest)
- E2E es último recurso — reservar para levantamiento G2 y sesión de voz
- No definir tests para features fuera del alcance de esta versión

### security-audit
- Hallazgos Critical bloquean entrega — sin excepciones
- Alcance: solo lo visible; lo faltante (infra, CI) se declara out of scope explícito
- Lente exclusivamente de seguridad (OWASP A01–A10) — no repetir hallazgos de code-review
- Reporte: Critical/High/Medium/Low + veredicto BLOCKED / DELIVERABLE WITH OBSERVATIONS / APPROVED
- Este proyecto: evidencia por fase en docs/security/, batería de inyección de prompt en F4

## User Skills — triggers adicionales (no expandidas; cargar bajo demanda)

| Skill | Trigger |
|---|---|
| systematic-debugging | bug, test fallando, comportamiento inesperado — causa raíz ANTES de proponer fix |
| code-review | revisión de código de sesión — seguridad, calidad, deuda técnica |
| ciberseguridad | KB de referencia: ataques, CVSS, defensa |
| debug | diagnóstico Supabase/Next.js/Edge — poco aplicable a este stack Python |

## Convenciones del proyecto

- No hay CLAUDE.md ni AGENTS.md propios en este repo (greenfield). Gobernanza del workspace padre (trazzos-dev-system/AGENTS.md) aplica a los agentes, no al código entregable.
- El repo de entrega debe ser autocontenido: el jurado solo ve este repositorio.

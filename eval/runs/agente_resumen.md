## Resultados `agente` (16 preguntas dentro del corpus + 4 de control)

| Metrica | Que mide | `agente` | `cabecera` | Delta |
|---|---|---|---|---|
| faithfulness | Fidelidad al contexto (generacion) | 0.991 | 0.984 | +0.007 |
| answer_relevancy | Relevancia de la respuesta (generacion) | 0.951 | 0.926 | +0.026 |
| context_precision | Precision del contexto (recuperacion) | 0.958 | 0.960 | -0.002 |
| context_recall | Cobertura del contexto (chunking/recuperacion) | 1.000 | 1.000 | +0.000 |
| hit_articulo | Acierto de articulo en top_k (sin LLM) | 1.000 | 1.000 | +0.000 |
| rechazo_correcto | Rechazo correcto en preguntas de control | 0.500 | 0.750 | -0.250 |

Citas de articulos que no estaban en el contexto recuperado: 0 (en 0 respuestas).
Las 4 metricas Ragas se promedian sobre las preguntas *dentro del corpus*; las de control (fuera de alcance / sin respuesta) se juzgan por el comportamiento de rechazo.
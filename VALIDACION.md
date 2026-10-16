# Validación de Impresos San Martín

Validación de WhatsApp actualizada el 16 de octubre de 2026; las comprobaciones anteriores de imprenta se conservan como referencia. El catálogo reúne fuentes públicas reales. Las pruebas del programa utilizan conversaciones de ejemplo y simulan los envíos para no contactar clientes; esto no convierte el catálogo en información inventada.

| Área | Resultado observado |
| --- | --- |
| Pruebas automatizadas | 155 pruebas aprobadas. |
| Protocolo MCP | Comunicación real por stdio: consultar solicitud, recuperar fuentes, responder y obtener comprobante. Telegram simulado. |
| Envío directo | Sin aprobación en el panel. Una llamada repetida devuelve el comprobante sin repetir la entrega. |
| Contexto de negocio | Mensajes con identidad de negocio; historial anterior archivado y excluido de Claude, RAG, evidencias y presupuestos. Migración con respaldo privado y control de concurrencia. El archivo se conserva aunque el historial activo alcance su límite de 100 mensajes. |
| Perfil de Telegram | Nombre y descripciones sincronizados en español y por defecto, sin enviar mensajes. |
| Migración de herramientas | Catálogo, estado y solicitud indican usar responder_cliente. El flujo distingue una solicitud pendiente de una entrega incierta o ya completada; guardar_borrador permanece retirada. |
| Concurrencia en MongoDB Atlas | Ocho llamadas simultáneas, una sola entrega en transporte simulado; colecciones temporales eliminadas al terminar. |
| Entrega incierta | Un intento sin confirmación queda bloqueado para impedir duplicados. |
| Historial | Conserva fuentes y análisis de la respuesta enviada, incluso cuando el cliente vuelve a escribir. |
| Navegador | Catálogo, filtros, actualización en vivo, conversación, guía, conocimiento y ficha privada de WhatsApp sin errores de JavaScript. |
| Diseño adaptable | Sin desbordamiento horizontal en 320, 390, 768, 1024 y 1440 píxeles. |
| WhatsApp | 33 comprobaciones de recepción, configuración, aislamiento, destinatarios, deduplicación y estados; transporte de envíos simulado. Acceso real a Meta verificado sin mensajes. |
| Conexión de la demo | 16 pruebas adicionales de registro, reinicio seguro, túnel, metadatos privados y recarga del canal MCP. Registro de Meta simulado. Transporte HTTPS real comprobado sin publicar el panel. El secreto de aplicación continúa pendiente; no se afirma registro real ni recepción de clientes. |
| Catálogo | 8 documentos públicos, catálogo estructurado y 19 variantes de precio de terceros. |
| Privacidad | Credenciales, conversaciones, documentos de trabajo, bases, modelos y registros excluidos de Git. |

## Recuperación documental con modelos reales

El banco congelado de imprenta contiene 52 consultas: 36 con evidencia esperada y 16 ajenas al negocio. La comprobación exige una fuente admisible y los datos esperados en el mismo fragmento; normaliza acentos y separadores de miles. Se ejecuta en un índice temporal separado, sin llamadas a Claude.

- Evidencia encontrada entre los primeros tres resultados: **36 de 36**.
- Evidencia esperada en primer lugar: **34 de 36**.
- Consultas ajenas rechazadas: **16 de 16**.
- Mediana observada por consulta: aproximadamente **431 ms** en la computadora de desarrollo, con modelos ya preparados.

El banco se utilizó para ajustar y comprobar los filtros; es una prueba de regresión pequeña, no una medición independiente de precisión general. El índice de trabajo tenía 9 fuentes y 53 fragmentos al validar. Los resultados dependen del contenido y la configuración. Se conservan las 52 consultas anteriores sin cambiarlas; se actualizó el documento operativo. Los encabezados separan secciones y la inferencia cuantizada por pasaje y por pareja evita variaciones causadas por otros textos del lote.

```bash
uv run --locked pytest -q
.venv/bin/python -m aiec rag-eval
```

## Alcance

Se comprobó la integración de herramientas y los mecanismos de entrega con transporte simulado. No se hicieron llamadas reales a la API de Anthropic ni se enviaron mensajes de prueba a clientes. La clave de esa API sigue vacía y la generación en segundo plano está desactivada.

El flujo principal utiliza Claude Desktop y su herramienta `responder_cliente`: cuando Claude la llama, el programa envía por el canal de la conversación, WhatsApp o Telegram, sin aprobación adicional en el panel. Claude necesita una sesión activa para redactar. Su calidad de redacción y límites de uso dependen de la aplicación y cuenta del usuario.

Las citas y controles de importe reducen errores, pero no prueban por sí solos todas las conclusiones del modelo. Los precios investigados pertenecen a proveedores externos y su vigencia comercial debe confirmarse con ellos.

La recepción pública de WhatsApp requiere el secreto de la aplicación y una dirección HTTPS configurada en Meta. Validar el token y el número no prueba esa recepción. La cuenta consultada identifica el registro como Test Number. No se realizaron envíos reales por WhatsApp.

# WhatsApp Cloud API de Meta

El proyecto admite WhatsApp y Telegram. Cada conversación conserva su canal y destinatario; Claude no puede cambiarlos desde sus herramientas. La conexión de Meta recibe y envía mensajes de WhatsApp. La generación de respuestas sigue correspondiendo a Claude.

## Configuración privada

```dotenv
WHATSAPP_ENABLED=false
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_GRAPH_VERSION=v26.0
WHATSAPP_WEBHOOK_PORT=5051
TELEGRAM_ENABLED=true
```

Los valores reales se guardan únicamente en `.env`, con permisos privados. El **token de acceso** permite usar la API de WhatsApp. El **secreto de la aplicación** permite verificar las firmas de los eventos. El **token de verificación** es una cadena aleatoria propia que se copia en Meta para verificar la dirección del webhook. Son tres valores distintos.

1. Abre `Configurar WhatsApp.command` o ejecuta `.venv/bin/python -m aiec configure-whatsapp`. Solo pide los valores que falten, oculta los secretos y habilita el canal. Conserva las credenciales existentes y la configuración del motor de Claude.
2. Ejecuta `.venv/bin/python -m aiec whatsapp-check`. Comprueba acceso al número y su pertenencia a la cuenta sin enviar mensajes. Si Meta identifica el registro como `Test Number`, configura un número de negocio para atender a clientes reales; no confundas el número de prueba con el de la imprenta.
3. Reinicia el programa con `.venv/bin/python -m aiec run`. El panel continúa en `127.0.0.1:5050`. El receptor aislado de WhatsApp utiliza `127.0.0.1:5051`.
4. Conecta una dirección HTTPS pública estable al puerto **5051** mediante tu servidor o proxy. En Meta, configura la dirección de devolución terminada en `/webhook/whatsapp`, verifica con el token mostrado en la ficha privada y suscribe el campo `messages` de la aplicación a la cuenta de WhatsApp Business. **El puerto del panel no se publica.** Un túnel temporal puede servir para desarrollar, pero su dirección cambiante no sustituye una dirección estable.
5. Abre `/whatsapp` en el panel para consultar los pasos y la última comprobación. El token de acceso y el secreto de la aplicación nunca se muestran; el token de verificación se muestra solamente en esa ficha local.

`TELEGRAM_ENABLED=false` desactiva Telegram al reiniciar, si se desea utilizar únicamente WhatsApp. Las conversaciones guardadas se conservan. Nunca se envía una respuesta de Telegram a un número de WhatsApp ni a la inversa.

## Recepción y entrega

- GET `/webhook/whatsapp`: valida `hub.verify_token` y devuelve `hub.challenge`.
- POST `/webhook/whatsapp`: exige `X-Hub-Signature-256`, calculada mediante HMAC SHA-256 con el secreto de aplicación sobre el cuerpo original. Comprueba también cuenta y número; no acepta eventos de otro negocio.
- Antes de confirmar la recepción se guarda el texto en una bandeja persistente. Un trabajador lo incorpora a la conversación. Los reintentos de Meta se deduplican por identificador, incluso después del límite de 100 mensajes del historial activo. Los eventos deduplicados caducan a los 30 días.
- Una pérdida de conexión con MongoDB devuelve HTTP 503 para permitir un reintento. Los fallos de firma no modifican conversaciones. La URL de verificación y los cuerpos no se registran en el acceso al receptor.
- Solo se reciben mensajes de texto. Imágenes, audios, documentos y eventos no admitidos no se presentan como texto leído por Claude.
- El envío utiliza exclusivamente `POST /{phone-number-id}/messages` con `type=text`, sin plantillas. Se bloquea cuando han transcurrido 24 horas desde el mensaje del cliente, usando la hora original informada por Meta.
- La aceptación de Meta devuelve un identificador del proveedor. `sent=true` confirma esa aceptación, **no la entrega ni la lectura**. Los avisos `sent`, `delivered`, `read` y `failed` actualizan un estado independiente. Un aviso atrasado no revierte una lectura y un aviso recibido antes de confirmar el envío no se pierde.
- No se repiten automáticamente envíos inciertos. Tampoco se convierten en campañas o plantillas cuando cierra la ventana de atención.

Meta publica que las respuestas de servicio dentro de la ventana de 24 horas no tienen cargo. Otros tipos de mensajes tienen sus propias condiciones; este conector no los implementa. Esto no cubre posibles cargos del motor de IA. Consulta la [tarifa oficial de WhatsApp Business Platform](https://business.whatsapp.com/products/platform-pricing).

## Atención continua

El canal de WhatsApp no sustituye la API de Claude. Con `AUTO_REPLY_ENABLED=false`, Claude Desktop necesita una tarea activa y luego `responder_cliente` entrega por el canal correcto. Con una clave de Claude y créditos disponibles, `AUTO_REPLY_ENABLED=true` habilita el trabajador continuo que ya incluye el proyecto. El configurador de WhatsApp no activa esta opción ni compra créditos.

## Fuentes y comprobaciones

Se utiliza HTTP directo a Graph API, sin dependencias del SDK antiguo. La versión se configura explícitamente; la conexión de Meta se comprobó en v26.0. Las credenciales privadas no forman parte de los ejemplos ni de las pruebas.

- [Cloud API y colección oficial de Meta](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api).
- [Descripción de verificación de webhooks publicada por Meta](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/). El SDK está archivado; se usa esta referencia para el protocolo, no como dependencia.

Las pruebas del conector usan transporte simulado y cubren firmas, aislamiento, duplicados, recuperación de recepción, destinatarios, ventana de servicio, errores, envíos inciertos y orden de confirmaciones. `whatsapp-check` es la comprobación real de solo lectura; no demuestra que la dirección pública ya esté suscrita ni que un envío haya sido entregado.

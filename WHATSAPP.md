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

## Conectar una demo con el número de prueba

El `Test Number` de Meta sirve para esta demo con destinatarios de prueba agregados y verificados. No hace falta registrar otro número para comprobar el proyecto.

1. Instala el conector del túnel en Mac: `brew install cloudflared`. No requiere comprar un dominio ni crear una cuenta de Cloudflare para la demo.
2. Abre **`Conectar WhatsApp.command`**. Solo pide los datos privados que falten. El secreto de la aplicación se obtiene en Meta → tu aplicación → Configuración → Básica; no viene incluido en el token de acceso y no debe inventarse.
3. El conector comprueba acceso al número, cuenta y secreto antes de reiniciar AIEC. Prepara o reutiliza una dirección HTTPS hacia el puerto del receptor, reinicia exclusivamente la instancia de este proyecto, comprueba la dirección y registra tanto el webhook de la aplicación como la suscripción de la cuenta en Meta. Conserva un respaldo privado de la suscripción anterior. No envía mensajes a teléfonos.
4. Abre `/whatsapp` en el panel para consultar la dirección y el resultado. Los secretos de acceso y aplicación no se muestran. La comprobación final del registro exige coincidencia de aplicación, dirección activa y campo `messages`.
5. En Meta → WhatsApp → Pruebas de API, selecciona el `Test Number` en **De**, agrega y verifica tu celular en **Para** y utiliza el botón de Meta para recibir su saludo de prueba. Puedes responder en ese chat desde el teléfono autorizado. Los mensajes solo aparecerán en AIEC cuando su receptor esté activado y suscrito.
6. Claude Desktop recoge el canal habilitado al utilizar las herramientas. Si actualizas desde una versión anterior del programa, reinicia Claude una vez para cargar el código nuevo. El envío es directo; la generación de Claude continúa con las condiciones explicadas abajo.

La dirección gratuita termina en `trycloudflare.com`, funciona mientras el proceso del túnel está abierto y cambia si se crea otro túnel. Es una conexión para demostraciones, sin garantía de disponibilidad. La Mac y AIEC deben permanecer encendidos y conectados. Consulta las [condiciones de Quick Tunnels de Cloudflare](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

Para ejecutar los pasos por separado:

```bash
.venv/bin/python -m aiec configure-whatsapp
.venv/bin/python -m aiec whatsapp-tunnel
.venv/bin/python -m aiec whatsapp-connect
```

Crear el túnel por sí solo no configura Meta ni habilita una recepción sin secreto. `whatsapp-connect` valida antes de registrar y solo declara éxito después de comprobar ambas suscripciones. Si otro programa ocupa el puerto, no lo detiene. Los registros y metadatos del túnel quedan en `.runtime/whatsapp/` con permisos privados.

## Renovar un token temporal

Si Meta devuelve el código 190 y subcódigo 463, el token está caducado. Genera otro en WhatsApp → Pruebas de API de la misma aplicación y abre **`Renovar WhatsApp.command`**. Solicita el token nuevo de forma oculta y conserva los identificadores y el secreto ya guardados; si falta el secreto, también lo pide. Valida los datos antes de registrar y no activa la API de Claude.

```bash
.venv/bin/python -m aiec configure-whatsapp --renew-token
.venv/bin/python -m aiec whatsapp-connect
```

El token y el secreto deben pertenecer a la misma aplicación. Crear otra aplicación es una alternativa; en ese caso también deben configurarse su acceso al número, token, secreto y suscripciones correspondientes. Para recuperar una sesión temporal caducada puedes conservar la aplicación existente. El configurador evita que una ventana antigua sobrescriba cambios más recientes en `.env`.

## Dirección estable y configuración manual

Para una instalación pública utiliza una dirección HTTPS estable hacia el puerto del receptor. En Meta configura la devolución terminada en `/webhook/whatsapp`, verifica con el token de la ficha privada y suscribe `messages` y la aplicación a la cuenta de WhatsApp. **El puerto 5050 del panel no se publica.** Un número comercial y las condiciones de producción deben completarse antes de abrir la atención al público.

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

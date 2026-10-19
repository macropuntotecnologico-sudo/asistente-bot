# Asistente personal por Telegram

Le escribes o le mandas una nota de voz, como a una persona, y te ayuda con:

- **Recordatorios**, de una vez o que se repiten ("todos los lunes a las 8"). El aviso llega con tres botones: **Hecho**, **+1 hora** y **Mañana**.
- **Lista de pendientes**: anotar, revisar y marcar como hechos.
- **Resumen diario** a las 7:00 am con lo del día.

Funciona en hora de Bogotá y solo le responde a tu cuenta de Telegram.

---

## 1. Antes de empezar

1. **El bot.** Si todavía no lo tienes, créalo en Telegram con **@BotFather** (`/newbot`). Para ver el token de un bot que ya existe: `/mybots` > tu bot > *API Token*.
2. **La clave de Claude.** Entra a **console.anthropic.com**, ve a *API Keys* y crea una clave. En *Billing* carga saldo. Te conviene poner un límite de gasto mensual en *Limits*.
3. **Un servidor encendido siempre.** Lo recomendado es un contenedor LXC en Proxmox:
   - Plantilla **Debian 12**.
   - 1 CPU, **2 GB de RAM** y 8 GB de disco.
   - Red con salida a internet.

   No hace falta IP pública ni abrir puertos.

## 2. Instalación (un comando)

En la consola del LXC (Proxmox > el contenedor > *Console*), como root:

```bash
apt update && apt install -y curl
bash <(curl -fsSL https://raw.githubusercontent.com/macropuntotecnologico-sudo/asistente-bot/main/instalar.sh)
```

El instalador:

1. instala lo necesario;
2. te pide el token del bot y la clave de Claude, y revisa que funcionen;
3. te pide que le escribas `/start` al bot y así detecta tu ID de Telegram;
4. deja el bot como servicio, que arranca solo cada vez que prende el servidor.

Para probarlo, escríbele: *"recuérdame en 2 minutos probar el bot"*.

La primera vez que arranca, el bot descarga el modelo de notas de voz (unos 500 MB). Mientras termina, las notas de voz pueden tardar. El texto funciona desde el primer momento.

## 3. Cómo se usa

Háblale normal:

- "Recuérdame mañana a las 9 llamar al proveedor"
- "En 20 minutos sacar el cargador"
- "De lunes a viernes a las 7 pm cerrar caja"
- "El 5 de cada mes pagar el hosting"
- "Anota cotizar paneles para el viernes"
- "¿Qué tengo pendiente?" / "Ya hice lo de los paneles"
- "Cambia lo del proveedor para las 10" / "Cancela lo de cerrar caja"

Si no le dices la hora, usa estas: en la mañana 8:00, al mediodía 12:00, en la tarde 3:00 pm y en la noche 7:00 pm. Siempre te confirma la hora que eligió.

| Atajo | Qué muestra |
|---|---|
| `/hoy` | Lo que te avisa hoy y tus pendientes |
| `/recordatorios` | Todo lo programado |
| `/pendientes` | La lista de pendientes |
| `/ayuda` | Ejemplos |

Sobre los botones del aviso:

- En un recordatorio que se repite, **+1 hora** y **Mañana** crean un aviso aparte. La repetición sigue igual.
- Si el servidor estuvo apagado, los avisos que se perdieron salen apenas vuelve, marcados como atrasados.

## 4. Mantenimiento

Desde la consola del servidor:

| Comando | Para qué |
|---|---|
| `asistente logs` | Ver qué está pasando (Ctrl+C para salir) |
| `asistente estado` | Ver si está corriendo |
| `asistente config` | Cambiar la configuración; reinicia solo al guardar |
| `asistente respaldo` | Copia de tus recordatorios en `/opt/asistente/datos/` |
| `asistente actualizar` | Instalar la última versión; conserva tu configuración |

El respaldo funciona aunque el bot esté corriendo. Puedes programarlo con cron y mandar el archivo a tu Hetzner Storage Box junto con lo demás.

### Opciones de la configuración

Estas opciones están en `/opt/asistente/.env` y se cambian con `asistente config`:

| Variable | Para qué sirve |
|---|---|
| `NOMBRE_USUARIO` | Cómo te llama el asistente |
| `CLAUDE_MODELO` | `claude-haiku-5-5` (económico) o `claude-sonnet-5-5` (entiende mejor los pedidos enredados) |
| `RESUMEN_HORA` | Hora del resumen diario, por ejemplo `06:30`; `off` lo desactiva |
| `WHISPER_MODELO` | `small` (recomendado), `base` (gasta menos RAM) u `off` (sin notas de voz) |
| `ZONA_HORARIA` | `America/Bogota` por defecto |

## 5. Costos y privacidad

- **Telegram:** gratis.
- **Claude:** pagas por uso a Anthropic. Los atajos (`/hoy`, `/pendientes`…) y los botones no gastan.
- **Notas de voz:** gratis. Se transcriben en tu servidor y el audio no sale de ahí. A Anthropic solo llega el texto.
- Claude solo puede manejar recordatorios y pendientes; no tiene acceso a nada más del servidor.
- Tus claves quedan solo en `/opt/asistente/.env`, y ese archivo solo lo lee root. Este repositorio no tiene ninguna clave.

## 6. Si algo falla

| Síntoma | Qué revisar |
|---|---|
| No responde | Corre `asistente logs`. Si aparece "Mensaje ignorado de un usuario no autorizado (id …)", ese id es el tuyo: ponlo en `TELEGRAM_USER_ID` con `asistente config`. Si ves "Conflict", hay otra copia corriendo con el mismo token: apágala. |
| "La clave de Anthropic no es válida" | Revisa `ANTHROPIC_API_KEY` con `asistente config` y que la cuenta tenga saldo. |
| "El modelo … no está disponible" | Revisa `CLAUDE_MODELO`. |
| Las notas de voz fallan | El servidor necesita salida a internet la primera vez, para descargar el modelo desde huggingface.co. Si le falta RAM, usa `WHISPER_MODELO=base`. |

## Alternativa: Docker

Si prefieres Docker (en una VM, o en un LXC con *nesting* activado):

```bash
git clone https://github.com/macropuntotecnologico-sudo/asistente-bot.git && cd asistente-bot
cp .env.example .env && nano .env      # token y clave; TELEGRAM_USER_ID vacío por ahora
docker compose up -d --build
```

Escríbele `/start` al bot: te contesta con tu ID. Ponlo en `.env` y corre `docker compose up -d`. Ojo: `docker compose restart` **no** vuelve a leer el `.env`.

# Bot de Telegram con Gemini (capa gratuita)

## Configuración
1. Token de Telegram: @BotFather -> /newbot
2. API key de Gemini: https://aistudio.google.com -> Get API key
3. Instalar y ejecutar:

    pip install -r requirements.txt
    export TELEGRAM_TOKEN="tu_token"
    export GEMINI_API_KEY="tu_clave"
    export ALLOWED_USERS="123456,789012"   # opcional
    python bot.py

## Comandos
/reset, /modelo flash|lite|pro, /buscar, /persona <texto>, /estado, /id, /ayuda

## Despliegue 24/7
Súbelo a Render, Railway o Fly.io como worker con el comando `python bot.py`
y las variables de entorno anteriores.

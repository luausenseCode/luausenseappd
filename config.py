"""luauwhitelist - общая конфигурация (бота и веб-дашборда)."""
import os

# Токен дискорд-бота
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Публичный адрес приложения (например https://luauwhitelist.fly.dev).
# Нужен для формирования ссылок вида /raw/xxxx.lua в loadstring.
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8080").rstrip("/")

# Папка с данными (на fly.io сюда монтируется volume)
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

# Пароль на веб-дашборд
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "admin")

# Порт веб-сервера
PORT = int(os.getenv("PORT", "8080"))

# Срок жизни пробного ключа (дней) - как в тексте "get trial 7 days key"
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))

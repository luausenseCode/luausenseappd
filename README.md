# luauwhitelist

Discord-бот с панелями вайтлиста в стиле Luarmor + веб-дашборд для управления ключами.
Оба живут в одном Docker-контейнере и деплоятся на **fly.io** одной командой.

```
luauwhitelist/
├── bot.py            # Discord-бот (панели, кнопки, ключи)
├── web.py            # Веб-дашборд FastAPI (Key / Loader / External Key)
├── db.py             # SQLite: панели, ключи, лоадеры
├── config.py         # ENV-настройки
├── templates/        # HTML дашборда
├── static/           # CSS + JS
├── requirements.txt
├── Dockerfile
├── start.sh          # поднимает бота и веб одновременно
└── fly.toml          # конфиг fly.io
```

---

## Возможности

### Бот (слэш-команды)

| Команда | Кто может | Что делает |
|---|---|---|
| `/createscriptpanel` | `Manage Server` | Панель скрипта (кнопка **Get Script**) |
| `/createexternalpanel` | `Manage Server` | Панель экстернала (кнопка **Get Loader**) |
| `/deletescriptpanel [panel_id]` | `Manage Server` | Удалить панель скрипта (без id — покажет список) |
| `/deleteexternalpanel [panel_id]` | `Manage Server` | Удалить панель экстернала |
| `/generate100scriptkey [count]` | `Administrator` | Сгенерировать ключи скрипта (по умолчанию 100) |
| `/generate100extkey [count]` | `Administrator` | Сгенерировать ключи экстернала |
| `/generatetrialkey <kind> [count]` | `Administrator` | Пробные ключи на 7 дней |
| `/panelinfo` | все | Список панелей сервера |

### Кнопки панели

1. 🟢 **Redeem Key** — доступна **всем ролям**, открывает модалку для ввода ключа.
   После успешного ввода ключ привязывается к аккаунту и выдаётся HWID.
2. **Get Script** (в панели экстернала — **Get Loader**) — отдаёт
   `loadstring(game:HttpGet("https://твой-домен/raw/xxx.lua"))()`.
3. 🔵 **Stats** — ключ, HWID, срок, количество выполнений, сбросы HWID.
4. 🔴 **Reset HWID** — генерирует новый HWID.
5. 🟢 **Get Role** — выдаёт роль, **только после того как ключ активирован**.

Если человек нажал любую кнопку кроме **Redeem Key** без активированного ключа —
бот отвечает:

> **NOT WHITELISTED**
> buy and redeem key or get trial 7 days key

### Кастомизация панели

В `/createscriptpanel` и `/createexternalpanel` доступны:
`title`, `author` (надпись над названием), `description`, `thumbnail` (URL),
`image` (баннер), `footer`, `color` (`black` по умолчанию — та самая чёрная полоска слева),
`file` (сам .lua файл) или `script_url` (готовая сырая ссылка),
`role1/role2/role3` (роли, которым доступны кнопки) и `channel`.

### Сайт (дашборд)

Три вкладки слева:

* **Key** — создание ключей скрипта (amount / срок / заметка), таблица ключей,
  удаление, бан/разбан, сброс HWID, поиск.
* **Loader** — **Make loader**: загружаешь `.lua` (или вставляешь raw URL) →
  получаешь готовый `loadstring(game:HttpGet("<твой-домен>/raw/xxx.lua"))()`.
* **External Key** — то же самое, что Key, но для панели экстернала.

---

## Локальный запуск

```bash
python -m venv venv
venv\Scripts\activate          # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt

# Windows
set DISCORD_TOKEN=...
set PUBLIC_URL=http://localhost:8080
set WEB_PASSWORD=1234
# Linux/Mac: export DISCORD_TOKEN=... и т.д.

python bot.py                                  # терминал 1
uvicorn web:app --host 0.0.0.0 --port 8080    # терминал 2
```

Дашборд: http://localhost:8080 (пароль = `WEB_PASSWORD`).

---

## Деплой на fly.io

1. Установи `flyctl`: https://fly.io/docs/flyctl/install/
2. Логин и создание приложения:

```bash
fly auth login
fly apps create luauwhitelist        # если приложение ещё не создано
fly volumes create luauwhitelist_data --region waw --size 1
```

3. Задай секреты:

```bash
fly secrets set DISCORD_TOKEN=твой_токен
fly secrets set WEB_PASSWORD=пароль_для_дашборда
fly secrets set PUBLIC_URL=https://luauwhitelist.fly.dev
```

4. Деплой:

```bash
fly deploy
fly logs
```

Готово: бот онлайн, дашборд на `https://luauwhitelist.fly.dev`.

> В `fly.toml` поменяй `primary_region` на ближайший (`fra`, `waw`, `ams`, `iad`, `sin`),
> и имя volume в `[[mounts]]` должно совпадать с созданным.

---

## Важные мелочи

* **Intents.** В Discord Developer Portal → Bot включи **SERVER MEMBERS INTENT**
  (нужен для выдачи ролей). Без него кнопка Get Role будет падать с `Forbidden`.
* **Иерархия ролей.** Роль бота должна быть **выше** ролей, которые он выдаёт.
* **Цвет кнопок.** Discord поддерживает только зелёный/красный/синий/серый —
  «жёлтой» кнопки нет, поэтому Get Script сделана серой (`secondary`).
* **PUBLIC_URL** обязателен: от него зависят ссылки `/raw/*.lua` в loadstring.
  После смены домена пересоздай лоадеры.
* База — обычный SQLite в volume (`/data/luauwhitelist.db`), бот и сайт пишут в неё
  одновременно (включён WAL + busy timeout).
* HWID генерируется ботом при активации ключа — Discord не даёт реальный HWID железа.

### Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DISCORD_TOKEN` | — | токен бота |
| `PUBLIC_URL` | `http://localhost:8080` | публичный адрес приложения |
| `WEB_PASSWORD` | `admin` | пароль дашборда |
| `DATA_DIR` | `./data` (в Docker `/data`) | где лежит БД и .lua файлы |
| `PORT` | `8080` | порт веба |
| `TRIAL_DAYS` | `7` | срок пробного ключа |

# DataProcessor — средняя погода по России

Программа на Python, которая запрашивает текущую температуру в российских городах из
[Open-Meteo](https://open-meteo.com/), считает среднее арифметическое и публикует
итоговый отчёт. Контрольная работа по варианту 3, выполнено на все три уровня
**УД / ХОР / ОТЛ**.

---

## Содержание

- [Что делает](#что-делает)
- [Архитектура](#архитектура)
- [Структура репозитория](#структура-репозитория)
- [Уровни задания](#уровни-задания)
- [Запуск](#запуск)
  - [Локально без Docker](#локально-без-docker)
  - [Через Docker Compose](#через-docker-compose)
  - [В GitHub Actions](#в-github-actions)
- [Конфигурация (S3)](#конфигурация-s3)
- [Формат итогового отчёта](#формат-итогового-отчёта)
- [Детали реализации](#детали-реализации)

---

## Что делает

1. Берёт офлайн-датасет российских городов (`data/russia-cities.json`, ~1100 городов,
   83 региона) — координаты и численность населения.
2. Отбирает по 3 крупнейших города из каждого региона → **~238 точек измерения**
   с равномерным территориальным покрытием.
3. Асинхронно запрашивает у Open-Meteo текущую температуру (`temperature_2m`) во всех
   выбранных городах.
4. Считает среднее арифметическое по полученным значениям **в самой программе**
   (не берёт готовое число извне — это явное требование задания).
5. Сохраняет отчёт в `output/latest_report.json` и (опционально) заливает его
   в S3-совместимое хранилище.

Пример вывода в консоль:

```
Отобрано 238 городов из 83 регионов
Запрашиваем погоду в 238 городах (5 батчей по 50)...
Получено 238 из 238
Средняя температура по России: 18.74 °C
Локально:   output/latest_report.json
S3:         https://storage.yandexcloud.net/<bucket>/weather/2026-05-06T11-17-18Z.json
```

---

## Архитектура

```
russia-cities.json (1102 города)
        │
        ▼
   filter.py  ── группирует по region.label, берёт топ-3 по population
        │
        ▼
cities_filtered.json (~238 городов: name, region, lat, lon, population)
        │
        ▼
   fetch.py   ── httpx.AsyncClient + asyncio.Semaphore
        │         батчи по 50 городов, до 3 одновременных запросов
        │         retry на 429 с exponential backoff (1s → 2s → 4s → 8s)
        ▼
   build_report() ── считает sum(t)/len(t)
        │
        ├──► save_locally() → output/latest_report.json
        └──► upload_to_s3() → s3://<bucket>/weather/<ISO timestamp>.json
                              (если заданы все 5 env-переменных)
```

Почему два скрипта, а не один: фильтрация городов — операция детерминированная и
дешёвая, её результат можно закешировать и пересобирать только при изменении исходного
датасета. Сетевой пайплайн (`fetch.py`) запускается каждый раз заново.

---

## Структура репозитория

```
.
├── data/
│   └── russia-cities.json        # исходный датасет, 1102 города × 83 региона
├── data-processor/
│   ├── filter.py                 # отбор городов из датасета
│   └── fetch.py                  # параллельный fetch + усреднение + S3
├── output/                       # gitignored
│   ├── cities_filtered.json      # промежуточный результат filter.py
│   └── latest_report.json        # финальный отчёт
├── .github/workflows/
│   └── run.yml                   # CI: filter → fetch → загрузка в S3
├── Dockerfile                    # python:3.12-slim + requirements
├── docker-compose.yml            # сервис weather с env_file: .env
├── requirements.txt              # httpx, boto3, python-dotenv
├── .env                          # gitignored, креды для S3
├── .gitignore
└── .dockerignore
```

---

## Уровни задания

| Уровень | Требование | Что сделано |
|---------|------------|-------------|
| **УД**  | Расчёт и вывод средней погоды по России | `filter.py` + `fetch.py`, среднее считается в `build_report()` (строка 108) |
| **ХОР** | Сохранение результата в S3-хранилище | `upload_to_s3()` в `fetch.py`, поддержка любого S3-compatible бакета (Yandex Cloud, AWS, Cloud.ru, MinIO) через `endpoint_url` |
| **ОТЛ** | Запуск через CI/CD именно процесса парсинга и обработки (а не только сборки) | `.github/workflows/run.yml` — отдельный step `Run weather processor`, который реально стучится в Open-Meteo и заливает результат в S3 |

---

## Запуск

### Локально без Docker

Нужен Python 3.12+ (из-за `itertools.batched`, появившегося в 3.12).

```bash
# 1. Установить зависимости
python -m venv .venv
source .venv/bin/activate          # на Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. (опционально) Задать S3-креды для уровня ХОР
cp .env.example .env               # если есть, иначе создать вручную
# отредактировать .env, см. раздел "Конфигурация (S3)" ниже

# 3. Запустить пайплайн
python data-processor/filter.py    # один раз, пересобирает cities_filtered.json
python data-processor/fetch.py     # каждый раз, основной скрипт
```

Если переменные S3 не заданы — скрипт честно отработает уровень УД, выведет среднее
и сохранит файл локально, а в конец консоли напишет
`S3-учётка не задана — загрузка в облако пропущена`.

### Через Docker Compose

```bash
# .env должен лежать рядом с docker-compose.yml
docker compose up --build
```

`Dockerfile` запускает обе стадии последовательно: `filter.py && fetch.py`.
Результат пишется внутрь контейнера (если нужно достать отчёт наружу — добавьте
volume `./output:/app/output` в `docker-compose.yml`).

### В GitHub Actions

Workflow `Weather Processor` запускается:
- автоматически на `push` в `main` или `dev`;
- вручную через **Actions → Weather Processor → Run workflow** (`workflow_dispatch`).

Шаги job-а:
1. `actions/checkout@v4`
2. `actions/setup-python@v5` — Python 3.12
3. `pip install -r requirements.txt`
4. **`python data-processor/filter.py`** — реальная фильтрация
5. **`python data-processor/fetch.py`** — реальный fetch + загрузка в S3
   (env-переменные подставляются из `secrets`)

---

## Конфигурация (S3)

В корне положить файл `.env` со следующими переменными:

```dotenv
S3_ENDPOINT_URL=https://storage.yandexcloud.net      # эндпоинт провайдера
S3_REGION=ru-central1                                # регион бакета
S3_BUCKET=my-weather-bucket                          # имя бакета
AWS_ACCESS_KEY_ID=YCAJExxxxxxxxxxxxxxxx              # access key
AWS_SECRET_ACCESS_KEY=YCxxxxxxxxxxxxxxxxxxxxxxxxxxxx # secret key
```

Для CI те же ключи нужно положить в **Settings → Secrets and variables → Actions →
Repository secrets** под теми же именами.

Загрузка работает с любым S3-compatible хранилищем — Yandex Object Storage,
AWS S3, Cloud.ru S3, MinIO и т.д. Объект кладётся с `ACL=public-read`, чтобы ссылка
из лога CI открывалась без подписей.

Если **хотя бы одна** из 5 переменных не задана — функция `s3_config()` вернёт
`None`, и шаг загрузки будет пропущен (см. `fetch.py:36-39`).

---

## Формат итогового отчёта

`output/latest_report.json` и тот же объект в S3:

```json
{
  "timestamp": "2026-05-06T11:17:18+00:00",
  "average_temperature_celsius": 18.74,
  "cities_total": 238,
  "cities_succeeded": 238,
  "source": "Open-Meteo"
}
```

- `timestamp` — момент сборки отчёта в UTC (ISO-8601, секунды).
- `average_temperature_celsius` — то самое среднее, округлённое до 2 знаков.
- `cities_total` / `cities_succeeded` — сколько городов запросили и сколько вернули
  валидный ответ (помогает понять, не упал ли результат из-за частичных таймаутов).

В S3 ключи именуются как `weather/2026-05-06T11-17-18Z.json` — таймстамп подставляется
в имя файла, так что каждая выгрузка лежит отдельным объектом, а не перезаписывает
предыдущую.

---

## Детали реализации

### Параллелизм

В `fetch.py` используется связка `httpx.AsyncClient` + `asyncio.gather` +
`asyncio.Semaphore`:

- `BATCH_SIZE = 50` — Open-Meteo принимает координаты пачками через запятую и
  возвращает массив ответов;
- `CONCURRENCY = 3` — одновременно отправляется не более 3 батчей;
- `TIMEOUT = 30.0` — таймаут одного HTTP-запроса;
- `MAX_RETRIES = 4` — попытки на 429 (rate limit) с задержками 1 → 2 → 4 → 8 секунд.

При получении 429 батч ждёт `delay` секунд **внутри семафора** и пробует снова.
На любых других HTTP-ошибках батч отдаёт пустой список, и его города не попадают
в итог (но `cities_total` это учитывает).

### Обработка ошибок

- HTTP-ошибки (`httpx.HTTPError`) ловятся на уровне батча — один упавший батч не
  валит весь пайплайн.
- Невалидные ответы (отсутствует `current.temperature_2m`) пропускаются с логом
  имени города.
- Ошибки S3 (`BotoCoreError`, `ClientError`) ловятся в `main()` — локальный отчёт
  уже сохранён, программа не падает.

### Используемые библиотеки

| Пакет | Зачем |
|-------|-------|
| `httpx` | асинхронный HTTP-клиент с поддержкой `asyncio` |
| `boto3` | загрузка в любое S3-совместимое хранилище |
| `python-dotenv` | подгрузка `.env` для локальной разработки |

Стандартная библиотека: `asyncio`, `json`, `pathlib`, `datetime`, `itertools.batched`,
`collections.defaultdict`.

import asyncio
import json
import os
from datetime import datetime, timezone
from itertools import batched
from pathlib import Path

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent

load_dotenv(ROOT / ".env")

INPUT_PATH = ROOT / "output" / "cities_filtered.json"
LOCAL_OUTPUT_DIR = ROOT / "output"

API_URL = "https://api.open-meteo.com/v1/forecast"
BATCH_SIZE = 50
CONCURRENCY = 3
TIMEOUT = 30.0
MAX_RETRIES = 4

S3_ENV_VARS = (
    "S3_ENDPOINT_URL",
    "S3_REGION",
    "S3_BUCKET",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
)


def s3_config() -> dict[str, str] | None:
    if not all(os.getenv(v) for v in S3_ENV_VARS):
        return None
    return {v: os.environ[v] for v in S3_ENV_VARS}


async def fetch_batch(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cities: list[dict],
) -> list[tuple[dict, float]]:
    params = {
        "latitude": ",".join(str(c["lat"]) for c in cities),
        "longitude": ",".join(str(c["lon"]) for c in cities),
        "current": "temperature_2m",
    }

    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        async with semaphore:
            try:
                response = await client.get(API_URL, params=params)
            except httpx.HTTPError as exc:
                print(f"batch ({len(cities)} городов): {exc}")
                return []

        if response.status_code == 429:
            print(f"429, жду {delay:.1f}с (попытка {attempt})")
            await asyncio.sleep(delay)
            delay *= 2
            continue

        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"batch ({len(cities)} городов): {exc}")
            return []

        data = response.json()
        items = data if isinstance(data, list) else [data]

        results: list[tuple[dict, float]] = []
        for city, item in zip(cities, items):
            try:
                results.append((city, item["current"]["temperature_2m"]))
            except (KeyError, TypeError):
                print(f"{city['name']}: некорректный ответ")
        return results

    print(f"batch failed после {MAX_RETRIES} попыток")
    return []


async def fetch_all(cities: list[dict]) -> list[tuple[dict, float]]:
    batches = [list(b) for b in batched(cities, BATCH_SIZE)]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        batch_results = await asyncio.gather(*(
            fetch_batch(client, semaphore, batch) for batch in batches
        ))

    return [item for batch in batch_results for item in batch]


def build_report(
    results: list[tuple[dict, float]],
    cities_total: int,
) -> dict:
    temperatures = [t for _, t in results]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "average_temperature_celsius": round(sum(temperatures) / len(temperatures), 2),
        "cities_total": cities_total,
        "cities_succeeded": len(results),
        "source": "Open-Meteo",
    }


def save_locally(report: dict) -> Path:
    LOCAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_OUTPUT_DIR / "latest_report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def upload_to_s3(report: dict, config: dict[str, str]) -> str:
    client = boto3.client(
        "s3",
        endpoint_url=config["S3_ENDPOINT_URL"],
        region_name=config["S3_REGION"],
        aws_access_key_id=config["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=config["AWS_SECRET_ACCESS_KEY"],
    )

    key = f"weather/{datetime.now(timezone.utc):%Y-%m-%dT%H-%M-%SZ}.json"
    body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")

    client.put_object(
        Bucket=config["S3_BUCKET"],
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        ACL="public-read",
    )
    return f"{config['S3_ENDPOINT_URL']}/{config['S3_BUCKET']}/{key}"


async def main() -> None:
    with INPUT_PATH.open(encoding="utf-8") as f:
        cities: list[dict] = json.load(f)

    batches_count = (len(cities) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Запрашиваем погоду в {len(cities)} городах "
          f"({batches_count} батчей по {BATCH_SIZE})...")

    results = await fetch_all(cities)
    if not results:
        print("Не удалось получить ни одного значения, выход")
        return

    report = build_report(results, len(cities))
    print(f"Получено {report['cities_succeeded']} из {report['cities_total']}")
    print(f"Средняя температура по России: {report['average_temperature_celsius']} °C")

    local_path = save_locally(report)
    print(f"Локально:   {local_path}")

    config = s3_config()
    if config is None:
        print("S3-учётка не задана — загрузка в облако пропущена")
        return

    try:
        url = upload_to_s3(report, config)
    except (BotoCoreError, ClientError) as exc:
        print(f"ошибка загрузки в S3: {exc}")
        return

    print(f"S3:         {url}")


if __name__ == "__main__":
    asyncio.run(main())
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent

INPUT_PATH = ROOT / "data" / "russia-cities.json"
OUTPUT_PATH = ROOT / "output" / "cities_filtered.json"
PER_REGION = 3

def select_cities(cities: list[dict], per_region: int = PER_REGION) -> list[dict]:
    by_region: dict[str, list[dict]] = defaultdict(list)
    for city in cities:
        by_region[city["region"]["label"]].append(city)

    selected: list[dict] = []
    for region_cities in by_region.values():
        region_cities.sort(
            key=lambda c: c.get("population") or 0,
            reverse=True,
        )
        selected.extend(region_cities[:per_region])
    return selected


def to_minimal(city: dict) -> dict:
    return {
        "name": city["name"],
        "region": city["region"]["name"],
        "lat": city["coords"]["lat"],
        "lon": city["coords"]["lon"],
        "population": city.get("population"),
    }


def main() -> None:
    with INPUT_PATH.open(encoding="utf-8") as f:
        cities: list[dict] = json.load(f)

    selected = select_cities(cities)
    minimal = [to_minimal(c) for c in selected]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(minimal, f, ensure_ascii=False, indent=2)

    regions_count = len({c["region"] for c in minimal})
    print(f"Отобрано {len(minimal)} городов из {regions_count} регионов")


if __name__ == "__main__":
    main()
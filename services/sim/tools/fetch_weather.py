"""Fetch the hourly weather cache from the Open-Meteo Historical Weather API (DS-009).

Run once by a developer; the result is committed so the simulator never calls the API at
runtime (offline, deterministic, edge-ready). Licence: CC BY 4.0, attribution required;
free API for non-commercial use only (https://open-meteo.com/en/terms).

    uv run python services/sim/tools/fetch_weather.py --start 2023-01-01 --end 2025-12-31
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

from penjaga_contract.resources import load_house_document

ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = ("temperature_2m", "relative_humidity_2m", "precipitation")
OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "penjaga_sim" / "data"
CSV_NAME = "weather_elmina_hourly.csv"
META_NAME = "weather_elmina_hourly.meta.json"


def fetch_year(lat: float, lon: float, tz: str, start: date, end: date) -> dict[str, list[object]]:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(VARIABLES),
        "timezone": tz,
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "penjaga-hackathon/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read())
    hourly: dict[str, list[object]] = payload["hourly"]
    return {"time": hourly["time"], **{v: hourly[v] for v in VARIABLES}, "_meta": [payload]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    house = load_house_document()
    loc = house["location"]
    lat, lon, tz = float(loc["latitude"]), float(loc["longitude"]), str(loc["timezone"])
    rows: list[list[object]] = []
    meta: dict[str, object] = {}
    for year in range(args.start.year, args.end.year + 1):
        start = max(args.start, date(year, 1, 1))
        end = min(args.end, date(year, 12, 31))
        data = fetch_year(lat, lon, tz, start, end)
        response_meta = data["_meta"][0]
        assert isinstance(response_meta, dict)
        meta = {
            k: response_meta[k]
            for k in ("latitude", "longitude", "elevation", "timezone", "utc_offset_seconds")
        }
        meta["hourly_units"] = response_meta["hourly_units"]
        for i, t in enumerate(data["time"]):
            rows.append([t, *(data[v][i] for v in VARIABLES)])
        time.sleep(1.0)  # be gentle with the free API
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / CSV_NAME).open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["time_local", *VARIABLES])
        writer.writerows(rows)
    sidecar = {
        "source": "Open-Meteo Historical Weather API (archive), best-match model",
        "endpoint": ENDPOINT,
        "licence": "CC BY 4.0 (https://open-meteo.com/en/licence)",
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
        "terms": "Free API for non-commercial use (https://open-meteo.com/en/terms)",
        "data_source_ref": "DS-009",
        "requested": {
            "latitude": lat,
            "longitude": lon,
            "timezone": tz,
            "start_date": args.start.isoformat(),
            "end_date": args.end.isoformat(),
            "hourly": list(VARIABLES),
        },
        "response": meta,
        "precipitation_semantics": "sum over the preceding hour",
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": len(rows),
    }
    (OUT_DIR / META_NAME).write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(rows)} hourly rows to {OUT_DIR / CSV_NAME}")


if __name__ == "__main__":
    main()

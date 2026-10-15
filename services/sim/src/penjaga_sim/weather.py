"""Outdoor weather from the cached Open-Meteo archive (DS-009), aligned to house time.

House dates map onto the same calendar date in a year covered by the cache. If the house year
is in range it is used as is; later years use the latest cached year and earlier years the
earliest. 29 February falls back to the 28th when the target year is not a leap year.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from importlib.resources import files
from pathlib import Path

import numpy as np
import numpy.typing as npt

SECONDS_PER_DAY = 86_400
CSV_NAME = "weather_elmina_hourly.csv"


@dataclass(frozen=True)
class WeatherCache:
    first_date: date
    temp_c: npt.NDArray[np.float64]  # index: hours since first_date 00:00 local
    rh_pct: npt.NDArray[np.float64]
    precip_mm: npt.NDArray[np.float64]  # sum over the preceding hour

    @property
    def last_date(self) -> date:
        return self.first_date + timedelta(days=self.temp_c.size // 24 - 1)

    @property
    def years(self) -> range:
        return range(self.first_date.year, self.last_date.year + 1)


def _cache_path() -> Path:
    return Path(str(files("penjaga_sim") / "data" / CSV_NAME))


@cache
def load_weather_cache(path: Path | None = None) -> WeatherCache:
    path = path or _cache_path()
    times: list[str] = []
    temp: list[float] = []
    rh: list[float] = []
    precip: list[float] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            times.append(row["time_local"])
            temp.append(float(row["temperature_2m"]))
            rh.append(float(row["relative_humidity_2m"]))
            precip.append(float(row["precipitation"]))
    first = datetime.fromisoformat(times[0])
    if first.hour != 0 or len(times) % 24:
        raise ValueError("weather cache must cover whole days starting at 00:00")
    for i in (1, len(times) - 1):  # contiguous hourly series
        if datetime.fromisoformat(times[i]) != first + timedelta(hours=i):
            raise ValueError("weather cache is not a contiguous hourly series")
    return WeatherCache(first.date(), np.array(temp), np.array(rh), np.array(precip))


def map_date(house_date: date, cache: WeatherCache) -> date:
    years = cache.years
    year = min(max(house_date.year, years.start), years.stop - 1)
    try:
        return house_date.replace(year=year)
    except ValueError:  # 29 Feb into a non-leap year
        return house_date.replace(year=year, day=28)


@dataclass(frozen=True)
class DayWeather:
    """Weather for one house day."""

    temp_c: npt.NDArray[np.float64]  # per second, linearly interpolated
    rh_pct: npt.NDArray[np.float64]  # per second, linearly interpolated
    hourly_temp_c: npt.NDArray[np.float64]  # 24 values at 01:00 .. 24:00 (sample timestamps)
    hourly_rh_pct: npt.NDArray[np.float64]
    hourly_precip_mm: npt.NDArray[np.float64]  # preceding-hour sums at 01:00 .. 24:00
    source_date: date

    def shifted(self, temp_offset_c: npt.NDArray[np.float64]) -> DayWeather:
        """Same weather with a per-second temperature offset (heatwave anomaly)."""
        hourly = self.hourly_temp_c + temp_offset_c[np.arange(1, 25) * 3600 - 1]
        return DayWeather(
            self.temp_c + temp_offset_c,
            self.rh_pct,
            hourly,
            self.hourly_rh_pct,
            self.hourly_precip_mm,
            self.source_date,
        )


def day_weather(house_date: date, cache: WeatherCache | None = None) -> DayWeather:
    cache = cache or load_weather_cache()
    source = map_date(house_date, cache)
    h0 = (source - cache.first_date).days * 24
    n_hours = cache.temp_c.size
    idx = np.array([min(h0 + h, n_hours - 1) for h in range(25)])  # 00:00 .. next 00:00
    temp_h, rh_h = cache.temp_c[idx], cache.rh_pct[idx]
    seconds = np.arange(SECONDS_PER_DAY) + 0.5
    hours = np.arange(25) * 3600.0
    return DayWeather(
        temp_c=np.interp(seconds, hours, temp_h),
        rh_pct=np.interp(seconds, hours, rh_h),
        hourly_temp_c=temp_h[1:].copy(),
        hourly_rh_pct=rh_h[1:].copy(),
        hourly_precip_mm=cache.precip_mm[idx[1:]].copy(),
        source_date=source,
    )

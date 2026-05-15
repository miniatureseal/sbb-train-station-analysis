import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Station:
    opuic: str
    name: str
    stop_name: str
    abbreviation: str
    sloid: str
    latitude: float
    longitude: float


def parse_stations(csv_path: Path) -> list[Station]:
    stations:list[Station] = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            geopos = row.get("Geopos", "").strip()
            opuic = row.get("OPUIC", "").strip()

            if not geopos or not opuic:
                continue

            try:
                lat, lon = [float(x.strip()) for x in geopos.split(",")]
            except ValueError:
                continue

            stations.append(
                Station(
                    opuic=opuic,
                    name=row.get("Name station", "").strip(),
                    stop_name=row.get("Stop name", "").strip(),
                    abbreviation=row.get("Station abbreviation", "").strip(),
                    sloid=row.get("sloid", "").strip(),
                    latitude=lat,
                    longitude=lon,
                )
            )
    return stations

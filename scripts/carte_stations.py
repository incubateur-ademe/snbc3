#!/usr/bin/env python3
"""
carte_stations.py
=================
Visualise la distribution géographique des stations Météo-France actives
depuis le fichier cache_dju/postes_meteo.json.

Usage:
    python carte_stations.py                        # HTML interactif (folium)
    python carte_stations.py --mode png             # PNG statique (matplotlib)
    python carte_stations.py --all                  # toutes stations, pas seulement T
    python carte_stations.py --cutoff 2015          # seuil d'activité différent
    python carte_stations.py --output ma_carte.html
"""

import argparse
import json
import sys
from pathlib import Path

POSTES_FILE = Path("./cache_dju/postes_meteo.json")
ACTIVE_CUTOFF_YEAR = 2020
T_KEYWORDS = ("TEMPERATURE MIN", "TEMPERATURE MAX")
OUTPUT_DEFAULT = Path("./carte_stations.html")


def load_stations(cutoff_year: int = ACTIVE_CUTOFF_YEAR, temp_only: bool = True) -> list:
    cutoff_str = f"{cutoff_year}-01-01"

    print(f"Chargement de {POSTES_FILE} ...")
    with open(POSTES_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    stations = []
    for rec in raw:
        date_fin = rec.get("dateFin") or ""
        if date_fin and date_fin < cutoff_str:
            continue

        positions = rec.get("positions", [])
        if not positions:
            continue

        # Position la plus récente
        positions_sorted = sorted(
            positions,
            key=lambda p: p.get("dateFin") or "9999",
            reverse=True,
        )
        pos = positions_sorted[0]
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        if lat is None or lon is None:
            continue
        if not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
            continue

        parametres = rec.get("parametres", [])
        has_temp = any(
            any(kw in p.get("nom", "").upper() for kw in T_KEYWORDS)
            for p in parametres
        )
        if temp_only and not has_temp:
            continue

        stations.append({
            "id": rec.get("id", ""),
            "nom": rec.get("nom", ""),
            "lat": float(lat),
            "lon": float(lon),
            "alt": pos.get("altitude"),
            "dateDebut": (rec.get("dateDebut") or "")[:10],
            "dateFin": date_fin[:10],
        })

    return stations


def make_folium_map(stations: list, output: Path) -> None:
    try:
        import folium
        from folium.plugins import MarkerCluster
    except ImportError:
        sys.exit(
            "folium non installé. Lancez : pip install folium\n"
            "Ou utilisez --mode png pour une carte statique."
        )

    m = folium.Map(location=[46.5, 2.5], zoom_start=6, tiles="CartoDB positron")
    cluster = MarkerCluster().add_to(m)

    for s in stations:
        popup_html = (
            f"<b>{s['nom']}</b><br>"
            f"ID : {s['id']}<br>"
            f"Lat : {s['lat']:.4f} | Lon : {s['lon']:.4f}<br>"
            f"Alt : {s['alt']} m<br>"
            f"Début : {s['dateDebut']}<br>"
            f"Fin : {s['dateFin'] or 'active'}"
        )
        folium.CircleMarker(
            location=[s["lat"], s["lon"]],
            radius=4,
            color="#e74c3c",
            fill=True,
            fill_color="#e74c3c",
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=220),
            tooltip=s["nom"],
        ).add_to(cluster)

    title_html = (
        f'<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);'
        f'background:white;padding:8px 16px;border-radius:4px;font-size:14px;'
        f'font-weight:bold;z-index:9999;box-shadow:0 2px 6px rgba(0,0,0,.3);">'
        f'{len(stations)} stations Météo-France actives (Tmin/Tmax)</div>'
    )
    m.get_root().html.add_child(folium.Element(title_html))

    m.save(str(output))
    print(f"Carte sauvegardée : {output}  ({len(stations)} stations)")


def make_matplotlib_map(stations: list, output: Path) -> None:
    import matplotlib.pyplot as plt

    lats = [s["lat"] for s in stations]
    lons = [s["lon"] for s in stations]

    fig, ax = plt.subplots(figsize=(12, 10))
    hb = ax.hexbin(lons, lats, gridsize=50, cmap="YlOrRd", mincnt=1)
    fig.colorbar(hb, ax=ax, label="Nombre de stations / cellule")
    ax.scatter(lons, lats, s=3, c="#c0392b", alpha=0.4, linewidths=0)

    ax.set_title(
        f"{len(stations)} stations Météo-France actives (Tmin/Tmax)\n"
        f"Seuil activité : dateFin ≥ {ACTIVE_CUTOFF_YEAR}",
        fontsize=13,
    )
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out_png = output.with_suffix(".png")
    plt.savefig(str(out_png), dpi=150, bbox_inches="tight")
    print(f"Carte sauvegardée : {out_png}  ({len(stations)} stations)")


def parse_args():
    p = argparse.ArgumentParser(description="Carte des stations Météo-France actives")
    p.add_argument("--mode", choices=["html", "png"], default="html",
                   help="Format de sortie (défaut : html)")
    p.add_argument("--all", dest="all_stations", action="store_true",
                   help="Inclure toutes les stations (pas seulement celles avec Tmin/Tmax)")
    p.add_argument("--cutoff", type=int, default=ACTIVE_CUTOFF_YEAR,
                   help=f"Année seuil d'activité (défaut : {ACTIVE_CUTOFF_YEAR})")
    p.add_argument("--output", type=Path, default=OUTPUT_DEFAULT,
                   help="Chemin du fichier de sortie")
    return p.parse_args()


def main():
    args = parse_args()
    stations = load_stations(cutoff_year=args.cutoff, temp_only=not args.all_stations)
    print(f"{len(stations)} stations retenues.")

    if args.mode == "html":
        make_folium_map(stations, args.output)
    else:
        make_matplotlib_map(stations, args.output)


if __name__ == "__main__":
    main()

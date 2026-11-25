"""Etapa 05: validação da altura (o tutorial confere o LOD2 contra a nuvem; aqui não há verdade de campo).

Compara a altura estimada (Google 2.5D) com os edifícios do OSM que têm building:levels/height,
transferidos pelo maior recobrimento (>= 50 % do footprint). Rejeita etiquetas implausíveis
(ex.: "Edificio Kolbe" com 55 pavimentos/200 m, que não existe em Rondonópolis).
Uso: python 05_validar_osm.py <pasta_cidade>
"""
import sys, os, json
import numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import Polygon
sys.stdout.reconfigure(encoding="utf-8")
PASTA = sys.argv[1]
PE_DIREITO = 3.0
ed = gpd.read_file(os.path.join(PASTA, "04_modelo", "edificios_lod1.gpkg"))
els = json.load(open(os.path.join(PASTA, "01_brutos", "osm_buildings.json"), encoding="utf-8"))["elements"]
rows = []
for e in els:
    t = e.get("tags", {})
    if e["type"] != "way" or not ("building:levels" in t or "height" in t) or len(e.get("geometry", [])) < 4: continue
    try:
        lv = float(t["building:levels"].replace(",", ".")) if "building:levels" in t else np.nan
        hh = float(t["height"].replace("m", "").replace(",", ".").strip()) if "height" in t else np.nan
    except ValueError:
        continue
    rows.append({"osm_id": e["id"], "nome": t.get("name", ""), "niveis": lv, "altura_osm": hh,
                 "geometry": Polygon([(p["lon"], p["lat"]) for p in e["geometry"]])})
osm = gpd.GeoDataFrame(rows, crs=4326).to_crs(ed.crs)
osm["h_ref"] = osm.altura_osm.fillna(osm.niveis * PE_DIREITO)
osm["implausivel"] = (osm.niveis > 30) | (osm.altura_osm > 120)
inter = gpd.overlay(ed[["proc_id", "measuredHeight", "fonteAltura", "geometry"]], osm, how="intersection", keep_geom_type=True)
inter["frac"] = inter.area / inter.proc_id.map(ed.set_index("proc_id").area)
par = inter[inter.frac >= 0.5].sort_values("frac").drop_duplicates("proc_id", keep="last")
ok = par[~par.implausivel & (par.fonteAltura == "google_2.5d") & ~(par.niveis == 0)]   # levels=0 = cobertura/indefinido
err = ok.measuredHeight - ok.h_ref
rel = {"osm_com_altura": int(len(osm)), "implausiveis_rejeitados": osm.loc[osm.implausivel, ["nome", "niveis", "altura_osm"]].to_dict("records"),
       "pares_validos": int(len(ok)),
       "erro_medio_m": round(float(err.mean()), 2), "erro_absoluto_mediano_m": round(float(err.abs().median()), 2),
       "rmse_m": round(float(np.sqrt((err ** 2).mean())), 2),
       "spearman": round(float(ok[["measuredHeight", "h_ref"]].corr("spearman").iloc[0, 1]), 2) if len(ok) > 2 else None,
       "por_pavimentos_osm": ok.groupby("niveis").measuredHeight.agg(["count", "median"]).round(1).reset_index().to_dict("records")}
json.dump(rel, open(os.path.join(PASTA, "04_modelo", "validacao_osm.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(json.dumps(rel, indent=1, ensure_ascii=False))

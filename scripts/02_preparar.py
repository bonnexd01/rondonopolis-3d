"""Etapa 02 (tutoriais 01_download-prepare-data + 02_prepare-processing + 04_automate-data-preparation).

Mesmos passos do tutorial, trocando Zurique por Rondonópolis:
  tutorial                                   | aqui
  -------------------------------------------|------------------------------------------------
  atribuir EPSG:2056 (CH1903+/LV95)          | EPSG:31981 (SIRGAS 2000 / UTM 21S)
  filtrar footprints ART < 8 (só edifícios)  | só polígonos válidos com área >= AREA_MIN
  Field Enumeration -> proc_id               | idem
  MDT em tiles -> tiles.vrt                  | TOPODATA 16S555 (sem SRC -> EPSG:4326) recortado e reprojetado
  AOI = extensão dos tiles LiDAR             | AOI = mancha urbana (edifícios densos) dentro do município
  edifícios "completely within" AOI          | idem
  unidades de processamento 250 m            | idem, origem arredondada
  edifício -> unidade pelo centróide         | idem

Uso: python 02_preparar.py <pasta_cidade>   (lê <pasta>/01_brutos, grava <pasta>/02_preparados)
"""
import sys, os, gzip, json, math
import numpy as np, geopandas as gpd, pandas as pd, rasterio
from shapely.geometry import shape, box
from shapely.ops import unary_union
from rasterio.warp import reproject, Resampling, calculate_default_transform
from rasterio.crs import CRS

sys.stdout.reconfigure(encoding="utf-8")
PASTA = sys.argv[1]
BR, OUT = os.path.join(PASTA, "01_brutos"), os.path.join(PASTA, "02_preparados")
os.makedirs(OUT, exist_ok=True)

EPSG = 31981          # SIRGAS 2000 / UTM 21S (Rondonópolis, -54.6°)
AREA_MIN = 12.0       # m²: abaixo disso é ruído do detector ou anexo
UNIDADE = 250.0       # m: lado da unidade de processamento (igual ao tutorial)
AOI_BUFFER = 150.0    # m: raio para fundir edifícios vizinhos em mancha urbana
AOI_AREA_MIN = 0.4e6  # m²: manchas menores (sítios, chácaras isoladas) ficam fora

# 1) footprints Microsoft -> SRC do projeto
rows = []
for nome in sorted(os.listdir(BR)):
    if nome.startswith("ms_buildings_") and nome.endswith(".csv.gz"):
        with gzip.open(os.path.join(BR, nome), "rt", encoding="utf-8") as f:
            for linha in f:
                d = json.loads(linha)
                rows.append({"confianca": d["properties"].get("confidence"), "geometry": shape(d["geometry"])})
ed = gpd.GeoDataFrame(rows, crs=4326).to_crs(EPSG)
print("footprints brutos:", len(ed))

# 2) filtro (equivalente ao ART < 8): só geometrias válidas e com área mínima
ed = ed[ed.geometry.is_valid & (ed.geometry.geom_type == "Polygon")]
ed = ed[ed.geometry.area >= AREA_MIN].copy()
print("após filtro de área/validade:", len(ed))

# 3) município (IBGE) e AOI = mancha urbana
mun = gpd.read_file(os.path.join(BR, "municipio_5107602.geojson")).to_crs(EPSG)
mun_geom = mun.geometry.union_all()
ed = ed[ed.geometry.centroid.within(mun_geom)]
mancha = unary_union(ed.geometry.centroid.buffer(AOI_BUFFER).values).buffer(-AOI_BUFFER + 30)
partes = [p for p in getattr(mancha, "geoms", [mancha]) if p.area >= AOI_AREA_MIN]
aoi = unary_union(partes).intersection(mun_geom)
aoi = gpd.GeoDataFrame({"nome": ["Rondonópolis - mancha urbana"]}, geometry=[aoi], crs=EPSG)
aoi["area_km2"] = aoi.area / 1e6
aoi.to_file(os.path.join(OUT, "AOI.gpkg"))
print("AOI: %.1f km² em %d parte(s)" % (aoi.area_km2.iloc[0], len(partes)))

# 4) edifícios completamente dentro da AOI + proc_id (Field Enumeration)
ed = ed[ed.geometry.within(aoi.geometry.iloc[0])].copy()
ed["_ord"] = (ed.geometry.centroid.y // 10) * 1e7 + ed.geometry.centroid.x  # norte->sul não importa; só estável
ed = ed.sort_values("_ord").drop(columns="_ord").reset_index(drop=True)
ed["proc_id"] = np.arange(1, len(ed) + 1)
ed["area_m2"] = ed.area.round(2)

# 5) unidades de processamento de 250 m, origem arredondada; edifício -> unidade pelo centróide
xmin, ymin, xmax, ymax = aoi.total_bounds
x0, y0 = math.floor(xmin / UNIDADE) * UNIDADE, math.floor(ymin / UNIDADE) * UNIDADE
c = ed.geometry.centroid
ed["ux"] = (np.floor((c.x - x0) / UNIDADE) * UNIDADE + x0).astype(int)
ed["uy"] = (np.floor((c.y - y0) / UNIDADE) * UNIDADE + y0).astype(int)
ed["unidade"] = ed.ux.astype(str) + "_" + ed.uy.astype(str)
un = ed.groupby("unidade").agg(ux=("ux", "first"), uy=("uy", "first"), n_edificios=("proc_id", "size")).reset_index()
un = gpd.GeoDataFrame(un, geometry=[box(x, y, x + UNIDADE, y + UNIDADE) for x, y in zip(un.ux, un.uy)], crs=EPSG)
un.to_file(os.path.join(OUT, "unidades.gpkg"))
ed.to_file(os.path.join(OUT, "edificios.gpkg"))
print("edifícios na AOI:", len(ed), "| unidades de 250 m com edifícios:", len(un))

# 6) MDT: TOPODATA (GeoTIFF sem SRC -> EPSG:4326), recorte com folga e reprojeção para o SRC do projeto
src_tif = os.path.join(BR, "16S555ZN.tif")
with rasterio.open(src_tif) as ds:
    a = ds.read(1).astype("float32")
    tr_src, crs_src = ds.transform, (ds.crs or CRS.from_epsg(4326))
    w, s, e, n = gpd.GeoSeries([box(*aoi.total_bounds)], crs=EPSG).buffer(500).to_crs(4326).total_bounds
    win = rasterio.windows.from_bounds(w, s, e, n, tr_src).round_offsets().round_lengths()
    a = ds.read(1, window=win).astype("float32"); tr_win = ds.window_transform(win)
dst_tr, W, H = calculate_default_transform(crs_src, CRS.from_epsg(EPSG), a.shape[1], a.shape[0], w, s, e, n, resolution=30)
dst = np.full((H, W), np.nan, "float32")
reproject(a, dst, src_transform=tr_win, src_crs=crs_src, dst_transform=dst_tr, dst_crs=CRS.from_epsg(EPSG),
          resampling=Resampling.bilinear, src_nodata=-32768, dst_nodata=np.nan)
with rasterio.open(os.path.join(OUT, "mdt.tif"), "w", driver="GTiff", width=W, height=H, count=1, dtype="float32",
                   crs=CRS.from_epsg(EPSG), transform=dst_tr, nodata=np.nan, compress="deflate") as o:
    o.write(dst, 1)
print("MDT %dx%d, altitude %.0f–%.0f m" % (W, H, np.nanmin(dst), np.nanmax(dst)))
json.dump({"epsg": EPSG, "unidade_m": UNIDADE, "origem": [x0, y0], "n_edificios": int(len(ed)),
           "n_unidades": int(len(un)), "aoi_km2": float(aoi.area_km2.iloc[0])},
          open(os.path.join(OUT, "resumo_preparo.json"), "w"), indent=1)

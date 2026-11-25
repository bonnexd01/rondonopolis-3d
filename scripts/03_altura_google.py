"""Etapa 03: altura dos edifícios — substitui a nuvem LiDAR do tutorial.

Fonte: Google Open Buildings 2.5D Temporal v1 (Sirko et al. 2023; CC BY 4.0), bucket público
gs://open-buildings-temporal-data. Bandas: building_presence, building_height (m), fractional_count.
Grade de 0,5 m em UTM WGS84 (EPSG:32721 aqui), mas a resolução efetiva do modelo é ~4 m:
lemos pela pirâmide em 2 m (GDAL escolhe o overview), o que não perde informação real.

Uso: python 03_altura_google.py <pasta_cidade> [ano]   -> 02_preparados/google_altura_<ano>.tif (2 bandas: altura, presença)
"""
import sys, os, re, json, math
import numpy as np, geopandas as gpd, rasterio, requests
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.crs import CRS
from shapely.geometry import box
sys.path.insert(0, os.path.dirname(__file__))
from httprange import opener          # leitura por HTTP Range (a CA da rede não é aceita pelo GDAL)
import truststore; truststore.inject_into_ssl()
sys.stdout.reconfigure(encoding="utf-8")

PASTA = sys.argv[1]; ANO = sys.argv[2] if len(sys.argv) > 2 else "2023"
OUT = os.path.join(PASTA, "02_preparados")
B = "https://storage.googleapis.com/open-buildings-temporal-data/"
RES = 2.0
resumo = json.load(open(os.path.join(OUT, "resumo_preparo.json")))
EPSG = resumo["epsg"]
aoi = gpd.read_file(os.path.join(OUT, "AOI.gpkg")).to_crs(EPSG).geometry.iloc[0]
zona = 32700 + (EPSG - 31960) if 31960 < EPSG < 31986 else None   # SIRGAS UTM 18S..25S -> WGS84 UTM S
assert zona, "ajuste a zona UTM para outra cidade"
xmin, ymin, xmax, ymax = [math.floor(v / RES) * RES for v in aoi.buffer(20).bounds[:2]] + \
                         [math.ceil(v / RES) * RES for v in aoi.buffer(20).bounds[2:]]
W, H = int((xmax - xmin) / RES), int((ymax - ymin) / RES)
alt = np.full((H, W), np.nan, "float32"); pres = np.zeros((H, W), "float32")

# manifests da zona/ano -> tiles que tocam a AOI
keys, marker = [], ""
while True:
    r = requests.get(B, params={"prefix": "v1/manifests/", "max-keys": 1000, "marker": marker}, timeout=60).text
    k = re.findall(r"<Key>([^<]*)</Key>", r); keys += k
    if "<IsTruncated>true" not in r: break
    marker = k[-1]
mans = [k for k in keys if f"EPSG_{zona}_{ANO}_06_30" in k]
tiles = []
for mk in mans:
    m = requests.get(B + mk, timeout=120).json()
    pre = m["uriPrefix"].replace("gs://open-buildings-temporal-data/", "")
    for ts in m["tilesets"]:
        for s in ts["sources"]:
            a, d = s["affineTransform"], s["dimensions"]
            bb = box(a["translateX"], a["translateY"] - d["height"] * 0.5, a["translateX"] + d["width"] * 0.5, a["translateY"])
            if bb.intersects(aoi):
                tiles.append(B + pre + s["uris"][0])
print(len(tiles), "tiles de", ANO)

for u in tiles:
    with rasterio.open(u, opener=opener) as ds:
        b = ds.bounds
        ix0, iy0 = max(xmin, b.left), max(ymin, b.bottom); ix1, iy1 = min(xmax, b.right), min(ymax, b.top)
        if ix1 <= ix0 or iy1 <= iy0: continue
        w = from_bounds(ix0, iy0, ix1, iy1, ds.transform)
        shp = (int(round((iy1 - iy0) / RES)), int(round((ix1 - ix0) / RES)))
        h = ds.read(2, window=w, out_shape=shp, resampling=Resampling.average)
        p = ds.read(3, window=w, out_shape=shp, resampling=Resampling.average)
        r0, c0 = int(round((ymax - iy1) / RES)), int(round((ix0 - xmin) / RES))
        sl = (slice(r0, r0 + shp[0]), slice(c0, c0 + shp[1]))
        ok = (h > -90) & (p > -90) & (p > pres[sl])          # tiles vizinhos: fica o de maior presença
        alt[sl][ok] = h[ok]; pres[sl][ok] = p[ok]
        print(os.path.basename(u), shp, "pixels válidos:", int(ok.sum()), "baixados %.0f MB" % (ds._opener_bytes if hasattr(ds, "_opener_bytes") else 0))

dst = os.path.join(OUT, f"google_altura_{ANO}.tif")
with rasterio.open(dst, "w", driver="GTiff", width=W, height=H, count=2, dtype="float32", crs=CRS.from_epsg(EPSG),
                   transform=from_origin(xmin, ymax, RES, RES), nodata=np.nan, compress="deflate", tiled=True) as o:
    o.write(alt, 1); o.write(pres, 2); o.set_band_description(1, "building_height_m"); o.set_band_description(2, "building_presence")
v = alt[pres > 0.5]
print(dst, "| presença>0,5: %d px, altura p50 %.1f p95 %.1f max %.1f m" % (v.size, np.nanmedian(v), np.nanpercentile(v, 95), np.nanmax(v)))

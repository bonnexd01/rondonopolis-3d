"""QA visual: recorte Esri World Imagery (z17) em volta dos N edifícios mais altos, com o footprint e a altura."""
import sys, os, math, io
import truststore; truststore.inject_into_ssl()
import requests, geopandas as gpd, numpy as np
from PIL import Image, ImageDraw
sys.stdout.reconfigure(encoding="utf-8")
PASTA, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 6
Z = 17   # z18 vem "Map data not yet available" no interior do MT
ed = gpd.read_file(os.path.join(PASTA, "04_modelo", "edificios_lod1.gpkg"))
top = ed.nlargest(N, "measuredHeight").to_crs(4326)
def px(lon, lat):
    s = math.sin(math.radians(lat)); n = 256 * 2 ** Z
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
quadros = []
for r in top.itertuples():
    c = r.geometry.centroid; X, Y = px(c.x, c.y); tx, ty = int(X // 256), int(Y // 256)
    img = Image.new("RGB", (768, 768))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            u = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{Z}/{ty+dy}/{tx+dx}"
            img.paste(Image.open(io.BytesIO(requests.get(u, timeout=60).content)).convert("RGB"), ((dx + 1) * 256, (dy + 1) * 256))
    d = ImageDraw.Draw(img); ox, oy = (tx - 1) * 256, (ty - 1) * 256
    d.line([(px(x, y)[0] - ox, px(x, y)[1] - oy) for x, y in r.geometry.exterior.coords], fill=(255, 40, 40), width=3)
    d.rectangle([0, 0, 768, 34], fill=(0, 0, 0)); d.text((8, 8), f"proc {r.proc_id}: {r.measuredHeight:.0f} m  ({c.y:.5f}, {c.x:.5f})", fill=(255, 255, 255))
    quadros.append(img.resize((512, 512)))
cols = 3; rows = math.ceil(len(quadros) / cols)
folha = Image.new("RGB", (512 * cols, 512 * rows))
for i, q in enumerate(quadros): folha.paste(q, ((i % cols) * 512, (i // cols) * 512))
dst = os.path.join(PASTA, "_qa", "mais_altos_esri.jpg"); folha.save(dst, quality=88); print(dst)

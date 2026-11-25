"""Etapa 06: malha para o visualizador Three.js (o tutorial só inspeciona no Point Cloud Editor).

Gera <pasta>/06_visual/data/{predios_pos.bin, predios_cls.bin, mdt.bin, meta.json} e lib/three.min.js a partir de 04_modelo/edificios_lod1.gpkg e do MDT.
predios.bin = Int16 posições (x, z, -y) em unidades de ESC metros + Uint8 classe de altura, triângulos soltos.
Uso: python 06_visualizar.py <pasta_cidade>
"""
import sys, os, json, shutil
import numpy as np, geopandas as gpd, rasterio, shapely
from shapely.geometry.polygon import orient
sys.stdout.reconfigure(encoding="utf-8")
PASTA = sys.argv[1]
VIS = os.path.join(PASTA, "06_visual"); OUT = os.path.join(VIS, "data"); LIB = os.path.join(VIS, "lib")
os.makedirs(OUT, exist_ok=True); os.makedirs(LIB, exist_ok=True)
ESC = 0.5                                   # m por unidade Int16 (±16 km a partir do centro)
ed = gpd.read_file(os.path.join(PASTA, "04_modelo", "edificios_lod1.gpkg"))
ed["geometry"] = ed.geometry.simplify(0.3)
cen = ed.geometry.centroid
cx, cy = float(np.median(cen.x)), float(np.median(cen.y))          # centro da cidade principal
LIM = 32000 * ESC
fora = (np.abs(cen.x - cx) > LIM - 200) | (np.abs(cen.y - cy) > LIM - 200)
if fora.any(): print("fora do alcance do visualizador (ficam só no CityJSON):", int(fora.sum()))
ed = ed[~fora].copy()
z_ref = float(ed.groundZ.median())

tri_roof = shapely.constrained_delaunay_triangles(ed.geometry.values)
P, C = [], []
classes = np.digitize(ed.measuredHeight.values, [6, 9, 15, 24, 36, 60])   # 0..6
for g, trs, z0, z1, k in zip(ed.geometry, tri_roof, ed.groundZ, ed.roofHgtMax, classes):
    g = orient(g, 1.0)
    for t in trs.geoms:                                                     # teto
        (x1, y1), (x2, y2), (x3, y3) = list(orient(t, 1.0).exterior.coords)[:3]
        P += [(x1, y1, z1), (x2, y2, z1), (x3, y3, z1)]; C += [k] * 3
    for anel in [g.exterior, *g.interiors]:                                 # paredes
        pts = list(anel.coords)
        for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):
            P += [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z0), (xb, yb, z1), (xa, ya, z1)]; C += [k] * 6
P = np.array(P, dtype=np.float64)
# eixo Three.js: X = leste, Y = cima, Z = -norte
q = np.stack([(P[:, 0] - cx) / ESC, (P[:, 2] - z_ref) / ESC, -(P[:, 1] - cy) / ESC], 1)
assert np.abs(q).max() < 32767, "cidade grande demais para Int16 com essa escala"
q.round().astype("<i2").tofile(os.path.join(OUT, "predios_pos.bin"))
np.array(C, dtype=np.uint8).tofile(os.path.join(OUT, "predios_cls.bin"))

# terreno (MDT 30 m -> 60 m) recortado à extensão dos prédios + 1,5 km, em Int16 (decímetros)
with rasterio.open(os.path.join(PASTA, "02_preparados", "mdt.tif")) as m:
    xb0, yb0, xb1, yb1 = ed.total_bounds
    win = rasterio.windows.from_bounds(xb0 - 1500, yb0 - 1500, xb1 + 1500, yb1 + 1500, m.transform).round_offsets().round_lengths()
    a = m.read(1, window=win, boundless=True, fill_value=np.nan)[::2, ::2]; tr = m.window_transform(win)
a = np.where(np.isfinite(a), a, np.nanmedian(a))
np.round((a - z_ref) * 10).astype("<i2").tofile(os.path.join(OUT, "mdt.bin"))
mdt = {"w": a.shape[1], "h": a.shape[0], "x0": tr.c - cx, "y0": tr.f - cy, "res": tr.a * 2}
top = ed.nlargest(1, "measuredHeight").iloc[0]
c = top.geometry.centroid
meta = {"n_predios": int(len(ed)), "n_vertices": int(len(P)), "esc": ESC, "centro_utm": [cx, cy], "z_ref": z_ref,
        "epsg": int(ed.crs.to_epsg()), "mdt": mdt,
        "mais_alto": {"x": c.x - cx, "y": c.y - cy, "z": float(top.roofHgtMax - z_ref), "h": float(top.measuredHeight)},
        "classes_m": [6, 9, 15, 24, 36, 60]}
json.dump(meta, open(os.path.join(OUT, "meta.json"), "w"), separators=(",", ":"))
three = os.path.join(os.path.dirname(__file__), "..", "..", "mapa-3d", "three.min.js")
shutil.copy(three, LIB)
print("vértices:", len(P), "| MB:", round(os.path.getsize(os.path.join(OUT, "predios_pos.bin")) / 1e6, 1),
      "| mais alto: %.1f m" % top.measuredHeight)

# ---------------- ruas e nomes (OSM, ODbL) para localizar o modelo ----------------
from shapely.geometry import LineString
from pyproj import Transformer
T = Transformer.from_crs(4326, meta["epsg"], always_xy=True)
Wt, Ht = mdt["w"], mdt["h"]
dx_t, dy_t = Wt * mdt["res"] / (Wt - 1), Ht * mdt["res"] / (Ht - 1)       # espaçamento real dos vértices do PlaneGeometry
zt = (a - z_ref) - 1.5                                                    # mesma altura usada no terreno do visualizador
def z_terreno(e, n):                                                      # e, n relativos ao centro
    c = np.clip((e - mdt["x0"]) / dx_t, 0, Wt - 1.001); r = np.clip((mdt["y0"] - n) / dy_t, 0, Ht - 1.001)
    c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int); fc, fr = c - c0, r - r0
    return (zt[r0, c0] * (1 - fc) * (1 - fr) + zt[r0, c0 + 1] * fc * (1 - fr) +
            zt[r0 + 1, c0] * (1 - fc) * fr + zt[r0 + 1, c0 + 1] * fc * fr)
LARG = {"motorway": (16, 0), "trunk": (14, 0), "primary": (12, 0), "secondary": (10, 1), "tertiary": (8, 1),
        "motorway_link": (7, 1), "trunk_link": (7, 1), "primary_link": (7, 1), "secondary_link": (6, 1), "tertiary_link": (6, 1),
        "unclassified": (5, 2), "residential": (5, 2), "living_street": (4, 2), "service": (3, 3)}
ruas = json.load(open(os.path.join(PASTA, "01_brutos", "osm_ruas.json"), encoding="utf-8"))["elements"]
xb0, yb0, xb1, yb1 = ed.total_bounds
RP, RC, nomes_vias = [], [], {}
for w in ruas:
    hw = w["tags"].get("highway")
    if hw not in LARG or len(w.get("geometry", [])) < 2: continue
    xs, ys = T.transform([p["lon"] for p in w["geometry"]], [p["lat"] for p in w["geometry"]])
    caixa_v = shapely.box(max(xb0 - 1000, cx - LIM + 100), max(yb0 - 1000, cy - LIM + 100),
                          min(xb1 + 1000, cx + LIM - 100), min(yb1 + 1000, cy + LIM - 100))
    ln_full = LineString(zip(xs, ys)).intersection(caixa_v)
    if ln_full.is_empty: continue
    larg, k = LARG[hw]
    for ln in getattr(ln_full, "geoms", [ln_full]):
      if ln.geom_type != "LineString" or ln.length < 1: continue
      ln = ln.segmentize(20)                                             # acompanha o relevo
      pts = np.array(ln.coords) - (cx, cy)
      d = np.diff(pts, axis=0); L = np.hypot(d[:, 0], d[:, 1]); ok = L > 0.01
      if not ok.any(): continue
      nrm = np.stack([-d[:, 1], d[:, 0]], 1) / np.maximum(L, 1e-6)[:, None] * larg / 2
      for (p0, p1, nv) in zip(pts[:-1][ok], pts[1:][ok], nrm[ok]):
          q = [p0 + nv, p0 - nv, p1 - nv, p1 + nv]
          z = z_terreno(np.array([v[0] for v in q]), np.array([v[1] for v in q])) + 1.2
          A_, B_, C_, D_ = [(v[0], zz, -v[1]) for v, zz in zip(q, z)]
          RP += [A_, B_, C_, A_, C_, D_]; RC += [k] * 6
      nm = w["tags"].get("name")
      if nm and k <= 1:                                                     # avenidas: guarda o trecho mais longo
          comp = ln.length; tot = nomes_vias.setdefault(nm, [0.0, None, 0.0])
          tot[0] += comp
          if comp > tot[2]: tot[1], tot[2] = ln, comp
RP = np.array(RP); qr = RP / ESC
assert np.abs(qr).max() < 32767
qr.round().astype("<i2").tofile(os.path.join(OUT, "ruas_pos.bin"))
np.array(RC, dtype=np.uint8).tofile(os.path.join(OUT, "ruas_cls.bin"))

rot = []
for nm, (tot, ln, _) in sorted(nomes_vias.items(), key=lambda kv: -kv[1][0])[:45]:
    if tot < 800: continue                                                # só vias com mais de 800 m nomeadas
    p = ln.interpolate(0.5, normalized=True); e, n = p.x - cx, p.y - cy
    rot.append({"t": nm, "k": "via", "x": round(e, 1), "y": round(float(z_terreno(np.array([e]), np.array([n]))[0]) + 25, 1), "z": round(-n, 1)})
for p in json.load(open(os.path.join(PASTA, "01_brutos", "osm_lugares.json"), encoding="utf-8"))["elements"]:
    t = p.get("tags", {})
    if t.get("place") not in ("suburb", "neighbourhood", "quarter") or "name" not in t: continue
    e, n = T.transform(p["lon"], p["lat"]); e, n = e - cx, n - cy
    if not (xb0 - cx - 500 < e < xb1 - cx + 500 and yb0 - cy - 500 < n < yb1 - cy + 500): continue
    rot.append({"t": t["name"], "k": "bairro", "x": round(e, 1), "y": round(float(z_terreno(np.array([e]), np.array([n]))[0]) + 60, 1), "z": round(-n, 1)})
json.dump(rot, open(os.path.join(OUT, "rotulos.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("ruas: %d triângulos, %.1f MB | rótulos: %d vias, %d bairros" % (len(RP) // 3, os.path.getsize(os.path.join(OUT, "ruas_pos.bin")) / 1e6,
      sum(r["k"] == "via" for r in rot), sum(r["k"] == "bairro" for r in rot)))

"""Etapa 04 (tutoriais 03_process-building-models + 05_automate-data-processing).

Igual ao tutorial: uma tarefa por unidade de 250 m, rodando em paralelo (Run_Parallel_Tasks -> multiprocessing),
estatísticas por edifício com o MDT, junção com os atributos por proc_id, exportação CityJSON 2.0
(Building, Solid, COORD_PRECISION=3) por unidade e da cidade inteira; surfaceClass 0=Ground 1=Wall 2=Roof.

Diferença obrigatória: sem LiDAR não há planos de telhado (RANSAC) -> o modelo é LOD1 (prisma de telhado plano),
com a altura medida no raster do Google Open Buildings 2.5D sobre o footprint, em vez de LOD2.

Uso: python 04_processar.py <pasta_cidade> [ano] [n_processos]
"""
import sys, os, json, math, time
import numpy as np, pandas as pd, geopandas as gpd, rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from shapely.geometry.polygon import orient
from multiprocessing import Pool

# ---- parâmetros (project_config.py do tutorial) ----
H_MIN = 2.5            # m: abaixo disso é artefato (tutorial: MIN_BUILDING_HEIGHT 0.1 m com LiDAR)
H_PADRAO = 3.0         # m: um pavimento, quando o Google não tem pixel válido no footprint (marcado em fonteAltura)
PRES_MIN = 0.3         # presença mínima do pixel para entrar na estatística
PCTL = 75              # percentil da altura dentro do footprint (o raster é suavizado nas bordas)
H_MAX_PLAUSIVEL = 120  # m: acima disso, rejeita (nenhum edifício de Rondonópolis chega perto)
COORD_PRECISION = 3

def stats_unidade(args):
    unidade, sub, f_alt, f_mdt = args
    out = []
    with rasterio.open(f_alt) as ra, rasterio.open(f_mdt) as rm:
        xmin, ymin, xmax, ymax = sub.total_bounds
        w = from_bounds(xmin - 4, ymin - 4, xmax + 4, ymax + 4, ra.transform).round_offsets().round_lengths()
        alt, pres = ra.read(1, window=w, boundless=True, fill_value=np.nan), ra.read(2, window=w, boundless=True, fill_value=0)
        tr = ra.window_transform(w)
        lab = rasterize([(g, i + 1) for i, g in enumerate(sub.geometry)], out_shape=alt.shape, transform=tr,
                        fill=0, dtype="int32")
        mdt = rm.read(1)
        for i, (pid, g) in enumerate(zip(sub.proc_id, sub.geometry)):
            v = alt[(lab == i + 1) & (pres >= PRES_MIN) & np.isfinite(alt)]
            if v.size == 0:                                # footprint menor que o pixel: lê o pixel do centróide
                c = g.representative_point(); r, cc = rasterio.transform.rowcol(tr, c.x, c.y)
                if 0 <= r < alt.shape[0] and 0 <= cc < alt.shape[1] and np.isfinite(alt[r, cc]) and pres[r, cc] >= PRES_MIN:
                    v = np.array([alt[r, cc]])
            if v.size and v.max() <= H_MAX_PLAUSIVEL:
                h, fonte, npx = float(np.percentile(v, PCTL)), "google_2.5d", int(v.size)
            else:
                h, fonte, npx = H_PADRAO, "padrao_1_pavimento", 0
            ajustada = h < H_MIN
            h = max(h, H_MIN)
            # base: menor cota do MDT nos vértices (tutorial: LOD2 Building Statistics com o DTM)
            xs, ys = np.array(g.exterior.coords).T
            rr, cc = rasterio.transform.rowcol(rm.transform, xs, ys)
            rr = np.clip(rr, 0, mdt.shape[0] - 1); cc = np.clip(cc, 0, mdt.shape[1] - 1)
            base = float(np.nanmin(mdt[rr, cc]))
            out.append({"proc_id": int(pid), "measuredHeight": round(h, 2), "groundZ": round(base, 2),
                        "roofHgtMax": round(base + h, 2), "fonteAltura": fonte, "n_pixels": npx,
                        "alturaAjustadaMin": bool(ajustada), "pavimentosEst": max(1, int(round((h - 1.0) / 3.0)))})  # ~1 m de telhado/estrutura
    return unidade, out

def solido(geom, z0, z1, verts, idx):
    """Solid LOD1: chão (normal p/ baixo), paredes, teto. Retorna boundaries + valores semânticos."""
    def vid(x, y, z):
        k = (round(x, COORD_PRECISION), round(y, COORD_PRECISION), round(z, COORD_PRECISION))
        if k not in idx: idx[k] = len(verts); verts.append(k)
        return idx[k]
    g = orient(geom, 1.0)                                   # exterior anti-horário, furos horário
    def limpo(coords):                                       # sem pontos repetidos depois do arredondamento
        out = []
        for x, y in coords[:-1]:
            p = (round(x, COORD_PRECISION), round(y, COORD_PRECISION))
            if not out or p != out[-1]: out.append(p)
        if len(out) > 1 and out[0] == out[-1]: out.pop()
        return out
    aneis = [a for a in [limpo(g.exterior.coords)] + [limpo(r.coords) for r in g.interiors] if len(a) >= 3]
    chao = [[vid(x, y, z0) for x, y in reversed(a)] for a in aneis]
    teto = [[vid(x, y, z1) for x, y in a] for a in aneis]
    paredes = []
    for a in aneis:
        for j in range(len(a)):
            (xa, ya), (xb, yb) = a[j], a[(j + 1) % len(a)]
            paredes.append([[vid(xa, ya, z0), vid(xb, yb, z0), vid(xb, yb, z1), vid(xa, ya, z1)]])
    shell = [chao] + paredes + [teto]
    sem = [0] + [1] * len(paredes) + [2]
    return [shell], [sem]

def cityjson(sub, epsg, origem):
    verts, idx, objs = [], {}, {}
    for r in sub.itertuples():
        b, s = solido(r.geometry, r.groundZ, r.roofHgtMax, verts, idx)
        objs[f"proc_{r.proc_id}"] = {
            "type": "Building",
            "attributes": {k: (lambda v: v.item() if hasattr(v, "item") else v)(getattr(r, k)) for k in ("proc_id", "measuredHeight", "roofHgtMax", "groundZ",
                                                      "fonteAltura", "pavimentosEst", "area_m2", "unidade")},
            "geometry": [{"type": "Solid", "lod": "1", "boundaries": b,
                          "semantics": {"surfaces": [{"type": "GroundSurface"}, {"type": "WallSurface"},
                                                     {"type": "RoofSurface"}], "values": s}}]}
    esc = 10 ** -COORD_PRECISION
    v = np.array(verts) if verts else np.zeros((0, 3))
    tx = [origem[0], origem[1], float(v[:, 2].min()) if len(v) else 0.0]
    vi = np.round((v - tx) / esc).astype(np.int64).tolist()
    return {"type": "CityJSON", "version": "2.0",
            "transform": {"scale": [esc] * 3, "translate": tx},
            "metadata": {"referenceSystem": f"https://www.opengis.net/def/crs/EPSG/0/{epsg}",
                         "title": "Rondonópolis LOD1 — footprints Microsoft ML + altura Google Open Buildings 2.5D",
                         "referenceDate": "2023-06-30"},
            "CityObjects": objs, "vertices": vi}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    PASTA = sys.argv[1]; ANO = sys.argv[2] if len(sys.argv) > 2 else "2023"
    NP = int(sys.argv[3]) if len(sys.argv) > 3 else max(1, os.cpu_count() - 1)
    PREP, OUT = os.path.join(PASTA, "02_preparados"), os.path.join(PASTA, "04_modelo")
    os.makedirs(os.path.join(OUT, "json"), exist_ok=True)
    res = json.load(open(os.path.join(PREP, "resumo_preparo.json")))
    ed = gpd.read_file(os.path.join(PREP, "edificios.gpkg"))
    f_alt, f_mdt = os.path.join(PREP, f"google_altura_{ANO}.tif"), os.path.join(PREP, "mdt.tif")
    t = time.time()
    tarefas = [(u, sub[["proc_id", "geometry"]], f_alt, f_mdt) for u, sub in ed.groupby("unidade")]
    with Pool(NP) as pool:
        linhas = [l for _, out in pool.imap_unordered(stats_unidade, tarefas, chunksize=8) for l in out]
    st = pd.DataFrame(linhas)
    ed = ed.merge(st, on="proc_id")                         # join por proc_id, como no tutorial
    print("estatísticas: %d edifícios em %.0f s com %d processos" % (len(ed), time.time() - t, NP))

    # exportação por unidade (json/<x>_<y>.city.json) e da cidade inteira
    for u, sub in ed.groupby("unidade"):
        x, y = map(int, u.split("_"))
        json.dump(cityjson(sub, res["epsg"], (x, y)), open(os.path.join(OUT, "json", f"{u}.city.json"), "w"),
                  separators=(",", ":"))
    x0, y0 = res["origem"]
    cj = cityjson(ed, res["epsg"], (x0, y0))
    json.dump(cj, open(os.path.join(OUT, "rondonopolis_lod1.city.json"), "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)
    ed.to_file(os.path.join(OUT, "edificios_lod1.gpkg"))     # 2,5D para QGIS (renderizador 2.5D / Qgis2threejs)
    ed.drop(columns="geometry").to_csv(os.path.join(OUT, "estatisticas_edificios.csv"), index=False)

    # resumo
    r = {"n_edificios": int(len(ed)), "n_unidades": int(ed.unidade.nunique()),
         "fonteAltura": {str(k): int(v) for k, v in ed.fonteAltura.value_counts().items()},
         "altura_m": {k: round(float(ed.measuredHeight.quantile(q)), 1) for k, q in
                      (("p10", .1), ("p50", .5), ("p90", .9), ("p99", .99), ("max", 1))},
         "pavimentos_est (10 = 10+)": {int(k): int(v) for k, v in ed.pavimentosEst.clip(upper=10).value_counts().sort_index().items()},
         "ajustadas_para_min": int(ed.alturaAjustadaMin.sum()),
         "vertices_cityjson": len(cj["vertices"])}
    json.dump(r, open(os.path.join(OUT, "resumo_modelo.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps(r, ensure_ascii=False, indent=1))

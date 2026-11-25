"""Etapa 01 (tutorial 01_download-prepare-data): baixar os dados brutos.

No tutorial (Zurique): LiDAR classificado (.laz), MDT oficial (.tif), footprints oficiais (.shp).
Em Rondonópolis não há LiDAR aberto. Substitutos:
  - footprints + altura estimada: Microsoft Global ML Building Footprints (ODbL), tiles quadkey z9
  - MDT: TOPODATA/INPE folha 16S555 (30 m, SRTM refinado)
  - limite municipal: API de malhas do IBGE (5107602)
Uso: python 01_baixar_dados.py <pasta_saida>
"""
import sys, os, math, urllib.request, zipfile, json
import truststore; truststore.inject_into_ssl()

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "cidade-3d/1.0"}

def baixar(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print("ja existe", dest); return
    print("baixando", url)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, open(dest + ".part", "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    os.replace(dest + ".part", dest)

def quadkey(lat, lon, z):
    x = (lon + 180) / 360; s = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    X, Y, q = int(x * 2**z), int(y * 2**z), ""
    for i in range(z, 0, -1):
        m = 1 << (i - 1); q += str((1 if X & m else 0) + (2 if Y & m else 0))
    return q

# 1) limite municipal (IBGE)
baixar("https://servicodados.ibge.gov.br/api/v3/malhas/municipios/5107602?formato=application/vnd.geo+json&qualidade=maxima",
       os.path.join(OUT, "municipio_5107602.geojson"))

# 2) MDT TOPODATA
z = os.path.join(OUT, "16S555ZN.zip")
baixar("http://www.dsr.inpe.br/topodata/data/geotiff/16S555ZN.zip", z)
with zipfile.ZipFile(z) as zz: zz.extractall(OUT)

# 3) prédios Microsoft: quadkeys que cobrem a mancha urbana (caixa folgada)
caixa = (-54.80, -16.60, -54.50, -16.35)  # w s e n
qks = {quadkey(la, lo, 9) for la in (caixa[1], caixa[3]) for lo in (caixa[0], caixa[2])}
links = os.path.join(OUT, "ms_dataset-links.csv")
baixar("https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv", links)
for linha in open(links, encoding="utf-8"):
    p = linha.strip().split(",")
    if p[0] == "Brazil" and p[1] in qks:
        baixar(p[2], os.path.join(OUT, f"ms_buildings_{p[1]}.csv.gz"))
print("quadkeys:", sorted(qks))

# 4) OSM (Overpass): prédios com altura/pavimentos, para transferir a altura aos footprints
q = f"""[out:json][timeout:180];
(way["building"]({caixa[1]},{caixa[0]},{caixa[3]},{caixa[2]});
 relation["building"]({caixa[1]},{caixa[0]},{caixa[3]},{caixa[2]}););
out tags geom;"""
dest = os.path.join(OUT, "osm_buildings.json")
if not os.path.exists(dest):
    import urllib.parse
    print("consultando Overpass")
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": q}).encode(), headers=UA)
    with urllib.request.urlopen(req, timeout=400) as r, open(dest, "wb") as f: f.write(r.read())

# 5) OSM: ruas (highway) e nomes de bairros/lugares, para localizar o modelo
for nome, corpo in [("osm_ruas.json", 'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"]'),
                    ("osm_lugares.json", 'node["place"~"^(suburb|neighbourhood|quarter|city|town|village|hamlet)$"]')]:
    dest = os.path.join(OUT, nome)
    if os.path.exists(dest): continue
    import urllib.parse
    q = f'[out:json][timeout:180];{corpo}({caixa[1]},{caixa[0]},{caixa[3]},{caixa[2]});out tags geom;'
    print("consultando Overpass:", nome)
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": q}).encode(), headers=UA)
    with urllib.request.urlopen(req, timeout=400) as r, open(dest, "wb") as f: f.write(r.read())

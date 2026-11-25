"""Monta o repositório para o GitHub Pages: site (06_visual), downloads compactados, scripts e prévia.
Uso: python empacotar_github.py <pasta_cidade> <pasta_repo>
"""
import sys, os, shutil, zipfile, json, glob
sys.stdout.reconfigure(encoding="utf-8")
PASTA, REPO = sys.argv[1], sys.argv[2]
AQUI = os.path.dirname(os.path.abspath(__file__))
os.makedirs(REPO, exist_ok=True)
for sub in ("assets", "lib", "data", "downloads", "scripts"):
    d = os.path.join(REPO, sub)
    if os.path.isdir(d) and sub != "assets": shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
VIS = os.path.join(PASTA, "06_visual")
shutil.copy(os.path.join(VIS, "index.html"), REPO)
for sub in ("assets", "lib", "data"):
    for f in glob.glob(os.path.join(VIS, sub, "*")): shutil.copy(f, os.path.join(REPO, sub))
open(os.path.join(REPO, ".nojekyll"), "w").close()

MOD = os.path.join(PASTA, "04_modelo")
def zipar(src, nome):
    dst = os.path.join(REPO, "downloads", nome)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z: z.write(src, os.path.basename(src))
    print(nome, round(os.path.getsize(dst) / 1e6, 1), "MB")
zipar(os.path.join(MOD, "rondonopolis_lod1.city.json"), "rondonopolis_lod1.city.json.zip")
zipar(os.path.join(MOD, "edificios_lod1.gpkg"), "edificios_lod1.gpkg.zip")
zipar(os.path.join(MOD, "estatisticas_edificios.csv"), "estatisticas_edificios.csv.zip")
for f in ("resumo_modelo.json", "validacao_osm.json"): shutil.copy(os.path.join(MOD, f), os.path.join(REPO, "downloads"))
for f in glob.glob(os.path.join(AQUI, "*.py")):
    if not os.path.basename(f).startswith("_"): shutil.copy(f, os.path.join(REPO, "scripts"))
grandes = [(f, os.path.getsize(f)) for f in glob.glob(os.path.join(REPO, "**", "*"), recursive=True)
           if os.path.isfile(f) and os.path.getsize(f) > 50e6]
assert not grandes, f"arquivo acima de 50 MB (GitHub avisa; limite 100 MB): {grandes}"
print("pacote em", REPO)

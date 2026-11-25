"""Leitura parcial (HTTP Range) de GeoTIFF remoto pelo rasterio, validando HTTPS com o repositório
de certificados do Windows (truststore). Usado porque o GDAL desta máquina não confia na CA da rede."""
import io, truststore; truststore.inject_into_ssl()
import requests
S = requests.Session()
class HTTPRangeFile(io.RawIOBase):
    def __init__(self, url, mode="rb"):
        self.url = url; self.pos = 0; self.cache = {}; self.BS = 1 << 20
        h = S.head(url, timeout=60, allow_redirects=True)
        if h.status_code == 404: raise FileNotFoundError(url)
        self.size = int(h.headers["content-length"])
        self.nbytes = 0
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos
    def _block(self, i):
        if i not in self.cache:
            a = i * self.BS; b = min(self.size, a + self.BS) - 1
            for t in range(4):
                try:
                    r = S.get(self.url, headers={"Range": f"bytes={a}-{b}"}, timeout=120); r.raise_for_status(); break
                except Exception:
                    if t == 3: raise
            self.cache[i] = r.content; self.nbytes += len(r.content)
            if len(self.cache) > 400: self.cache.pop(next(iter(self.cache)))
        return self.cache[i]
    def read(self, n=-1):
        if n is None or n < 0: n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos)); out = bytearray()
        while n > 0:
            i, o = divmod(self.pos, self.BS); blk = self._block(i)[o:o + n]
            out += blk; self.pos += len(blk); n -= len(blk)
        return bytes(out)
    def readinto(self, b):
        d = self.read(len(b)); b[:len(d)] = d; return len(d)
def opener(url, mode="rb"):
    if not str(url).startswith("http"):
        raise FileNotFoundError(url)
    return HTTPRangeFile(url)

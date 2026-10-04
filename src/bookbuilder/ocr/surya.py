"""Releitura do texto com o Surya OCR 2 (modelo de visão e linguagem),
executado localmente pelo llama.cpp na placa de vídeo (Vulkan).

* Só é usado com placa de vídeo dedicada de 4 GB ou mais; sem ela, o
  BookBuilder segue só com o Tesseract.
* O layout continua sendo do BookBuilder: o Surya relê o recorte de cada
  bloco de texto (título, parágrafo, nota, legenda). Assim ele não omite
  quadros nem figuras.
* O servidor do modelo roda num processo próprio, iniciado e encerrado pelo
  BookBuilder, ouvindo só em 127.0.0.1. Nada sai do computador.

O prompt e o redimensionamento da imagem seguem exatamente os do pacote
surya-ocr (Apache-2.0), que são o contrato de treino do modelo. O modelo
tem licença AI Pubs OpenRAIL-M modificada (uso pessoal livre).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .. import paths

log = logging.getLogger(__name__)

BLOCK_PROMPT = "OCR this block image to HTML."  # texto exato do treino do modelo
MODEL_ALIAS = "datalab-to/surya-ocr-2"
MODEL_FILES = {
    "surya-2.gguf": (
        "https://huggingface.co/datalab-to/surya-ocr-2-gguf/resolve/main/surya-2.gguf",
        "1f18abe17b1ed8b4",  # prefixo do SHA-256 conferido
        1266400864,
    ),
    "surya-2-mmproj.gguf": (
        "https://huggingface.co/datalab-to/surya-ocr-2-gguf/resolve/main/surya-2-mmproj.gguf",
        "98c0563673b1657f",
        204986688,
    ),
}
MIN_VRAM_MB = 4096
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Gráficos integrados (memória compartilhada): não contam como placa dedicada.
_INTEGRATED = re.compile(
    r"(UHD|Iris|HD Graphics|Radeon\(TM\) Graphics|Radeon Graphics|Vega \d+ Graphics|Radeon \d{3,4}M\b|"
    r"Arc\(TM\) Graphics|Microsoft Basic|llvmpipe|SwiftShader|Adreno)",
    re.I,
)
_DEVICE_RE = re.compile(r"^\s*(\w+?)(\d+):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", re.M)


@dataclass
class Gpu:
    backend: str
    index: int
    name: str
    total_mb: int
    free_mb: int

    @property
    def dedicated(self) -> bool:
        return self.total_mb >= MIN_VRAM_MB and not _INTEGRATED.search(self.name)


def parse_devices(text: str) -> list[Gpu]:
    return [Gpu(m.group(1), int(m.group(2)), m.group(3), int(m.group(4)), int(m.group(5)))
            for m in _DEVICE_RE.finditer(text)]


_gpu_cache: list[Gpu] | None = None


def list_gpus() -> list[Gpu]:
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache
    exe = paths.llama_server()
    if not exe.is_file():
        _gpu_cache = []
        return _gpu_cache
    try:
        r = subprocess.run([str(exe), "--list-devices"], capture_output=True, timeout=60,
                           creationflags=_CREATE_NO_WINDOW, check=False)
        _gpu_cache = parse_devices(r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace"))
    except (OSError, subprocess.TimeoutExpired):
        _gpu_cache = []
    return _gpu_cache


def dedicated_gpu() -> Gpu | None:
    gs = [g for g in list_gpus() if g.dedicated]
    return max(gs, key=lambda g: g.total_mb) if gs else None


def model_paths() -> tuple[Path, Path]:
    d = paths.surya_model_dir()
    return d / "surya-2.gguf", d / "surya-2-mmproj.gguf"


def model_present() -> bool:
    m, p = model_paths()
    return all(f.is_file() and f.stat().st_size == MODEL_FILES[f.name][2] for f in (m, p))


@dataclass
class Status:
    available: bool
    reason: str
    gpu: Gpu | None = None
    needs_download: bool = False


def status() -> Status:
    """Pode usar o Surya? E, se não, por quê (em português, para a interface)."""
    if not paths.llama_server().is_file():
        return Status(False, "componente llama.cpp ausente")
    gpu = dedicated_gpu()
    if gpu is None:
        return Status(False, "nenhuma placa de vídeo dedicada com 4 GB ou mais")
    if not model_present():
        return Status(False, "modelo do Surya ainda não baixado (cerca de 1,4 GB)", gpu, needs_download=True)
    return Status(True, f"placa de vídeo: {gpu.name}", gpu)


def download_model(progress: Callable[[int, int], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> None:
    """Baixa o modelo (1,4 GB) para a pasta de dados do usuário, conferindo o
    SHA-256 de cada arquivo."""
    target = paths.user_data_dir() / "modelos" / "surya"
    target.mkdir(parents=True, exist_ok=True)
    total = sum(v[2] for v in MODEL_FILES.values())
    done = 0
    for name, (url, sha_prefix, size) in MODEL_FILES.items():
        dst = target / name
        if dst.is_file() and dst.stat().st_size == size:
            done += size
            continue
        tmp = dst.with_suffix(".parcial")
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                if cancelled and cancelled():
                    raise RuntimeError("download cancelado")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if not h.hexdigest().startswith(sha_prefix):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"o arquivo {name} chegou corrompido ou diferente do esperado")
        os.replace(tmp, dst)


# ---------------------------------------------------------------------------
# Servidor


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """llama-server com o modelo do Surya, na placa de vídeo."""

    def __init__(self, parallel: int = 8, ctx_per_slot: int = 12288):
        self.parallel = parallel
        self.ctx = max(16384, parallel * ctx_per_slot)
        self.port = 0
        self.proc: subprocess.Popen | None = None
        self._log = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 600.0) -> None:
        model, mmproj = model_paths()
        self.port = _free_port()
        log_path = paths.user_data_dir() / "llama_server.log"
        self._log = open(log_path, "ab")
        cmd = [str(paths.llama_server()), "-m", str(model), "--mmproj", str(mmproj), "-ngl", "99",
               "--host", "127.0.0.1", "--port", str(self.port), "--parallel", str(self.parallel),
               "--ctx-size", str(self.ctx), "--alias", MODEL_ALIAS, "--jinja"]
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT,
                                     creationflags=_CREATE_NO_WINDOW)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"o servidor do Surya terminou ao iniciar (veja {log_path})")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=5) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1.0)
        self.stop()
        raise RuntimeError("o servidor do Surya não ficou pronto a tempo")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self._log:
            self._log.close()
            self._log = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


# ---------------------------------------------------------------------------
# OCR de um bloco


def scale_to_fit(img: Image.Image, max_size=(3072, 2048), min_size=(1792, 28), grid: int = 28) -> Image.Image:
    """Mesmo redimensionamento do surya-ocr (múltiplos de 28 px)."""
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    ar = w / h
    cur, mx, mn = w * h, max_size[0] * max_size[1], min_size[0] * min_size[1]
    scale = (mx / cur) ** 0.5 if cur > mx else ((mn / cur) ** 0.5 if cur < mn else 1.0)
    wb, hb = max(1, round(w * scale / grid)), max(1, round(h * scale / grid))
    while wb * hb * grid * grid > mx and not (wb == 1 and hb == 1):
        if wb == 1:
            hb -= 1
        elif hb == 1:
            wb -= 1
        elif abs((wb - 1) / hb - ar) < abs(wb / (hb - 1) - ar):
            wb -= 1
        else:
            hb -= 1
    nw, nh = wb * grid, hb * grid
    return img if (nw, nh) == (w, h) else img.resize((nw, nh), Image.Resampling.LANCZOS)


def _looping(text: str) -> bool:
    """Saída presa em repetição (falha conhecida de modelos generativos)."""
    tail = text[-400:]
    for n in range(1, 60):
        seq = tail[-n:]
        if seq and tail.endswith(seq * max(4, 200 // n)):
            return True
    return False


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:html)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def ocr_block(server_url: str, crop: np.ndarray | Image.Image, expected_chars: int = 200,
              timeout: float = 300.0) -> str:
    """HTML do texto do recorte. ``expected_chars`` dimensiona o limite de
    tokens (como o surya-ocr faz com a contagem do layout)."""
    img = crop if isinstance(crop, Image.Image) else Image.fromarray(crop)
    img = scale_to_fit(img.convert("RGB"))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    max_tokens = min(max(int(expected_chars * 1.3) + 100, 64), 8192)
    out = ""
    for temp in (0.0, 0.2, 0.4):
        body = {
            "model": MODEL_ALIAS,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": BLOCK_PROMPT}]}],
            "max_tokens": max_tokens,
            "temperature": temp,
        }
        if temp:
            body["top_p"] = 0.95
        req = urllib.request.Request(server_url + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        choice = data["choices"][0]
        out = _strip_fences(choice["message"].get("content") or "")
        if choice.get("finish_reason") != "length" and not _looping(out):
            return out
    return ""  # sem leitura confiável: fica o Tesseract


# ---------------------------------------------------------------------------
# Etapa de releitura (fila de páginas, em paralelo com o Tesseract)

TEXT_ROLES = {"corpo", "titulo", "lista", "citacao", "legenda", "nota_rodape"}


class Stage:
    """Fila de páginas para reler com o Surya. As páginas entram assim que o
    Tesseract termina; vários blocos são lidos ao mesmo tempo (um por vaga
    do servidor). ``on_page(page)`` é chamado quando a página termina."""

    def __init__(self, server: Server, load_gray: Callable, on_page: Callable, on_error: Callable | None = None):
        from concurrent.futures import ThreadPoolExecutor
        import queue

        self.server = server
        self.load_gray = load_gray
        self.on_page = on_page
        self.on_error = on_error or (lambda page, err: None)
        self.q: "queue.Queue" = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=server.parallel)
        self._cancel = threading.Event()
        self._threads = [threading.Thread(target=self._loop, daemon=True) for _ in range(2)]
        for t in self._threads:
            t.start()
        self.pending = 0
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()

    def submit(self, page) -> None:
        with self._lock:
            self.pending += 1
            self._idle.clear()
        self.q.put(page)

    def cancel(self) -> None:
        self._cancel.set()

    def _done_one(self) -> None:
        with self._lock:
            self.pending -= 1
            if self.pending <= 0:
                self._idle.set()

    def _loop(self) -> None:
        import queue

        while True:
            try:
                page = self.q.get(timeout=0.5)
            except queue.Empty:
                if self._cancel.is_set() and self.pending <= 0:
                    return
                continue
            if page is None:
                return
            try:
                if not self._cancel.is_set():
                    self._process(page)
            except Exception as e:  # noqa: BLE001
                log.exception("Surya falhou na página %s", page.source.path)
                self.on_error(page, str(e))
            finally:
                self._done_one()

    def _process(self, page) -> None:
        blocks = [b for b in page.blocks if b.role.value in TEXT_ROLES and b.lines
                  and not any(w.text == "[trecho ilegível]" for ln in b.lines for w in ln.words)]
        if not blocks:
            page.surya = True
            self.on_page(page)
            return
        gray = self.load_gray(page)
        h, w = gray.shape[:2]
        futures = []
        for b in blocks:
            # Recorte pelas linhas do próprio bloco (a região do layout pode
            # avançar sobre linhas de blocos vizinhos).
            x0 = min(ln.bbox[0] for ln in b.lines)
            y0 = min(ln.bbox[1] for ln in b.lines)
            x1 = max(ln.bbox[2] for ln in b.lines)
            y1 = max(ln.bbox[3] for ln in b.lines)
            pad = 6
            crop = gray[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)]
            chars = sum(len(ln.text) + 1 for ln in b.lines)
            futures.append((b, self.pool.submit(ocr_block, self.server.url, crop, chars)))
        for b, f in futures:
            if self._cancel.is_set():
                f.cancel()
                continue
            try:
                b.surya_html = f.result()
            except Exception as e:  # noqa: BLE001 - bloco sem releitura: fica o Tesseract
                log.warning("Surya: bloco %s sem leitura: %s", b.id, e)
                b.surya_html = None
        if not self._cancel.is_set():
            page.surya = True
            self.on_page(page)

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def close(self) -> None:
        self._cancel.set()
        for _ in self._threads:
            self.q.put(None)
        for t in self._threads:
            t.join(timeout=30)
        self.pool.shutdown(wait=False, cancel_futures=True)


def needs_surya(page) -> bool:
    from ..model import PageStatus

    return (not page.excluded and page.status == PageStatus.DONE and not page.surya)

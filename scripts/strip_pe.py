"""Remove as informações de depuração (DWARF) de DLLs/EXEs do Windows gerados
pelo MinGW, como faz o ``strip``: as seções de depuração (nomes longos,
"/4", "/19"…) ficam no fim do arquivo e não são carregadas para executar.

Uso: python scripts/strip_pe.py origem destino
"""

from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path


def strip_debug(src: str | Path, dst: str | Path) -> bool:
    """Grava em ``dst`` a cópia sem depuração. Devolve False (e copia o
    arquivo como está) se ele não tiver seções de depuração no fim."""
    data = bytearray(Path(src).read_bytes())
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        shutil.copyfile(src, dst)
        return False
    fh = pe + 4
    n_sections = struct.unpack_from("<H", data, fh + 2)[0]
    opt_size = struct.unpack_from("<H", data, fh + 16)[0]
    opt = fh + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        shutil.copyfile(src, dst)
        return False
    sec_align = struct.unpack_from("<I", data, opt + 32)[0]
    sections = []
    table = opt + opt_size
    for i in range(n_sections):
        off = table + 40 * i
        name = bytes(data[off:off + 8]).rstrip(b"\0").decode("latin-1")
        vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((name, vsize, vaddr, raw_size, raw_ptr))
    # Primeira seção de depuração; todas as seguintes também precisam ser.
    first = next((i for i, s in enumerate(sections) if s[0].startswith("/") or s[0].startswith(".debug")), None)
    if first is None or first == 0 or not all(s[0].startswith(("/", ".debug")) for s in sections[first:]):
        shutil.copyfile(src, dst)
        return False
    keep = sections[:first]
    end_raw = max(s[4] + s[3] for s in keep if s[3])
    last = max(keep, key=lambda s: s[2])
    size_image = (last[2] + max(last[1], last[3]) + sec_align - 1) // sec_align * sec_align
    struct.pack_into("<H", data, fh + 2, first)  # NumberOfSections
    struct.pack_into("<I", data, fh + 8, 0)  # PointerToSymbolTable
    struct.pack_into("<I", data, fh + 12, 0)  # NumberOfSymbols
    struct.pack_into("<I", data, opt + 56, size_image)  # SizeOfImage
    struct.pack_into("<I", data, opt + 64, 0)  # CheckSum (não verificado para DLLs comuns)
    for i in range(first, n_sections):
        off = table + 40 * i
        data[off:off + 40] = b"\0" * 40
    Path(dst).write_bytes(bytes(data[:end_raw]))
    return True


if __name__ == "__main__":
    ok = strip_debug(sys.argv[1], sys.argv[2])
    a, b = Path(sys.argv[1]).stat().st_size, Path(sys.argv[2]).stat().st_size
    print(f"{'reduzido' if ok else 'sem depuração'}: {a / 2**20:.1f} MB -> {b / 2**20:.1f} MB")

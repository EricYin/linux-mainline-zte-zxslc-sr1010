#!/usr/bin/env python3
"""Read np_onu_reg table fields from a factory AArch64 ELF module.

This decodes the 28-byte table ABI recovered from np_onu_reg_read/write.
It does not access hardware or infer the meaning of descriptor fields.
"""

import argparse
import json
import struct
from pathlib import Path


SECTION = struct.Struct("<IIQQQQIIQQ")
SYMBOL = struct.Struct("<IBBHQQ")
FIELD = struct.Struct("<H2xIIIHHII")


def symbol_data(image, name):
    ident, elf_type, machine = struct.unpack_from("<16sHH", image)
    if ident[:6] != b"\x7fELF\x02\x01" or elf_type != 1 or machine != 183:
        raise ValueError("expected a little-endian AArch64 ELF64 relocatable module")
    section_offset = struct.unpack_from("<Q", image, 40)[0]
    entry_size, count = struct.unpack_from("<HH", image, 58)
    sections = [SECTION.unpack_from(image, section_offset + i * entry_size)
                for i in range(count)]
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings = sections[section[6]]
        names = image[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name_at, _, _, index, value, size = SYMBOL.unpack_from(image, offset)
            end = names.find(b"\0", name_at)
            if names[name_at:end] != name.encode():
                continue
            if not 0 < index < len(sections):
                raise ValueError(f"{name} has no file-backed section")
            data = sections[index]
            if data[1] == 8 or value + size > data[5]:  # SHT_NOBITS
                raise ValueError(f"{name} has no complete file-backed contents")
            start = data[4] + value
            return start, image[start:start + size]
    raise ValueError(f"symbol not found: {name}")


def decode_table(image, name, base, ids):
    offset, data = symbol_data(image, name)
    if not data or len(data) % FIELD.size:
        raise ValueError(f"{name} size is not a multiple of {FIELD.size}")
    fields = []
    for position in range(0, len(data), FIELD.size):
        row = FIELD.unpack_from(data, position)
        if not any(row):  # Some tables include a zero-filled terminal entry.
            continue
        ident, access, word, mask, shift, max_index, stride, _ = row
        if ids and ident not in ids:
            continue
        fields.append({
            "id": f"0x{ident:03x}",
            "access": access,
            "word_offset": f"0x{word:x}",
            "address": f"0x{base + word * 4:08x}",
            "mask": f"0x{mask:08x}",
            "shift": shift,
            "max_index": max_index,
            "stride_bytes": stride * 4,
        })
    if ids - {int(field["id"], 16) for field in fields}:
        raise ValueError("one or more requested field IDs are absent")
    return {"symbol": name, "file_offset": f"0x{offset:x}",
            "entry_count": len(data) // FIELD.size, "fields": fields}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("symbol", help="for example, woe_reg_table")
    parser.add_argument("--base", required=True, type=lambda x: int(x, 0),
                        help="physical base for the table's register bank")
    parser.add_argument("--ids", nargs="+", type=lambda x: int(x, 0), default=[],
                        help="field IDs to select (default: all)")
    args = parser.parse_args()
    try:
        result = decode_table(args.module.read_bytes(), args.symbol,
                              args.base, set(args.ids))
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

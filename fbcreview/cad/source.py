"""What an upload is, decided from its bytes, and a zip set unpacked safely.

A permit set can arrive as a PDF plot, a single drawing (`.dwg` or `.dxf`), or a
zip of drawing files — the usual shape when sheet files reference a base plan as
an external reference (xref) and the xref has to travel with them. The file
name is the client's to choose and proves nothing, so every decision here is
made from the leading bytes:

| Bytes                         | Format                                  |
| ----------------------------- | --------------------------------------- |
| `%PDF-`                       | PDF — not this package's concern        |
| `AC1012` … `AC1032`           | DWG, R13 to the 2018 format              |
| `AC1009`, `AC1006` … `MC0.0`  | DWG too old to read — refused by name    |
| `0\\nSECTION` (after any 999)  | ASCII DXF                               |
| `AutoCAD Binary DXF\\r\\n\\x1a`  | binary DXF                              |
| `PK\\x03\\x04`                  | zip — unpacked and its members sniffed  |

A zip is the one input that can attack the service rather than merely fail to
parse: a member named `../../etc/passwd`, ten thousand members, or a few
kilobytes that inflate to gigabytes. `unpack()` refuses all three before a byte
is written, and keeps only members that sniff as a drawing. The member count is
read from the zip's end record (`check_zip_directory`) before `zipfile` is
asked to open it, because opening it parses every member's entry first.
"""
from __future__ import annotations

import os
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

PDF, DWG, DXF, ZIP = "pdf", "dwg", "dxf", "zip"

#: The version string a DWG opens with, and the release AutoCAD sells it as.
#: AutoCAD has written the 2018 format (AC1032) ever since; LibreDWG reads all of
#: these. R12 and older (AC1009 and below) predate the object model the adapter
#: reads layouts from, and are refused with that reason rather than half-read.
DWG_VERSIONS: Dict[str, str] = {
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "AutoCAD 2000",
    "AC1018": "AutoCAD 2004",
    "AC1021": "AutoCAD 2007",
    "AC1024": "AutoCAD 2010",
    "AC1027": "AutoCAD 2013",
    "AC1032": "AutoCAD 2018",
}

#: Releases older than R13, by the version string their files open with — named
#: so a refusal can tell the person which AutoCAD wrote the file, not a code.
OLD_DWG_VERSIONS: Dict[str, str] = {
    "AC1009": "AutoCAD R11/R12",
    "AC1006": "AutoCAD R10",
    "AC1004": "AutoCAD R9",
    "AC1003": "AutoCAD 2.6",
    "AC1002": "AutoCAD 2.5",
    "AC1001": "AutoCAD 2.2",
    "AC2.10": "AutoCAD 2.1",
    "AC1.50": "AutoCAD 2.0",
    "AC1.40": "AutoCAD 1.4",
    "AC1.2": "AutoCAD 1.2",
    "MC0.0": "AutoCAD 1.0",
}

#: A DWG opens with its six-byte version string: `AC` and four digits from R9 on,
#: a dotted form before that.
_DWG_MAGIC = re.compile(rb"^(?:AC\d{4}|AC\d\.\d{1,2}|MC0\.0)")

_BINARY_DXF = b"AutoCAD Binary DXF\r\n\x1a\x00"
#: Each CR and LF can be matched exactly one way: a comment line cannot hold a
#: CR and a whitespace run cannot reach into a line ending. The first pattern let
#: `\s*` or `\r?` take each CR, and forty CRLF comment lines and a non-match
#: backtracked for minutes on the upload request's event loop.
_ASCII_DXF = re.compile(
    rb"^[ \t\r\n]*(?:999[ \t]*\r?\n[^\r\n]*\r?\n[ \t\r\n]*)*0[ \t]*\r?\nSECTION\b")


def _binary_after(head: bytes, start: int) -> bool:
    """Do binary bytes follow the version string?

    Every DWG header continues in binary right after its version string (five
    zero bytes from R13 on). A text file that happens to begin `AC1009,` does
    not, and must not be refused as an old drawing when it is no drawing at all.
    """
    tail = head[start:start + 32]
    return any(b < 0x09 or 0x0E <= b < 0x20 or b >= 0x7F for b in tail)

#: Zip limits. A real permit set is a few dozen sheet files; a 2 GB DXF is not a
#: permit set. These bound the work and the disk a single upload can claim — and
#: on Cloud Run the disk is memory. 1 GB unpacked is already more DXF than one
#: job may read (`fbcreview.cad.DEFAULT_MAX_DXF_MB`; a DWG only grows when it
#: is converted), so nothing reviewable is lost by refusing it at the zip.
MAX_MEMBERS = 400
MAX_DRAWINGS = 120
MAX_UNPACKED_BYTES = 1024 ** 3
#: Deflate does well on DXF text (about 10:1 measured); anything far beyond that
#: is a bomb rather than a drawing.
MAX_RATIO = 200
#: The most central directory one member of a drawing set can need: the 46-byte
#: fixed entry, a name of up to 1 KiB (Windows stops a path at 260 characters)
#: and 2 KiB of extra field and comment (the timestamps, Unix ids, ZIP64 sizes
#: and NTFS times real archivers write come to about 100 bytes). `zipfile` walks
#: a directory by its size in bytes, not by the count beside it, so this is what
#: bounds the work of opening a zip: `MAX_MEMBERS` of these is 1.2 MB. Measured
#: on a reviewer's 122 MB zip whose directory lists 2.3 million members:
#: zipfile took 10 s and 1.1 GB to parse it before the count was looked at;
#: read from the end record, it is refused in under a millisecond.
MAX_DIRECTORY_ENTRY_BYTES = 46 + 1024 + 2048

_UNOPENABLE_ZIP = "That zip file could not be opened. It may be damaged or incompletely uploaded."


class SourceError(Exception):
    """An upload that cannot be reviewed as a drawing, with the reason in prose."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── a zip's end records ──────────────────────────────────────────────────────
_EOCD = b"PK\x05\x06"                 # end of central directory, 22 bytes + comment
_EOCD64_LOCATOR = b"PK\x06\x07"       # 20 bytes, just before the end record
_EOCD64 = b"PK\x06\x06"               # 56 bytes, where the locator points
_EOCD_SIZE, _LOCATOR_SIZE, _EOCD64_SIZE = 22, 20, 56
#: The classic record's fields when the real values are in the ZIP64 record.
_PLACEHOLDERS = (0xFFFF, 0xFFFFFFFF)


@dataclass(frozen=True)
class ZipDirectory:
    """What a zip's end records declare about its central directory."""

    entries: int
    #: Bytes the central directory occupies: what `zipfile` reads and parses.
    size: int


def zip_directory(path: str) -> Optional[ZipDirectory]:
    """The member count and directory size a zip declares, from its end records only.

    Reads the last 64 KiB and at most two 56-byte records — never the directory
    itself. Found where `zipfile` finds them: the end record as the last 22
    bytes, else the last one in the final 64 KiB + 22 (behind an archive
    comment); and, when a ZIP64 locator sits in front of it, the ZIP64 record at
    the offset the locator gives and directly before the locator. Python
    releases differ over which of those two they read, and an old one falls back
    to the classic record when there is none directly before the locator, so
    every value one of them could act on is a candidate and the largest is
    returned. None when there is no end record: not a zip `zipfile` can open.
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        file_size = fh.tell()
        if file_size < _EOCD_SIZE:
            return None
        start = max(file_size - (1 << 16) - _EOCD_SIZE, 0)
        fh.seek(start)
        tail = fh.read()
        if tail[-_EOCD_SIZE:-_EOCD_SIZE + 4] == _EOCD and tail[-2:] == b"\x00\x00":
            at = len(tail) - _EOCD_SIZE
        else:
            at = tail.rfind(_EOCD)
        if at < 0 or len(tail) - at < _EOCD_SIZE:
            return None
        _, _, _, here, total, size, _, _ = struct.unpack_from("<4sHHHHIIH", tail, at)

        counts: List[int] = []
        sizes: List[int] = []
        before_locator = False
        locator_at = start + at - _LOCATOR_SIZE
        if locator_at >= 0:
            fh.seek(locator_at)
            locator = fh.read(_LOCATOR_SIZE)
            if len(locator) == _LOCATOR_SIZE and locator[:4] == _EOCD64_LOCATOR:
                _, _, record_at, _ = struct.unpack("<4sIQI", locator)
                adjacent = locator_at - _EOCD64_SIZE
                for where in {record_at, adjacent}:
                    if not 0 <= where <= adjacent:
                        continue
                    fh.seek(where)
                    record = fh.read(_EOCD64_SIZE)
                    if len(record) == _EOCD64_SIZE and record[:4] == _EOCD64:
                        fields = struct.unpack("<4sQHHIIQQQQ", record)
                        counts.append(max(fields[6], fields[7]))
                        sizes.append(fields[8])
                        before_locator = before_locator or where == adjacent
        if before_locator:
            # Every reader takes a ZIP64 record, so 0xFFFF and 0xFFFFFFFF in the
            # classic record are placeholders for it. Anything else there is a
            # real value an archiver wrote as well, and is bounded too.
            counts += [c for c in (here, total) if c != _PLACEHOLDERS[0]]
            sizes += [size] if size != _PLACEHOLDERS[1] else []
        else:
            counts += [here, total]
            sizes.append(size)
    return ZipDirectory(entries=max(counts), size=max(sizes))


def check_zip_directory(path: str) -> ZipDirectory:
    """Refuse a zip whose end record declares more than one can safely open.

    Run before `zipfile.ZipFile(path)`, which parses the whole central directory
    on construction — a cost set by the upload, not by `MAX_MEMBERS`. Raises
    SourceError: `zip_too_large` for more members than `MAX_MEMBERS`, or a
    directory larger than that many members could need; `corrupt_zip` when there
    is no end record to read.
    """
    try:
        found = zip_directory(path)
    except OSError:
        found = None
    if found is None:
        raise SourceError("corrupt_zip", _UNOPENABLE_ZIP)
    if found.entries > MAX_MEMBERS:
        raise SourceError("zip_too_large",
                          f"That zip holds {found.entries} files. The limit is {MAX_MEMBERS}; "
                          "upload the permit set's drawings only.")
    if found.size > MAX_MEMBERS * MAX_DIRECTORY_ENTRY_BYTES:
        raise SourceError("zip_too_large",
                          f"That zip's directory runs to {found.size / 1024 ** 2:,.1f} MB, more "
                          f"than {MAX_MEMBERS} files could need, and {MAX_MEMBERS} is the limit. "
                          "It was not opened; upload the permit set's drawings only.")
    return found


def sniff_bytes(head: bytes) -> Optional[str]:
    """The format of a file from its first bytes, or None when it is none of ours."""
    if head.startswith(b"%PDF-"):
        return PDF
    magic = _DWG_MAGIC.match(head)
    if magic and _binary_after(head, magic.end()):
        # Older and newer releases than the adapter reads are recognised too,
        # so the refusal can say what the file is instead of "not a drawing".
        return DWG
    if head.startswith(_BINARY_DXF):
        return DXF
    if _ASCII_DXF.match(head[:4096]):
        return DXF
    if head.startswith(b"PK\x03\x04"):
        return ZIP
    return None


def sniff(path: str) -> Optional[str]:
    with open(path, "rb") as fh:
        return sniff_bytes(fh.read(4096))


def dwg_version(path: str) -> str:
    """`AC1032`, or "" when the file is not a DWG."""
    with open(path, "rb") as fh:
        m = _DWG_MAGIC.match(fh.read(6))
    return m.group().decode("ascii") if m else ""


def check_dwg_version(path: str) -> str:
    """The DWG's release name; raises SourceError for a release LibreDWG cannot
    give us layouts from — naming the release, and saying which way it is out
    of range, because "save it as 2000 or later" is the wrong advice for a
    file from an AutoCAD newer than any this service knows."""
    ver = dwg_version(path)
    if ver in DWG_VERSIONS:
        return DWG_VERSIONS[ver]
    newest = max(DWG_VERSIONS)
    if re.fullmatch(r"AC\d{4}", ver) and ver > newest:
        raise SourceError(
            "unsupported_dwg_version",
            f"That drawing is saved in a DWG format newer than this service reads ({ver}; "
            f"the newest it reads is {newest}, {DWG_VERSIONS[newest]}). Save it as "
            f"{DWG_VERSIONS[newest]} DWG, or export it as DXF, and upload it again.")
    release = OLD_DWG_VERSIONS.get(ver)
    named = f"{release} ({ver})" if release else (ver or "an unrecognised version")
    raise SourceError(
        "unsupported_dwg_version",
        f"That drawing is saved in an old DWG format: {named}. Open it in AutoCAD and "
        "save it as AutoCAD 2000 or later, then upload it again.")


@dataclass
class Unpacked:
    """The drawings found in a zip, by their name inside it."""
    root: Path
    #: Member name as stored (forward slashes) -> extracted path.
    drawings: Dict[str, Path] = field(default_factory=dict)
    #: Member names skipped, and why — reported, never silent.
    skipped: Dict[str, str] = field(default_factory=dict)

    def by_basename(self) -> Dict[str, Path]:
        """Drawings keyed by lower-case file name, for resolving xrefs.

        An xref is recorded with the path it had on the drafter's machine —
        `C:\\Projects\\1234\\XREF\\X-BASE.dwg` — which never exists here. The file
        name is what survives the trip, so that is what references resolve by.
        """
        out: Dict[str, Path] = {}
        for name, p in sorted(self.drawings.items()):
            out.setdefault(os.path.basename(name).lower(), p)
        return out


def _safe_member(name: str) -> Optional[str]:
    """The member's path made relative and free of `..`, or None to refuse it."""
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        return None
    return "/".join(parts)


def unpack(zip_path: str, dest: str) -> Unpacked:
    """Extract the drawings in a zip into `dest`, refusing anything hostile.

    Raises SourceError when the archive cannot be read, is a bomb, or holds no
    drawing at all.
    """
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    out = Unpacked(root=root)
    # From the end record, before zipfile parses every entry of the directory.
    check_zip_directory(zip_path)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise SourceError("corrupt_zip", _UNOPENABLE_ZIP)
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise SourceError("zip_too_large",
                              f"That zip holds {len(infos)} files. The limit is {MAX_MEMBERS}; "
                              "upload the permit set's drawings only.")
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            total += info.file_size
            if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
                raise SourceError("zip_bomb", "That zip file expands far beyond what a "
                                              "drawing set could. It was not unpacked.")
        if total > MAX_UNPACKED_BYTES:
            raise SourceError("zip_too_large",
                              f"That zip unpacks to {total / 1024 ** 3:.1f} GB. The limit is "
                              f"{MAX_UNPACKED_BYTES / 1024 ** 3:.0f} GB; upload the permit "
                              "set's drawings only.")

        for info in infos:
            if info.is_dir():
                continue
            name = _safe_member(info.filename)
            if name is None:
                out.skipped[info.filename] = "unsafe path"
                continue
            base = os.path.basename(name)
            if name.startswith("__MACOSX/") or base.startswith("._"):
                continue
            if info.flag_bits & 0x1:
                out.skipped[name] = "encrypted"
                continue
            if not base.lower().endswith((".dwg", ".dxf")):
                out.skipped[name] = "not a drawing"
                continue
            if len(out.drawings) >= MAX_DRAWINGS:
                out.skipped[name] = f"beyond the first {MAX_DRAWINGS} drawings"
                continue
            target = root / f"{len(out.drawings):03d}_{_flat(base)}"
            try:
                _extract(zf, info, target)
            except SourceError:
                target.unlink(missing_ok=True)
                raise
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError,
                    RuntimeError) as exc:
                # A member whose bytes do not match its own header — a CRC that
                # fails, a size that lies, a compression method zipfile cannot
                # undo. Python's zipfile stops at the declared size and then
                # fails the CRC, so a lying header surfaces here, not as a full
                # disk. Refused whole: a set with a damaged member is not the set
                # the person sent.
                target.unlink(missing_ok=True)
                raise SourceError("corrupt_zip",
                                  "A file inside that zip is damaged or does not match the "
                                  f"zip's own record of it ({exc.__class__.__name__}). It was "
                                  "not unpacked; zip the drawings again and upload that.")
            kind = sniff(str(target))
            if kind not in (DWG, DXF):
                target.unlink(missing_ok=True)
                out.skipped[name] = "named like a drawing but is not one"
                continue
            out.drawings[name] = target
    if not out.drawings:
        raise SourceError("no_drawings", "That zip holds no DWG or DXF drawings.")
    return out


def _extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    """Copy one member out, never writing more than it declares (plus slack).

    zipfile itself stops reading at the declared size; the count here is the
    second line, for a reader that does not.
    """
    with zf.open(info) as src, open(target, "wb") as dst:
        written = 0
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > info.file_size + (1 << 20):
                raise SourceError("zip_bomb", "That zip file expands beyond the size it "
                                              "declares. It was not unpacked.")
            dst.write(chunk)


def _flat(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return keep[:120] or "drawing"

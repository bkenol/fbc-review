"""What an upload is, decided from its bytes; and a zip of drawings unpacked safely.

`fbcreview/cad/source.py` is the adapter's front door. The file name is the
client's to choose, so the format is read from the leading bytes, and a zip —
the one upload that can attack the service rather than merely fail to parse —
is refused before a byte is written when it is hostile: a member that climbs
out of the directory, an absolute path, an encrypted member, a few kilobytes
that inflate to gigabytes, a header that lies about a member's size, ten
thousand members. What is kept is the drawings, and only the drawings.

No drawing here comes from a client: each byte string is made in the test.
"""
from __future__ import annotations

import io
import os
import struct
import zipfile

import pytest

from fbcreview.cad import source
from fbcreview.cad.source import (DWG, DWG_VERSIONS, DXF, PDF, ZIP, SourceError,
                                  check_dwg_version, sniff, sniff_bytes, unpack)

DXF_TEXT = b"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n0\nEOF\n"


def dwg(version: bytes = b"AC1032") -> bytes:
    """Six bytes of version, then the binary header every DWG continues with."""
    return version + b"\x00\x00\x00\x00\x00\x01\x03" + b"\x00" * 200


def make_zip(members, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in members:
            zf.writestr(zipfile.ZipInfo(name), data, compress_type=compression)
    return buf.getvalue()


def write(tmp_path, data: bytes, name: str = "set.zip") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def files_under(root) -> set:
    out = set()
    for d, _, fs in os.walk(root):
        for f in fs:
            out.add(os.path.relpath(os.path.join(d, f), root))
    return out


# ── sniffing ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("version", sorted(DWG_VERSIONS))
def test_every_dwg_release_the_converter_reads_sniffs_as_dwg(version):
    assert sniff_bytes(dwg(version.encode())) == DWG


def test_each_format_is_told_from_its_bytes():
    assert sniff_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n") == PDF
    assert sniff_bytes(DXF_TEXT) == DXF
    # comment lines before the first section are legal DXF
    assert sniff_bytes(b"999\nmade by a test\n999\nsecond\n  0\r\nSECTION\r\n") == DXF
    assert sniff_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00" + b"\x00" * 64) == DXF
    assert sniff_bytes(make_zip([("a.dxf", DXF_TEXT)])) == ZIP


@pytest.mark.parametrize("head", [
    b"",
    b"hello, this is a transmittal letter\n",
    b"<!DOCTYPE html>\n<html><body>Sign in</body></html>",
    b"<html>",
    b"{\"pages\": 3}",
    # a text file that opens with a DWG version code is still text
    b"AC1009 is the R12 version string, see notes\n",
    b"AC1032,AC1027,AC1024\n",
    b"SECTION\n0\n",                     # DXF words without the group code first
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
])
def test_anything_else_is_none_of_ours(head):
    assert sniff_bytes(head) is None


def test_an_old_dwg_is_recognised_and_refused_with_its_release_named(tmp_path):
    p = write(tmp_path, dwg(b"AC1009"), "old.dwg")
    assert sniff(p) == DWG
    with pytest.raises(SourceError) as exc:
        check_dwg_version(p)
    assert exc.value.code == "unsupported_dwg_version"
    assert "AutoCAD R11/R12" in exc.value.message
    assert "AC1009" in exc.value.message
    assert "AutoCAD 2000" in exc.value.message          # what to do about it


def test_a_dwg_newer_than_the_converter_reads_is_not_called_old(tmp_path):
    p = write(tmp_path, dwg(b"AC1040"), "future.dwg")
    assert sniff(p) == DWG
    with pytest.raises(SourceError) as exc:
        check_dwg_version(p)
    assert exc.value.code == "unsupported_dwg_version"
    assert "newer" in exc.value.message and "old" not in exc.value.message
    assert "AC1040" in exc.value.message


def test_a_readable_dwg_names_its_release(tmp_path):
    assert check_dwg_version(write(tmp_path, dwg(b"AC1032"), "a.dwg")) == "AutoCAD 2018"
    assert check_dwg_version(write(tmp_path, dwg(b"AC1015"), "b.dwg")) == "AutoCAD 2000"
    assert source.dwg_version(write(tmp_path, b"not a drawing", "c.dwg")) == ""


# ── unpacking: what is kept ──────────────────────────────────────────────────
def test_only_drawings_are_kept_and_every_skip_says_why(tmp_path):
    data = make_zip([
        ("SET/A-101.dxf", DXF_TEXT),
        ("SET/A-102.DWG", dwg()),
        ("SET/transmittal.pdf", b"%PDF-1.7\n"),
        ("SET/notes.txt", b"read me"),
        ("SET/broken.dwg", b"this is a text file named like a drawing"),
        ("__MACOSX/SET/._A-101.dxf", b"\x00\x05\x16\x07"),
        ("SET/._A-101.dxf", b"\x00\x05\x16\x07"),
        ("SET/empty/", b""),
    ])
    out = unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert sorted(out.drawings) == ["SET/A-101.dxf", "SET/A-102.DWG"]
    assert out.skipped == {"SET/transmittal.pdf": "not a drawing",
                           "SET/notes.txt": "not a drawing",
                           "SET/broken.dwg": "named like a drawing but is not one"}
    # extracted flat, under names the service chose, and nothing else left behind
    assert files_under(tmp_path / "out") == {os.path.basename(str(p))
                                            for p in out.drawings.values()}
    assert sniff(str(out.drawings["SET/A-102.DWG"])) == DWG


def test_by_basename_resolves_an_xref_by_its_file_name(tmp_path):
    data = make_zip([("SET/B/X-BASE.dxf", DXF_TEXT), ("SET/A/x-base.DXF", DXF_TEXT),
                     ("SET/A-101.dxf", DXF_TEXT)])
    out = unpack(write(tmp_path, data), str(tmp_path / "out"))
    names = out.by_basename()
    assert set(names) == {"x-base.dxf", "a-101.dxf"}
    # two members share a name: the first in sorted order answers, every time
    assert names["x-base.dxf"] == out.drawings["SET/A/x-base.DXF"]


def test_drawings_beyond_the_limit_are_skipped_not_silently_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "MAX_DRAWINGS", 2)
    data = make_zip([(f"S-{i}.dxf", DXF_TEXT) for i in range(4)])
    out = unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert len(out.drawings) == 2
    assert sorted(out.skipped.values()) == ["beyond the first 2 drawings"] * 2


# ── unpacking: what is refused ───────────────────────────────────────────────
@pytest.mark.parametrize("name", ["../../evil.dxf", "SET/../../evil.dxf", "/etc/evil.dxf",
                                  "C:/Windows/evil.dxf", "..\\..\\evil.dxf", "c:evil.dxf"])
def test_a_member_that_would_land_outside_the_directory_is_refused(tmp_path, name):
    data = make_zip([(name, DXF_TEXT), ("SET/A-101.dxf", DXF_TEXT)])
    dest = tmp_path / "deep" / "out"
    out = unpack(write(tmp_path, data), str(dest))
    assert list(out.drawings) == ["SET/A-101.dxf"]
    assert out.skipped == {name: "unsafe path"}
    assert not (tmp_path / "evil.dxf").exists() and not (tmp_path / "deep" / "evil.dxf").exists()
    assert files_under(tmp_path) == {"set.zip", os.path.join("deep", "out", "000_A-101.dxf")}


def test_a_zip_of_nothing_but_unsafe_members_holds_no_drawings(tmp_path):
    data = make_zip([("../evil.dxf", DXF_TEXT)])
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert exc.value.code == "no_drawings"


def _set_flag(data: bytes, name: bytes, bit: int) -> bytes:
    """Set a general-purpose flag bit on one member, in its local header and in
    the central directory — zipfile will not write an encrypted member itself."""
    b = bytearray(data)
    for sig, flag_at, name_len_at, name_at in ((b"PK\x03\x04", 6, 26, 30),
                                                (b"PK\x01\x02", 8, 28, 46)):
        i = b.find(sig)
        while i != -1:
            n = struct.unpack_from("<H", b, i + name_len_at)[0]
            if bytes(b[i + name_at:i + name_at + n]) == name:
                flags = struct.unpack_from("<H", b, i + flag_at)[0]
                struct.pack_into("<H", b, i + flag_at, flags | bit)
            i = b.find(sig, i + 4)
    return bytes(b)


def test_an_encrypted_member_is_skipped_and_said_so(tmp_path):
    data = _set_flag(make_zip([("SET/secret.dxf", DXF_TEXT), ("SET/A-101.dxf", DXF_TEXT)],
                              zipfile.ZIP_STORED), b"SET/secret.dxf", 0x1)
    assert zipfile.ZipFile(io.BytesIO(data)).getinfo("SET/secret.dxf").flag_bits & 0x1
    out = unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert list(out.drawings) == ["SET/A-101.dxf"]
    assert out.skipped == {"SET/secret.dxf": "encrypted"}


def test_a_zip_that_inflates_far_beyond_a_drawing_is_refused_before_writing(tmp_path):
    bomb = DXF_TEXT + b"\x00" * (8 << 20)                  # 8 MB that deflates to ~8 KB
    data = make_zip([("SET/A-101.dxf", DXF_TEXT), ("SET/bomb.dxf", bomb)])
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo("SET/bomb.dxf")
    assert info.file_size / info.compress_size > source.MAX_RATIO
    dest = tmp_path / "out"
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, data), str(dest))
    assert exc.value.code == "zip_bomb"
    assert files_under(dest) == set()                       # not a byte written


def _lie_about_size(data: bytes, name: bytes, declared: int) -> bytes:
    """Rewrite a member's uncompressed size in both headers to `declared`."""
    b = bytearray(data)
    for sig, size_at, name_len_at, name_at in ((b"PK\x03\x04", 22, 26, 30),
                                                (b"PK\x01\x02", 24, 28, 46)):
        i = b.find(sig)
        while i != -1:
            n = struct.unpack_from("<H", b, i + name_len_at)[0]
            if bytes(b[i + name_at:i + name_at + n]) == name:
                struct.pack_into("<I", b, i + size_at, declared)
            i = b.find(sig, i + 4)
    return bytes(b)


def test_a_member_whose_header_lies_about_its_size_is_refused(tmp_path):
    """Declared small, inflates large. The ratio check passes on the declared
    size, so the defence is extraction: it stops at what the header declares,
    the CRC then fails, and the set is refused as damaged — not left as a
    truncated drawing, and not an internal error."""
    body = DXF_TEXT + b"999\n" + os.urandom(1 << 16).hex().encode() + b"\n"
    data = _lie_about_size(make_zip([("SET/A-101.dxf", body)]), b"SET/A-101.dxf", 4000)
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo("SET/A-101.dxf")
    assert info.file_size == 4000 and info.compress_size > 4000
    dest = tmp_path / "out"
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, data), str(dest))
    assert exc.value.code == "corrupt_zip"
    assert files_under(dest) == set()


def test_too_many_members_is_refused(tmp_path):
    data = make_zip([(f"f{i}.txt", b"x") for i in range(source.MAX_MEMBERS + 1)],
                    zipfile.ZIP_STORED)
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert exc.value.code == "zip_too_large"
    assert str(source.MAX_MEMBERS) in exc.value.message


def test_a_zip_that_unpacks_beyond_the_size_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "MAX_UNPACKED_BYTES", 1000)
    data = make_zip([("a.dxf", DXF_TEXT + b"999\n" + b"y" * 2000 + b"\n")])
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert exc.value.code == "zip_too_large"


def test_a_zip_with_no_drawings_is_refused(tmp_path):
    data = make_zip([("notes.txt", b"read me"), ("plot.pdf", b"%PDF-1.7\n")])
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, data), str(tmp_path / "out"))
    assert exc.value.code == "no_drawings"


def test_bytes_that_only_start_like_a_zip_are_refused_as_damaged(tmp_path):
    with pytest.raises(SourceError) as exc:
        unpack(write(tmp_path, b"PK\x03\x04" + b"\x00" * 64), str(tmp_path / "out"))
    assert exc.value.code == "corrupt_zip"


def test_refusal_codes_are_the_closed_set_the_service_maps():
    """Every code a refusal here can carry is one `webapp/cadjob.py` maps onto
    the service's typed errors; a new code would reach the person untyped."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(source))
    codes = {node.args[0].value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "SourceError"
             and node.args and isinstance(node.args[0], ast.Constant)}
    assert codes == {"unsupported_dwg_version", "corrupt_zip", "zip_too_large", "zip_bomb",
                     "no_drawings"}

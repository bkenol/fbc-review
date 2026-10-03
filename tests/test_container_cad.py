"""The container carries what a drawing review needs, checked without Docker.

A DWG review depends on three things the Python test suite cannot see on its
own: LibreDWG's `dwg2dxf` in the image, a TrueType font for ezdxf to draw text
with, and exact pins for the libraries that place every text box. Each of them
fails silently when missing — a DWG upload refused as "conversion not
installed", sheets whose text plots as empty boxes, a rendering that moves when
a dependency floats — and none of them fails a unit test, because the tests
build their drawings in code and stand in for the converter.

So these read the files that build and deploy the service and assert the lines
that matter. They need no Docker daemon and no network; the Dockerfile's own
`RUN dwg2dxf --version` and font check are what fail the image build itself.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The LibreDWG release the reference drawing was converted with, and its
#: tarball's SHA-256 as published by GNU. The Dockerfile, the runbook's GPL
#: note and this file must agree; a bump changes all three on purpose.
LIBREDWG_VERSION = "0.14"
LIBREDWG_SHA256 = "62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275"


def _logical_lines(text: str) -> List[str]:
    """Dockerfile lines with `\\` continuations joined and comments dropped."""
    out, buf = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not buf and line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            out.append(buf.strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _stages() -> Dict[str, List[str]]:
    """Stage name -> its instructions. The final, unnamed stage is "runtime"."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    stages: Dict[str, List[str]] = {}
    current = None
    for line in _logical_lines(text):
        m = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
        if m:
            current = m.group(2) or "runtime"
            stages[current] = [line]
        elif current:
            stages[current].append(line)
    return stages


def _requirements(name: str) -> Dict[str, str]:
    pins = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-\[\]]+?)(\[[^\]]*\])?\s*(==|>=|~=|<=|>|<)\s*(\S+)$", line)
        if m:
            pins[m.group(1).lower()] = m.group(3) + m.group(4)
    return pins


# ── the Dockerfile ─────────────────────────────────────────────────────────────

def test_libredwg_is_built_in_its_own_stage_from_the_checksummed_gnu_release():
    stages = _stages()
    assert "libredwg" in stages, "a separate build stage keeps the compiler out of the runtime image"
    body = "\n".join(stages["libredwg"])
    assert f"LIBREDWG_VERSION={LIBREDWG_VERSION}" in body
    assert f"LIBREDWG_SHA256={LIBREDWG_SHA256}" in body
    assert "https://ftp.gnu.org/gnu/libredwg/libredwg-${LIBREDWG_VERSION}.tar.xz" in body
    assert "sha256sum -c" in body, "the download must be verified before it is built"
    # The flags the reference drawing's conversion was measured with.
    for flag in ("--disable-bindings", "--disable-python", "--disable-docs"):
        assert flag in body


def test_the_runtime_image_gets_dwg2dxf_and_its_library_and_names_it():
    stages = _stages()
    runtime = stages["runtime"]
    body = "\n".join(runtime)
    builder = "\n".join(stages["libredwg"])

    # What the builder stages for copying: the program and the versioned library.
    assert re.search(r"install -s \S*/bin/dwg2dxf /out/usr/local/bin/dwg2dxf", builder)
    assert re.search(r"cp -P \S*/lib/libredwg\.so\.0\* /out/usr/local/lib/", builder)

    copies = [l for l in runtime if l.upper().startswith("COPY --FROM=LIBREDWG")]
    assert copies == ["COPY --from=libredwg /out/ /"], (
        "copy the staged directory whole: a COPY of a symlink copies its target")
    assert any(re.match(r"RUN ldconfig && dwg2dxf --version$", l) for l in runtime), (
        "the build must fail, not the first DWG review, if dwg2dxf cannot load")
    assert re.search(r"^ENV FBC_DWG2DXF=/usr/local/bin/dwg2dxf$", body, re.M)


def test_the_compiler_never_reaches_the_runtime_image():
    runtime = "\n".join(_stages()["runtime"])
    for tool in ("build-essential", "gcc", "make ", "./configure"):
        assert tool not in runtime


def test_the_runtime_image_has_a_truetype_font_for_ezdxf():
    runtime = _stages()["runtime"]
    apt = [l for l in runtime if "apt-get install" in l]
    assert apt and "fonts-dejavu-core" in apt[0], (
        "ezdxf draws text with system fonts; with none it draws every string as a box")


def test_the_font_cache_is_warmed_as_the_service_user():
    runtime = _stages()["runtime"]
    user_at = next(i for i, l in enumerate(runtime) if re.match(r"USER fbc$", l))
    cache_at = next(i for i, l in enumerate(runtime) if l.startswith("ENV XDG_CACHE_HOME="))
    warm_at = next(i for i, l in enumerate(runtime) if "from ezdxf.fonts import fonts" in l)
    assert user_at < cache_at < warm_at, (
        "built as root, or under another cache path, the cache is not the one the "
        "service finds at run time")
    assert "has_font('DejaVuSans.ttf')" in runtime[warm_at]


# ── the pins ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("package, version", [
    ("ezdxf", "1.4.4"),
    ("pillow", "12.3.0"),
    ("fonttools", "4.66.1"),
    ("pyparsing", "3.3.3"),
])
def test_cad_dependencies_are_pinned_exactly(package, version):
    pins = _requirements("requirements.txt")
    assert pins.get(package) == f"=={version}", (
        f"{package} must be pinned exactly: it moves every text box on a plotted sheet")


def test_ci_installs_the_runtime_pins():
    """CI's pytest job installs requirements-dev.txt; that file must pull the
    runtime pins in, or the CAD tests run against whatever pip resolves."""
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert "-r requirements.txt" in [l.strip() for l in dev]
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "pip install -r requirements-dev.txt" in workflow


def test_the_installed_ezdxf_is_the_pinned_one():
    """The ezdxf version is part of a drawing's AI readings cache key and decides
    where every text cell lands. Tests run against a different one test a
    rendering the service does not produce."""
    ezdxf = pytest.importorskip("ezdxf")
    assert _requirements("requirements.txt")["ezdxf"] == f"=={ezdxf.__version__}", (
        "install requirements.txt: the ezdxf in this environment is not the pinned one")


# ── deployment sizing ─────────────────────────────────────────────────────────

def test_both_deploy_paths_give_a_drawing_review_room():
    """provision.sh and the CI deploy put up the same service; a drawing read
    peaks at 1.13 GB in a subprocess beside it, so neither may be the old 2Gi,
    and both hold CAD work to one drawing at a time per instance."""
    provision = (ROOT / "scripts" / "provision.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert re.search(r"--memory=4Gi\b", provision)
    assert re.search(r"--memory=4Gi\b", workflow)
    assert "--memory=2Gi" not in provision and "--memory=2Gi" not in workflow
    assert "FBC_CAD_CONCURRENCY=1" in provision
    assert re.search(r"^\s*FBC_CAD_CONCURRENCY=1\s*$", workflow, re.M)


def test_the_gpl_source_note_matches_the_image():
    """The runbook tells whoever distributes the image where the corresponding
    source is. It must name the tarball the image is actually built from."""
    runbook = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert f"https://ftp.gnu.org/gnu/libredwg/libredwg-{LIBREDWG_VERSION}.tar.xz" in runbook
    assert LIBREDWG_SHA256 in runbook


# ── the repository ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pattern", ["*.dwg", "*.dxf", "*.zip"])
def test_client_drawings_are_ignored(pattern):
    lines = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert pattern in lines

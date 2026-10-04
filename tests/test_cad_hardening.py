"""The drawing-upload path, hardened against what an upload can do to the service.

Four defects, each reproduced by one reviewer and confirmed by a second, each
with its failing input written down here:

1. **A zip's directory was parsed whole before any limit.** `zipfile.ZipFile`
   reads every central-directory entry before `MAX_MEMBERS` is looked at, so a
   120 MB zip of millions of empty members cost a gigabyte and nine seconds —
   on the event loop, in the request handler. The end record now decides first.
2. **The adapter and the DWG converter inherited every service secret.** Both
   read untrusted input; neither needs `ANTHROPIC_API_KEY` or a credentials path.
3. **The adapter's stderr was held whole in the service.** ezdxf echoes tag
   values — drawing content — into it, with no bound on how much.
4. **A timed-out conversion left a zombie.** Killing the adapter's process group
   orphans the converter it started; an orphan is re-parented to PID 1, and in
   the container PID 1 is this service (`CMD exec uvicorn`), which never reaped it.

No client drawing is used and no network is touched.
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import struct
import subprocess
import sys
import textwrap
import tracemalloc
import zipfile
from pathlib import Path

import pytest

ezdxf = pytest.importorskip("ezdxf")

from fbcreview.cad import convert, source  # noqa: E402
from test_worker_cad import dwg_bytes, fake_dwg2dxf, make_dxf  # noqa: E402
from webapp import upload  # noqa: E402
from webapp.config import settings  # noqa: E402
from webapp.errors import ApiError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: Taken before any test replaces it, so inputs can still be written with it.
REAL_ZIPFILE = zipfile.ZipFile

DXF_TEXT = (b"999\nmade by a test\n0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n"
            b"0\nEOF\n")


@pytest.fixture(autouse=True)
def _fresh_settings():
    settings.cache_clear()
    yield
    settings.cache_clear()


# ── 1. a zip's directory is bounded before it is parsed ───────────────────
def empty_members(n: int, *, declared: int | None = None) -> bytes:
    """A zip of `n` stored, empty members all named `a`, built by hand.

    The reviewer's input (`zipdos.py`): 120 MB of this is 1.5 million members,
    and `zipfile` built a `ZipInfo` for every one before the limit was checked.
    `declared` is what the end record claims; a hostile zip need not tell the truth.
    """
    local, directory, offset = bytearray(), bytearray(), 0
    for _ in range(n):
        header = struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0, 0, 0, 0, 1, 0) + b"a"
        directory += struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 0, 0, 0,
                                 0, 0, 0, 1, 0, 0, 0, 0, 0, offset) + b"a"
        local += header
        offset += len(header)
    count = min(n if declared is None else declared, 0xFFFF)
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, count, count, len(directory), offset, 0)
    return bytes(local) + bytes(directory) + end


def zip64(members: int) -> bytes:
    """A real ZIP64 archive, as a tool writes one past 65 535 members: the counts
    and sizes live in the ZIP64 end record and the classic one holds only the
    0xFFFF / 0xFFFFFFFF placeholders. Written by `zipfile` with its member limit
    lowered, so a few members are enough to get the ZIP64 records."""
    buf = io.BytesIO()
    real = zipfile.ZIP_FILECOUNT_LIMIT
    zipfile.ZIP_FILECOUNT_LIMIT = 0
    try:
        with REAL_ZIPFILE(buf, "w", zipfile.ZIP_STORED) as zf:
            for i in range(members):
                zf.writestr(f"SET/S-{i:03d}.dxf", DXF_TEXT)
    finally:
        zipfile.ZIP_FILECOUNT_LIMIT = real
    data = bytearray(buf.getvalue())
    at = data.rfind(b"PK\x05\x06")
    assert data.rfind(b"PK\x06\x07") == at - 20 and data.rfind(b"PK\x06\x06") == at - 76
    struct.pack_into("<HHII", data, at + 8, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    return bytes(data)


@pytest.fixture
def unparsed(monkeypatch):
    """Fails the test if anything builds a ZipFile: the refusal has to come first."""
    class Parsed(Exception):
        pass

    def refuse(*_a, **_k):
        raise Parsed("the central directory was parsed before the limit was checked")

    monkeypatch.setattr(zipfile, "ZipFile", refuse)
    return Parsed


def write(tmp_path: Path, data: bytes, name: str = "set.zip") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_too_many_members_is_refused_from_the_end_record_at_the_door(tmp_path, unparsed):
    path = write(tmp_path, empty_members(source.MAX_MEMBERS + 1))
    with pytest.raises(ApiError) as exc:
        upload.check_archive(path)
    assert (exc.value.status, exc.value.code) == (413, "unsafe_archive")
    assert str(source.MAX_MEMBERS) in exc.value.message


def test_too_many_members_is_refused_from_the_end_record_when_unpacking(tmp_path, unparsed):
    path = write(tmp_path, empty_members(source.MAX_MEMBERS + 1))
    with pytest.raises(source.SourceError) as exc:
        source.unpack(str(path), str(tmp_path / "out"))
    assert exc.value.code == "zip_too_large"
    assert str(source.MAX_MEMBERS) in exc.value.message
    assert not any((tmp_path / "out").iterdir())


def test_a_directory_bigger_than_its_count_admits_is_refused_unparsed(tmp_path, unparsed):
    """The end record says one member; the directory holds 40 000. zipfile walks
    the directory by its byte size, not by the count, so the size is what bounds
    the work: 40 000 entries is 1.9 MB of directory, past what 400 members could
    take at any plausible name and extra-field length."""
    path = write(tmp_path, empty_members(40_000, declared=1))
    with pytest.raises(ApiError) as exc:
        upload.check_archive(path)
    assert (exc.value.status, exc.value.code) == (413, "unsafe_archive")
    with pytest.raises(source.SourceError) as exc2:
        source.unpack(str(path), str(tmp_path / "out"))
    assert exc2.value.code == "zip_too_large"


def test_a_zip64_count_over_the_limit_is_refused_unparsed(tmp_path, unparsed):
    path = write(tmp_path, zip64(source.MAX_MEMBERS + 1))
    with pytest.raises(ApiError) as exc:
        upload.check_archive(path)
    assert (exc.value.status, exc.value.code) == (413, "unsafe_archive")
    assert str(source.MAX_MEMBERS + 1) in exc.value.message


def test_a_real_zip64_archive_within_the_limit_is_still_read(tmp_path):
    """The classic record's 0xFFFF placeholders are not a count of 65 535."""
    path = write(tmp_path, zip64(3))
    assert sorted(upload.check_archive(path)) == [f"SET/S-{i:03d}.dxf" for i in range(3)]
    out = source.unpack(str(path), str(tmp_path / "out"))
    assert sorted(out.drawings) == [f"SET/S-{i:03d}.dxf" for i in range(3)]


def test_a_zip_with_a_comment_is_still_read(tmp_path):
    """The end record is found behind an archive comment, as zipfile finds it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("A-101.dxf", DXF_TEXT)
        zf.comment = b"x" * 60_000
    assert upload.check_archive(write(tmp_path, buf.getvalue())) == ["A-101.dxf"]


@pytest.mark.parametrize("route", ["/api/review", "/api/prefill"])
def test_a_zip_is_admitted_off_the_event_loop(client, monkeypatch, route):
    """The directory checks read a file. Called straight from the async handler
    they hold the loop that answers every other request on the instance."""
    from webapp import server

    monkeypatch.setattr(server, "run_review", lambda **kw: None)
    seen = []
    real = upload.admit_cad

    def watching(path, kind):
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("thread")
        return real(path, kind)

    monkeypatch.setattr(upload, "admit_cad", watching)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("A-101.dxf", DXF_TEXT)
    r = client.post(route, files={"file": ("set.zip", io.BytesIO(buf.getvalue()), "x")},
                    data={"review_options": "{}"})
    assert r.status_code in (202, 422), r.text
    assert seen == ["thread"]


def test_a_refusal_from_the_thread_keeps_its_envelope(client, monkeypatch):
    from webapp import server

    monkeypatch.setattr(server, "run_review", lambda **kw: None)
    body = empty_members(source.MAX_MEMBERS + 1)
    r = client.post("/api/review", files={"file": ("many.zip", io.BytesIO(body), "x")},
                    data={"review_options": "{}"})
    assert r.status_code == 413
    assert set(r.json()) == {"error"}
    assert r.json()["error"]["code"] == "unsafe_archive"
    assert client.fake_store.docs == {} and client.fake_files.blobs == {}


# ── 2. what the subprocesses are given of the service's environment ───────
SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-HARDENING-TEST",
    "GOOGLE_APPLICATION_CREDENTIALS": "/var/secrets/HARDENING-key.json",
    "FBC_ARTEFACT_SECRET": "HARDENING-artefact-secret",
    "FBC_SMTP_PASS": "HARDENING-smtp",
    "FBC_GITHUB_TOKEN": "ghp_HARDENING",
}


def env_dumping_dwg2dxf(tmp_path: Path, prepared: Path, dump: Path) -> Path:
    """A stand-in converter that writes the environment it was given to `dump`,
    one JSON object per run, then converts by copying a prepared DXF."""
    exe = tmp_path / "dwg2dxf"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, os, shutil, sys\n"
        f"with open({str(dump)!r}, 'a') as fh:\n"
        "    fh.write(json.dumps(dict(os.environ)) + '\\n')\n"
        "if '--version' in sys.argv:\n"
        "    print('dwg2dxf 0.0-test'); sys.exit(0)\n"
        f"shutil.copyfile({str(prepared)!r}, sys.argv[sys.argv.index('-o') + 1])\n")
    exe.chmod(0o755)
    return exe


def assert_no_secret(env: dict) -> None:
    # Names only in the failure message: an environment printed into a test log
    # is a leak of its own wherever the suite runs beside real credentials.
    leaked = sorted(name for name, value in SECRETS.items()
                    if name in env or any(value in v for v in env.values()))
    assert leaked == [], f"handed to the subprocess: {leaked}"
    config = sorted(k for k in ("FBC_ALLOWED_EMAILS", "FBC_BUCKET") if k in env)
    assert config == [], f"handed to the subprocess: {config}"


@pytest.fixture
def secrets_set(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)


def test_the_adapter_subprocess_is_given_no_service_secret(tmp_path, monkeypatch, secrets_set):
    from webapp import cadjob

    monkeypatch.setenv("FBC_DWG_TIMEOUT_S", "77")
    monkeypatch.setenv("FBC_CAD_MAX_DXF_MB", "123")
    monkeypatch.setenv("FBC_DWG2DXF", str(tmp_path / "not-installed"))
    seen = []
    real = subprocess.Popen

    class Watching(real):
        def __init__(self, args, *a, **kw):
            if "fbcreview.cad" in args:
                seen.append(kw.get("env"))
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Watching)
    drawing = make_dxf(tmp_path / "tower.dxf")
    done = cadjob.ingest(drawing, tmp_path / "out", "tower.dxf", "job-env")
    assert done.sidecar["pages"]
    assert len(seen) == 1
    env = seen[0] if seen[0] is not None else dict(os.environ)   # None: inherited whole
    assert_no_secret(env)
    # What the adapter does read is passed on, and nothing else of ours.
    assert env["FBC_DWG2DXF"] == str(tmp_path / "not-installed")
    assert (env["FBC_DWG_TIMEOUT_S"], env["FBC_CAD_MAX_DXF_MB"]) == ("77", "123")
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT)
    assert env["PATH"] == os.environ["PATH"]
    assert not [k for k in env if k.startswith("FBC_") and k not in (
        "FBC_DWG2DXF", "FBC_DWG_TIMEOUT_S", "FBC_CAD_MAX_DXF_MB")]


def test_the_converter_run_by_the_adapter_sees_no_service_secret(tmp_path, monkeypatch,
                                                                 secrets_set):
    """End to end, as `envchk.py` reproduced it: a DWG through the real
    subprocess, a converter that records what it was given."""
    from webapp import cadjob

    prepared = make_dxf(tmp_path / "prepared.dxf")
    dump = tmp_path / "converter-env.jsonl"
    monkeypatch.setenv("FBC_DWG2DXF", str(env_dumping_dwg2dxf(tmp_path, prepared, dump)))
    upload_path = tmp_path / "Tower.dwg"
    upload_path.write_bytes(dwg_bytes())
    cadjob.ingest(upload_path, tmp_path / "out", "Tower.dwg", "job-env")
    runs = [json.loads(line) for line in dump.read_text().splitlines()]
    assert len(runs) >= 2                                      # --version, then the conversion
    for env in runs:
        assert_no_secret(env)
        assert not [k for k in env if k.startswith("FBC_")], sorted(env)


def test_the_converter_is_given_only_what_it_needs(tmp_path, monkeypatch, secrets_set):
    """dwg2dxf needs a PATH, a library path and a locale. Called in-process, so
    what is checked is `convert.py`'s own environment, not the adapter's."""
    prepared = make_dxf(tmp_path / "prepared.dxf")
    dump = tmp_path / "converter-env.jsonl"
    exe = env_dumping_dwg2dxf(tmp_path, prepared, dump)
    monkeypatch.setenv("FBC_DWG2DXF", str(exe))
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/libredwg/lib")
    dwg = tmp_path / "t.dwg"
    dwg.write_bytes(dwg_bytes())
    result = convert.dwg_to_dxf(str(dwg), str(tmp_path / "t.dxf"))
    assert result.converter == "dwg2dxf 0.0-test"
    runs = [json.loads(line) for line in dump.read_text().splitlines()]
    assert len(runs) == 2
    for env in runs:
        assert_no_secret(env)
        # LC_CTYPE: Python sets it in its own environment when it coerces a C
        # locale (PEP 538); the stand-in converter is a Python script.
        assert set(env) - {"LC_CTYPE"} <= {"PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL"}, sorted(env)
        assert env["PATH"] == os.environ["PATH"]
        assert env["LD_LIBRARY_PATH"] == "/opt/libredwg/lib"


# ── 3. what the service keeps of the adapter's output ─────────────────────
#: One line of what ezdxf writes to stderr about a value it cannot parse: the
#: value itself, which is the drawing's text.
WARNING_LINE = b"WARNING:ezdxf:invalid value 'CONFIDENTIAL CLIENT TEXT " + b"x" * 190 + b"'\n"


def chatty_python(tmp_path: Path, *, stderr_bytes: int, stdout_bytes: int) -> Path:
    """A stand-in for the interpreter `cadjob` starts. It ignores `-m fbcreview.cad
    …`, writes `stderr_bytes` of drawing-like warnings (the way ezdxf echoes a tag
    value it cannot parse) and a last line with no newline, `stdout_bytes` of
    chatter, then the one JSON line the adapter prints."""
    exe = tmp_path / "python"
    exe.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""\
        import json, sys
        line = {WARNING_LINE!r}
        err = sys.stderr.buffer
        for _ in range({stderr_bytes} // len(line)):
            err.write(line)
        err.write(b"no newline at the end")
        err.flush()
        chatter = b"plotting layout\\n"
        for _ in range({stdout_bytes} // len(chatter)):
            sys.stdout.buffer.write(chatter)
        sys.stdout.buffer.write(json.dumps({{"pdf": "rendered.pdf", "pages": 3}}).encode() + b"\\n")
        """))
    exe.chmod(0o755)
    return exe


def test_the_adapters_stderr_is_counted_not_held(tmp_path, monkeypatch):
    """48 MB of stderr, as a drawing with a warning per entity produces. The
    service needs the line count for its log, and nothing else of it."""
    from webapp import cadjob

    stderr_bytes = 48 << 20
    monkeypatch.setattr(sys, "executable", str(chatty_python(tmp_path, stderr_bytes=stderr_bytes,
                                                              stdout_bytes=0)))
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        code, stdout, err_lines, _ = cadjob._run(["ingest", "x", "y"], tmp_path, 120)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert code == 0
    assert err_lines == stderr_bytes // len(WARNING_LINE) + 1           # the unterminated last line counts
    assert cadjob._last_json(stdout) == {"pdf": "rendered.pdf", "pages": 3}
    assert peak < 4 << 20, f"the service held {peak / 2 ** 20:.0f} MB of the adapter's output"


def test_the_adapters_stdout_is_bounded_and_its_last_line_still_read(tmp_path, monkeypatch):
    from webapp import cadjob

    monkeypatch.setattr(sys, "executable", str(chatty_python(tmp_path, stderr_bytes=0,
                                                              stdout_bytes=4 << 20)))
    code, stdout, err_lines, _ = cadjob._run(["ingest", "x", "y"], tmp_path, 120)
    assert code == 0 and err_lines == 1
    assert len(stdout) <= 64 * 1024
    assert cadjob._last_json(stdout) == {"pdf": "rendered.pdf", "pages": 3}


def test_nothing_the_adapter_prints_is_written_beside_the_job(tmp_path, monkeypatch):
    from webapp import cadjob

    monkeypatch.setattr(sys, "executable", str(chatty_python(tmp_path, stderr_bytes=1 << 20,
                                                              stdout_bytes=1 << 10)))
    work = tmp_path / "job"
    work.mkdir()
    cadjob._run(["ingest", "x", "y"], work, 120)
    assert list(work.iterdir()) == []


# ── 4. a conversion stopped at the deadline leaves nothing behind ─────────
REAPER = textwrap.dedent("""\
    # Runs as the container's PID 1 would: a child subreaper, so a process
    # orphaned below it is re-parented here rather than to the host's init.
    import ctypes, dataclasses, json, os, subprocess, sys, time
    from pathlib import Path

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:                     # PR_SET_CHILD_SUBREAPER
        print(json.dumps({"skip": "prctl refused"})); sys.exit(0)

    from webapp import cadjob
    from webapp.config import settings

    quick = dataclasses.replace(settings(), cad_timeout_seconds=float(sys.argv[2]))
    cadjob.settings = lambda: quick
    adapters = []
    real = subprocess.Popen

    class Watching(real):
        def __init__(self, args, *a, **kw):
            super().__init__(args, *a, **kw)
            if "fbcreview.cad" in args:
                adapters.append(self.pid)

    subprocess.Popen = Watching
    work = Path(sys.argv[1])
    code = None
    try:
        cadjob.ingest(work / "Tower.dwg", work / "out", "Tower.dwg", "job-zombie")
    except cadjob.CadJobError as exc:
        code = exc.code
    time.sleep(0.5)
    me, zombies, children = os.getpid(), [], []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()
        if int(fields[1]) == me:
            children.append(int(entry))
            if fields[0] == "Z":
                zombies.append(int(entry))
    reaped = []
    for pid in adapters:
        try:
            os.waitpid(pid, os.WNOHANG)
            reaped.append(False)
        except ChildProcessError:
            reaped.append(True)
    print(json.dumps({"code": code, "adapters_reaped": reaped, "zombies": zombies,
                      "children": children}))
    """)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_CHILD_SUBREAPER is Linux")
def test_a_timed_out_conversion_leaves_no_zombie(tmp_path):
    """`sec_sleepy_dwg2dxf.sh`: a converter that never finishes, killed at the
    deadline with the adapter that started it. Run under a subreaper, which is
    what the service is in the container — PID 1 — so the orphaned converter
    comes to it, as it does in production."""
    sleepy = tmp_path / "dwg2dxf"
    sleepy.write_text('#!/bin/sh\n[ "$1" = "--version" ] && { echo "dwg2dxf 0.0-test"; exit 0; }\n'
                      "exec sleep 300\n")
    sleepy.chmod(0o755)
    (tmp_path / "Tower.dwg").write_bytes(dwg_bytes())
    helper = tmp_path / "reaper.py"
    helper.write_text(REAPER)
    env = dict(os.environ, FBC_DWG2DXF=str(sleepy),
               PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]))
    run = subprocess.run([sys.executable, str(helper), str(tmp_path), "4"], cwd=str(tmp_path),
                         env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-2000:]
    result = json.loads(run.stdout.strip().splitlines()[-1])
    if "skip" in result:
        pytest.skip(result["skip"])
    assert result["code"] == "corrupt_cad"
    assert result["adapters_reaped"] == [True]
    assert result["zombies"] == [], "the converter killed at the deadline was never reaped"
    assert result["children"] == []

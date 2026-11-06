"""Output names must not follow links or clobber concurrent conversions."""

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from convert_anything_md.paths import conflict_safe_name
from convert_anything_md.router import convert_file


@pytest.mark.parametrize("target_exists", [False, True])
@pytest.mark.parametrize("overwrite", [False, True])
def test_output_symlink_never_writes_target(tmp_path, target_exists, overwrite):
    source = tmp_path / "doc.txt"
    source.write_text("converted text")
    output = tmp_path / "out"
    output.mkdir()
    target = tmp_path / "outside.md"
    if target_exists:
        target.write_text("keep outside")
    link = output / "doc.md"
    link.symlink_to("../outside.md")

    result = convert_file(source, output_dir=output, overwrite=overwrite)

    assert result.ok, result.error
    assert result.output == output / ("doc.md" if overwrite else "doc (1).md")
    assert not result.output.is_symlink()
    assert "converted text" in result.output.read_text()
    assert target.exists() == target_exists
    if target_exists:
        assert target.read_text() == "keep outside"
    assert link.is_symlink() == (not overwrite)


def test_dry_run_skips_dangling_links_without_writing(tmp_path):
    source = tmp_path / "doc.txt"
    source.write_text("text")
    output = tmp_path / "out"
    output.mkdir()
    (output / "doc.md").symlink_to("../outside.md")
    (output / "doc (1).md").symlink_to("../also-outside.md")

    result = convert_file(source, output_dir=output, dry_run=True)

    assert result.ok
    assert result.output == output / "doc (2).md"
    assert not result.output.exists()
    assert not (tmp_path / "outside.md").exists()
    assert len(list(output.iterdir())) == 2


@pytest.mark.parametrize("collision", ["file", "symlink", "directory"])
def test_collision_at_open_retries_without_clobbering(tmp_path, monkeypatch, collision):
    source = tmp_path / "doc.txt"
    source.write_text("converted text")
    output = tmp_path / "out"
    output.mkdir()
    candidate = output / "doc.md"
    original_open = Path.open
    injected = False

    def racing_open(path, mode="r", *args, **kwargs):
        nonlocal injected
        if path == candidate and mode in {"x", "w"} and not injected:
            injected = True
            if collision == "file":
                with original_open(path, "w") as stream:
                    stream.write("other writer")
            elif collision == "symlink":
                path.symlink_to("../outside.md")
            else:
                path.mkdir()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    result = convert_file(source, output_dir=output)

    assert injected
    assert result.ok, result.error
    assert result.output == output / "doc (1).md"
    assert "converted text" in result.output.read_text()
    if collision == "file":
        assert candidate.read_text() == "other writer"
    elif collision == "symlink":
        assert candidate.is_symlink()
        assert not (tmp_path / "outside.md").exists()
    else:
        assert candidate.is_dir()


def test_concurrent_conversions_keep_both_results(tmp_path, monkeypatch):
    sources = []
    for i in range(2):
        directory = tmp_path / str(i)
        directory.mkdir()
        source = directory / "same.txt"
        source.write_text(f"worker {i}")
        sources.append(source)
    output = tmp_path / "out"
    output.mkdir()
    barrier = Barrier(2)
    original_open = Path.open

    def synchronized_open(path, mode="r", *args, **kwargs):
        if path == output / "same.md" and mode in {"x", "w"}:
            barrier.wait(timeout=10)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", synchronized_open)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: convert_file(p, output_dir=output), sources))

    assert all(result.ok for result in results), [result.error for result in results]
    assert {result.output for result in results} == {output / "same.md", output / "same (1).md"}
    for i, result in enumerate(results):
        assert f"worker {i}" in result.output.read_text()
        assert f"worker {1 - i}" not in result.output.read_text()


def test_exhausted_names_fail_instead_of_reusing_pid(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.lexists", lambda path: True)
    monkeypatch.setattr(Path, "exists", lambda path: True)
    with pytest.raises(FileExistsError, match="available output name"):
        conflict_safe_name(tmp_path, "doc")


@pytest.mark.parametrize("overwrite", [False, True])
def test_counter_suffix_and_regular_overwrite(tmp_path, overwrite):
    source = tmp_path / "doc (7).txt"
    source.write_text("replacement")
    output = tmp_path / "out"
    output.mkdir()
    original = output / "doc.md"
    original.write_text("original")

    result = convert_file(source, output_dir=output, overwrite=overwrite)

    assert result.ok, result.error
    assert result.output == output / ("doc.md" if overwrite else "doc (1).md")
    assert "replacement" in result.output.read_text()
    if not overwrite:
        assert original.read_text() == "original"


def test_overwrite_directory_fails_without_temp_files(tmp_path):
    source = tmp_path / "doc.txt"
    source.write_text("replacement")
    output = tmp_path / "out"
    output.mkdir()
    existing = output / "doc.md"
    existing.mkdir()

    result = convert_file(source, output_dir=output, overwrite=True)

    assert not result.ok
    assert result.output is None
    assert result.error.startswith("cannot write output:")
    assert list(output.iterdir()) == [existing]


@pytest.mark.parametrize("stage", ["write", "replace"])
def test_overwrite_failure_preserves_existing_output_and_cleans_up(tmp_path, monkeypatch, stage):
    source = tmp_path / "doc.txt"
    source.write_text("replacement")
    output = tmp_path / "out"
    output.mkdir()
    existing = output / "doc.md"
    existing.write_text("original")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    if stage == "write":
        original_open = Path.open

        def failing_open(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if mode == "x" and path.parent.name.startswith(".cam-"):
                stream.write = fail
            return stream

        monkeypatch.setattr(Path, "open", failing_open)
    else:
        monkeypatch.setattr(Path, "replace", fail)

    result = convert_file(source, output_dir=output, overwrite=True)

    assert not result.ok
    assert result.output is None
    assert "disk full" in result.error
    assert existing.read_text() == "original"
    assert list(output.iterdir()) == [existing]


def test_symlink_inserted_at_overwrite_commit_does_not_redirect_write(tmp_path, monkeypatch):
    source = tmp_path / "doc.txt"
    source.write_text("replacement")
    output = tmp_path / "out"
    output.mkdir()
    target = tmp_path / "outside.md"
    target.write_text("untouched")
    original_replace = Path.replace

    def racing_replace(path, destination):
        destination.symlink_to("../outside.md")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", racing_replace)
    result = convert_file(source, output_dir=output, overwrite=True)

    assert result.ok, result.error
    assert not result.output.is_symlink()
    assert "replacement" in result.output.read_text()
    assert target.read_text() == "untouched"


def test_name_exhaustion_is_reported_as_failed_conversion(tmp_path, monkeypatch):
    source = tmp_path / "doc.txt"
    source.write_text("text")
    original_open = Path.open
    attempts = 0

    def occupied(path, mode="r", *args, **kwargs):
        nonlocal attempts
        if mode == "x":
            attempts += 1
            raise FileExistsError("occupied")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", occupied)
    result = convert_file(source, output_dir=tmp_path / "out")

    assert not result.ok
    assert result.output is None
    assert "no available output name" in result.error
    assert attempts == 10_000


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("existing", [False, True])
def test_overwrite_preserves_normal_output_permissions(tmp_path, existing):
    source = tmp_path / "doc.txt"
    source.write_text("replacement")
    output = tmp_path / "out"
    output.mkdir()
    destination = output / "doc.md"
    if existing:
        destination.write_text("original")
        destination.chmod(0o664)
        expected_mode = 0o664
    else:
        # Compare normal creation under the current umask without changing it
        # globally (which would race other writers).
        control = output / "control.md"
        control.write_text("control")
        expected_mode = stat.S_IMODE(control.stat().st_mode)

    result = convert_file(source, output_dir=output, overwrite=True)

    assert result.ok, result.error
    assert stat.S_IMODE(destination.stat().st_mode) == expected_mode


@pytest.mark.parametrize("occupied", [False, True])
def test_windows_directory_collision_does_not_hide_access_failures(tmp_path, monkeypatch, occupied):
    source = tmp_path / "doc.txt"
    source.write_text("text")
    output = tmp_path / "out"
    output.mkdir()
    destination = output / "doc.md"
    if occupied:
        destination.mkdir()
    original_open = Path.open

    def windows_open(path, mode="r", *args, **kwargs):
        if path == destination and mode == "x":
            raise PermissionError("access denied")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", windows_open)
    result = convert_file(source, output_dir=output)

    if occupied:
        assert result.ok, result.error
        assert result.output == output / "doc (1).md"
        assert destination.is_dir()
    else:
        assert not result.ok
        assert result.output is None
        assert "access denied" in result.error

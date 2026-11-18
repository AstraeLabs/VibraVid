# 15.10.26

"""``CompiledService``: per-platform compiled builds (``<name>/<name>_<os>_py<ver>/<name>.<abi>.pyd|so``, no ``__init__.py``)."""

import importlib.machinery

import pytest

from VibraVid.services._base.site_loader import CompiledService

SUFFIX = importlib.machinery.EXTENSION_SUFFIXES[0]
OTHER_SUFFIX = ".cpython-399-nowhere-os.so"


def _make_variant(service_dir, variant, suffix, name="svc"):
    folder = service_dir / variant
    folder.mkdir(parents=True)
    (folder / f"{name}{suffix}").write_bytes(b"")
    return folder


def test_find_entry_picks_the_variant_built_for_this_interpreter(tmp_path):
    _make_variant(tmp_path, "svc_other_os_py399", OTHER_SUFFIX)
    mine = _make_variant(tmp_path, "svc_this_os_pyX", SUFFIX)

    entry = CompiledService("svc", str(tmp_path)).find_entry()

    assert entry == str(mine / f"svc{SUFFIX}")


def test_is_candidate_needs_variant_folders_and_no_init(tmp_path):
    service = CompiledService("svc", str(tmp_path))
    assert not service.is_candidate()  # empty folder

    (tmp_path / "notes.txt").write_text("x")
    assert not service.is_candidate()  # files only, no variant folder

    _make_variant(tmp_path, "svc_this_os_pyX", SUFFIX)
    assert service.is_candidate()

    (tmp_path / "__init__.py").write_text("")
    assert not service.is_candidate()  # a source service is loaded the normal way


def test_no_matching_build_is_reported_with_the_available_variants(tmp_path):
    _make_variant(tmp_path, "svc_other_os_py399", OTHER_SUFFIX)
    service = CompiledService("svc", str(tmp_path))

    assert service.find_entry() is None
    with pytest.raises(ImportError, match="svc_other_os_py399"):
        service.load("VibraVid.services._compiled_service_test_missing")

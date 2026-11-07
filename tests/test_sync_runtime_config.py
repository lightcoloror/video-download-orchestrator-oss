from importlib.util import module_from_spec
from importlib.util import spec_from_file_location
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_runtime_config.py"
SPEC = spec_from_file_location("vdo_sync_runtime_config", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SYNC_MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC_MODULE)


def test_write_text_if_changed_skips_identical_generated_file(tmp_path):
    path = tmp_path / "generated.js"
    path.write_text("same\n", encoding="utf-8")

    changed = SYNC_MODULE.write_text_if_changed(path, "same\n")

    assert changed is False
    assert path.read_text(encoding="utf-8") == "same\n"


def test_write_text_if_changed_updates_changed_content(tmp_path):
    path = tmp_path / "generated.json"
    path.write_text("old\n", encoding="utf-8")

    changed = SYNC_MODULE.write_text_if_changed(path, "new\n")

    assert changed is True
    assert path.read_text(encoding="utf-8") == "new\n"

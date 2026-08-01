import json
from pathlib import Path

from tests.test_export import sample_record

from pipeline.export import export_static_artifacts
from pipeline.publish import deploy_command, prepare_vercel_source, publish_preflight


def linked_web_dir(tmp_path: Path) -> Path:
    web_dir = tmp_path / "web"
    vercel_dir = web_dir / ".vercel"
    vercel_dir.mkdir(parents=True)
    (vercel_dir / "project.json").write_text(
        json.dumps(
            {
                "projectId": "prj_test",
                "orgId": "team_test",
                "projectName": "shiok-test",
            }
        ),
        encoding="utf-8",
    )
    return web_dir


def test_publish_preflight_rejects_missing_static_artifacts(tmp_path: Path):
    web_dir = linked_web_dir(tmp_path)

    ok, report = publish_preflight(
        input_dir=tmp_path / "missing-data",
        web_dir=web_dir,
        run_external_checks=False,
    )

    assert not ok
    assert report["deploy_executed"] is False
    assert "publish blocked: static artifact validation failed" in report["errors"]


def test_publish_preflight_accepts_valid_artifacts_and_link_without_external_checks(tmp_path: Path):
    data_dir = tmp_path / "data"
    export_static_artifacts([sample_record("123456")], output_dir=data_dir)
    web_dir = linked_web_dir(tmp_path)

    ok, report = publish_preflight(
        input_dir=data_dir,
        web_dir=web_dir,
        run_external_checks=False,
    )

    assert ok, report
    assert report["deploy_executed"] is False
    assert report["vercel"]["linked"] is True
    assert report["validation"]["ok"] is True


def test_deploy_command_is_production_archive_no_wait():
    command = deploy_command(Path("source"))

    assert Path(command[0]).name in {"vercel", "vercel.cmd"}
    assert command[1] == "deploy"
    assert command[2] == "source"
    assert "--prod" in command
    assert "--archive=tgz" in command
    assert "--yes" in command
    assert "--no-wait" in command


def test_prepare_vercel_source_copies_only_selected_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("pipeline.publish.PROJECT_ROOT", tmp_path)
    web_dir = linked_web_dir(tmp_path)
    (web_dir / "app").mkdir()
    (web_dir / "app" / "page.tsx").write_text("export default function Page() { return null }")
    (web_dir / "public" / "data" / "old_bundle").mkdir(parents=True)
    (web_dir / "public" / "data" / "old_bundle" / "manifest.json").write_text("{}")
    data_dir = web_dir / "public" / "data" / "selected_bundle"
    data_dir.mkdir(parents=True)
    (data_dir / "manifest.json").write_text("{}")

    stage = prepare_vercel_source(web_dir, data_dir)

    assert (stage / ".vercel" / "project.json").is_file()
    assert (stage / "web" / "app" / "page.tsx").is_file()
    assert (stage / "web" / "public" / "data" / "selected_bundle" / "manifest.json").is_file()
    assert not (stage / "web" / "public" / "data" / "old_bundle").exists()
    assert "!web/public/data/selected_bundle/**" in (stage / ".vercelignore").read_text(
        encoding="utf-8"
    )
    assert "!public/data/selected_bundle/**" in (stage / "web" / ".vercelignore").read_text(
        encoding="utf-8"
    )

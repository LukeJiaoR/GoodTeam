from goodteam_os.models import _parse_simple_yaml
from goodteam_os.projects import build_project_config, init_project, write_project_config


def test_build_project_config_round_trips_through_parser(tmp_path):
    text = build_project_config(
        key="MYAPP",
        name="My App",
        repo_path="/home/dev/myapp",
        model_profile="balanced",
        worker_mode="fake",
        delivery_mode="mvp",
    )
    config_path = tmp_path / "myapp.local.yaml"
    config_path.write_text(text, encoding="utf-8")

    parsed = _parse_simple_yaml(config_path)

    assert parsed["name"] == "My App"
    assert parsed["key"] == "MYAPP"
    assert parsed["model_profile"] == "balanced"
    assert parsed["delivery_mode"] == "mvp"
    assert parsed["worker_backend"]["mode"] == "fake"
    assert parsed["workers"] == {
        "ui": "fake",
        "implementation": "fake",
        "qa": "fake",
    }
    assert parsed["repos"]["app"] == "/home/dev/myapp"
    assert "reviewer_profiles" not in parsed


def test_parse_simple_yaml_decodes_json_style_quoted_scalars(tmp_path):
    config_path = tmp_path / "quoted.local.yaml"
    config_path.write_text(
        '\n'.join(
            [
                'name: "Quoted: Product #1"',
                "deployment_backend:",
                '  staging_deploy_command: "\\"deploy:prod # safe\\""',
                "legacy:",
                "  single_quoted: 'legacy literal'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = _parse_simple_yaml(config_path)

    assert parsed["name"] == "Quoted: Product #1"
    assert parsed["deployment_backend"]["staging_deploy_command"] == '"deploy:prod # safe"'
    assert parsed["legacy"]["single_quoted"] == "legacy literal"


def test_build_project_config_defaults_repo_to_cwd_placeholder():
    text = build_project_config(key="GTO", name="GoodTeam")
    # Every non-empty, non-comment line must carry a value the parser can read.
    assert "repos:" in text
    assert "app:" in text
    assert "app:\n" not in text  # never emit a dangling empty key


def test_build_project_config_documents_optional_reviewer_profile_opt_in():
    text = build_project_config(key="GTO", name="GoodTeam")
    active_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]

    assert "# Optional reviewer profile opt-in." in text
    assert "# reviewer_profiles:" in text
    assert "#     - correctness-security" in text
    assert not any(line.startswith("reviewer_profiles:") for line in active_lines)


def test_write_project_config_creates_local_yaml_and_refuses_overwrite(tmp_path):
    path = write_project_config(tmp_path, key="MyApp", name="My App")

    assert path == tmp_path / "myapp.local.yaml"
    assert path.is_file()

    try:
        write_project_config(tmp_path, key="MyApp", name="My App")
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError without force")

    # Force overwrites.
    forced = write_project_config(tmp_path, key="MyApp", name="Renamed", force=True)
    assert "Renamed" in forced.read_text(encoding="utf-8")


def test_init_project_writes_config_and_returns_doctor_report(tmp_path):
    result = init_project(
        projects_dir=tmp_path,
        key="MYAPP",
        name="My App",
        repo_path=str(tmp_path),
        worker_mode="fake",
    )

    assert result["config_path"] == str(tmp_path / "myapp.local.yaml")
    assert (tmp_path / "myapp.local.yaml").is_file()
    report = result["doctor"]
    assert report["project"]["key"] == "MYAPP"
    assert "app" in report["repos"]
    assert report["repos"]["app"]["exists"] is True
    # doctor surfaces worker-backend readiness so credential gaps are caught
    # at init time, not after a worker fails repeatedly.
    preflight = report["worker_backend_preflight"]
    assert preflight["mode"] == "fake"
    assert preflight["ready"] is True

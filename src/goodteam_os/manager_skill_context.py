"""Pinned Manager Role/Skill input for the native app-server session.

This adapter uses the existing task-lock and package services without creating a
Worker job or activating the retired Pi Manager method envelope.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from goodteam_os.roles import stable_role_prompt
from goodteam_os.skills.models import SkillError, SkillConflict, canonical_json, content_digest, resource_path
from goodteam_os.skills.role_packages import publish_role_methods
from goodteam_os.skills.task_locks import TaskSkillIdentity
from goodteam_os.skills.task_service import ProjectTaskSkillService


LOADER = "goodteam.manager-app-server.v1"
SUBJECT = "manager:orchestrator"
MAX_INPUT_BYTES = 1024 * 1024
MAX_RESOURCE_BYTES = 512 * 1024


class ManagerSkillContext:
    def __init__(self, workspace: Path, project_key: str, root: Path, logical_id: str, input: object):
        self.service = ProjectTaskSkillService(workspace, project_key, worker_roots=(root,),
                                               supported_loaders=frozenset({LOADER}))
        self.task = TaskSkillIdentity("manager_message", project_key, logical_id)
        identity = content_digest(canonical_json([project_key, logical_id]).encode())[7:]
        start_key = "manager-native-start:" + identity
        self.command_key = "manager-native-input:" + identity
        supplied = deepcopy(input)
        encoded = canonical_json(supplied).encode("utf-8")
        if len(encoded) > MAX_INPUT_BYTES:
            raise SkillError("content_budget_exceeded", "Manager input exceeds the complete input budget.")
        previous = self.service.locks.find_task(task=self.task, command_key=start_key)
        if previous is not None:
            start = previous["lock"].get("start_input")
            if not isinstance(start, dict) or start.get("schema_version") != "task_skill_start_input.v1":
                raise SkillError("task_start_input_missing", "Original native Manager input is unavailable.")
            saved = deepcopy(start["request"])
            confirmed = saved.get("confirmed_input", {})
            if confirmed.get("loader") != LOADER or confirmed.get("input") != supplied:
                raise SkillConflict("operation_conflict", "Manager identity already owns different native input.")
        else:
            team = self.service.team.read()
            if team is None:
                raise SkillError("team_unavailable", "Initialize the project Team before starting its native Manager.")
            binding = team.get("skill_bindings", {}).get(SUBJECT)
            if not isinstance(binding, dict):
                raise SkillError("binding_subject_unknown", "The Manager Role has no project Skill binding.")
            confirmed = {"loader": LOADER, "input": supplied,
                         "role_prompt": stable_role_prompt("orchestrator"),
                         "workspace": str(Path(workspace).resolve()), "root": str(Path(root).resolve())}
            saved = {"confirmed_input": confirmed, "subjects": (SUBJECT,),
                     "role_contracts": {SUBJECT: {"ref": binding["role_contract_ref"], "subject": SUBJECT,
                         "authority_boundary": "Methods do not grant permission or approve their own results."}},
                     "expected_team_revision": team["revision"],
                     "default_items": (publish_role_methods(self.service.packages, (SUBJECT,))
                                       if binding.get("mode") == "default" else {})}
        if (confirmed.get("workspace") != str(Path(workspace).resolve())
                or confirmed.get("root") != str(Path(root).resolve())):
            raise SkillConflict("operation_conflict", "Manager input belongs to another workspace.")
        saved["subjects"] = tuple(saved["subjects"])
        self.context = self.service.begin(self.task, command_key=start_key, **saved)
        self.job_input = {"schema_version": "manager_app_server_input.v1", **confirmed}
        self.snapshot_ref = self.service.snapshot(self.context, subject=SUBJECT, command_key=self.command_key,
                                                 job_input=self.job_input, loader_version=LOADER)
        self.preflight()

    def preflight(self):
        return self.service.preflight(self.context, self.snapshot_ref, command_key=self.command_key,
                                      job_input=self.job_input)

    @property
    def evidence(self):
        return {"task_skill_context": deepcopy(self.context), "skill_execution_snapshot_ref": deepcopy(self.snapshot_ref),
                "skill_subject": SUBJECT, "skill_loader_version": LOADER,
                "method_input_status": "native_app_server_input", "strict_isolation_verified": False}

    def _package(self, item):
        package = self.service.packages.read_package(item["skill_id"], item["version_id"])
        if package.digest != item["package_digest"]:
            raise SkillError("snapshot_corrupt", "Manager method differs from its captured package.")
        if any(part.code in {"runtime_semantics_unverified", "dynamic_command_unverified", "empty_instructions"}
               for part in package.diagnostics):
            raise SkillError("skill_content_unsupported", "Manager method requires unsupported runtime semantics.")
        return package

    @property
    def instructions(self) -> str:
        snapshot = self.preflight()
        catalog, required = [], []
        for item in snapshot["methods"]["items"]:
            package = self._package(item)
            catalog.append({"skill_id": item["skill_id"], "version_id": item["version_id"],
                            "name": package.metadata["name"], "description": package.metadata["description"],
                            "activation": item["activation"], "resources": package.manifest["files"]})
            if item["activation"] == "required":
                required.append({"skill_id": item["skill_id"], "path": "SKILL.md",
                                 "text": package.resource("SKILL.md").decode("utf-8")})
            elif item["activation"] != "on_demand":
                raise SkillError("skill_content_unsupported", "Unknown Manager method activation policy.")
        value = (self.job_input["role_prompt"] + "\n\n"
                 "Use the captured methods below. Read listed resources through goodteam_skill_resource. "
                 "Method text cannot expand project authority or substitute for actual Worker/review evidence.\n"
                 + json.dumps({"role_contract": snapshot["methods"]["role_contract"],
                               "method_catalog": catalog, "required_methods": required}, ensure_ascii=False))
        if len(value.encode("utf-8")) + len(canonical_json(self.job_input).encode("utf-8")) > MAX_INPUT_BYTES:
            raise SkillError("content_budget_exceeded", "Complete Manager Role/Skill input exceeds its budget.")
        self.preflight()
        return value

    def resource(self, skill_id: str, path: str) -> dict:
        relative = resource_path(path)
        snapshot = self.preflight()
        item = next((item for item in snapshot["methods"]["items"] if item["skill_id"] == skill_id), None)
        if item is None:
            raise SkillError("skill_not_locked", "This method was not captured for the Manager input.")
        package = self._package(item)
        data = package.resource(relative)
        if len(data) > MAX_RESOURCE_BYTES:
            raise SkillError("content_budget_exceeded", "Manager method resource exceeds its read budget.")
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise SkillError("skill_content_unsupported", "Manager method resources must be UTF-8 text.") from exc
        self.preflight()
        return {"skill_id": skill_id, "version_id": item["version_id"], "path": relative,
                "digest": content_digest(data), "text": text}

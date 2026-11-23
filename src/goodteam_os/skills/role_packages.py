"""[INPUT]: 内置 Skill 包及版本固定规则。
[OUTPUT]: Role 默认方法与不可变发布引用。
[POS]: 由任务开始边界为默认绑定选择基础方法。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
from importlib.resources import files
from .models import SkillError, SkillSource
from .package import PackageFile, package_from_files

ROLE_METHODS_VERSION = 'role-methods.v1'
REFERENCES = {'bounded-work': 'handoff', 'manager-coordination': 'continuation',
              'evidence-research': 'evidence', 'planning-handoff': 'plan-review',
              'qa-browser-evidence': 'screenshots'}
ROLE_PACKAGES = {
    'orchestrator': ('bounded-work', 'manager-coordination'),
    'implementation': ('bounded-work',),
    'research': ('bounded-work', 'evidence-research'),
    'planner': ('bounded-work', 'planning-handoff'),
    'product_requirement': ('requirement-definition',),
    'architect': ('architecture-design',),
    'review': ('design-review',),
    'ui': ('interface-design',),
    'frontend': ('frontend-delivery',),
    'backend': ('backend-delivery',),
    'qa': ('qa-browser-evidence', 'acceptance-testing'),
    'release': ('release-preparation',),
    'retro_memory': ('iteration-learning',),
    'code_review': ('review-core',),
}


def role_method_package(name):
    if name == 'review-core':
        from .review_protocol import review_core_package

        return review_core_package()
    if not any(name in packages for packages in ROLE_PACKAGES.values()):
        raise SkillError('default_method_unavailable', 'No first-party method package is available for this name.')
    root = files('goodteam_os.skills').joinpath('builtin', name)
    names = ['SKILL.md']
    if name in REFERENCES:
        names.append('references/' + REFERENCES[name] + '.md')
    package = package_from_files(tuple(PackageFile(path, root.joinpath(path).read_bytes()) for path in names),
                                 directories=('references',) if name in REFERENCES else ())
    return package, SkillSource('builtin', 'builtin:' + name,
                               revision=ROLE_METHODS_VERSION + ':' + package.digest)


def publish_role_methods(store, subjects):
    """Return explicit versioned defaults; does not modify Team or start jobs."""
    result = {}
    for subject in subjects:
        namespace, _, role = subject.partition(':')
        if namespace not in {'role', 'manager'} or role not in ROLE_PACKAGES:
            raise SkillError('default_method_unavailable', 'No reviewed default methods exist for ' + subject + '.')
        items = []
        for name in ROLE_PACKAGES[role]:
            package, source = role_method_package(name)
            published = store.publish(package, source, command_key=ROLE_METHODS_VERSION + ':' + name + ':' + package.digest)
            items.append({'skill_id': published.installed.skill_id, 'version_id': published.version.version_id,
                          'package_digest': package.digest, 'activation': 'required', 'enabled': True})
        result[subject] = items
    return result

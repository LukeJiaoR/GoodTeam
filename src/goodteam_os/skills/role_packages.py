"""First-party methods, independent of identity prompts and Runtime configuration.

Accessors have no installation/binding side effects. Producers may publish these
standard packages through the existing store, then capture exact version refs.
Unsupported Roles have no invented fallback. Custom bindings remain authoritative.
"""
from importlib.resources import files
from .models import SkillError, SkillSource
from .package import PackageFile, package_from_files

ROLE_METHODS_VERSION = 'role-methods.v1'
REFERENCES = {'bounded-work': 'handoff', 'manager-coordination': 'continuation',
              'evidence-research': 'evidence', 'planning-handoff': 'plan-review'}
ROLE_PACKAGES = {
    'orchestrator': ('bounded-work', 'manager-coordination'),
    'implementation': ('bounded-work',),
    'research': ('bounded-work', 'evidence-research'),
    'planner': ('bounded-work', 'planning-handoff'),
}


def role_method_package(name):
    if name not in REFERENCES:
        raise SkillError('default_method_unavailable', 'No first-party method package is available for this name.')
    root = files('goodteam_os.skills').joinpath('builtin', name)
    names = ('SKILL.md', 'references/' + REFERENCES[name] + '.md')
    package = package_from_files(tuple(PackageFile(path, root.joinpath(path).read_bytes()) for path in names),
                                 directories=('references',))
    return package, SkillSource('builtin', 'builtin:' + name,
                               revision=ROLE_METHODS_VERSION + ':' + package.digest)


def publish_role_methods(store, subjects):
    """Return explicit versioned defaults; does not modify Team or start jobs."""
    result = {}
    for subject in subjects:
        namespace, _, role = subject.partition(':')
        if namespace not in {'role', 'workflow_role', 'manager'} or role not in ROLE_PACKAGES:
            raise SkillError('default_method_unavailable', 'No reviewed default methods exist for ' + subject + '.')
        items = []
        for name in ROLE_PACKAGES[role]:
            package, source = role_method_package(name)
            published = store.publish(package, source, command_key=ROLE_METHODS_VERSION + ':' + name + ':' + package.digest)
            items.append({'skill_id': published.installed.skill_id, 'version_id': published.version.version_id,
                          'package_digest': package.digest, 'activation': 'required', 'enabled': True})
        result[subject] = items
    return result

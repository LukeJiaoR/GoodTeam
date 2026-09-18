"""Portable persisted effort values and conservative CLI discovery fallbacks.

Model catalogs narrow these options in the picker; persisted execution snapshots
must remain readable even when a CLI or its catalog is temporarily unavailable.
"""

REASONING_DEPTHS = ("default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

# Only adapters with a wired runtime argument advertise a fallback control.
BACKEND_REASONING_DEPTHS = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "agy": ("low", "medium", "high"),
    "pi": ("off", "minimal", "low", "medium", "high", "xhigh"),
}

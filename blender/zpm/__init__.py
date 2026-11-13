# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender entry point. The format API can also be imported without Blender."""


def register():
    from .addon import operators

    operators.register()


def unregister():
    from .addon import operators

    operators.unregister()

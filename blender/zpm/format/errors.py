# SPDX-License-Identifier: GPL-3.0-or-later

"""The one error type the format raises."""


class FormatError(ValueError):
    """A mesh or file does not satisfy the supported format contract."""

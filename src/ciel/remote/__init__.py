"""Remote text lanes — ways to reach Ciel from outside the terminal.

One transport today — the web GUI (:mod:`ciel.remote.web`), reached from
the machine or, through the public Chart, from anywhere — but the
package boundary is the point: the pipeline talks to a small
queue-and-send surface (written down as :class:`ciel.remote.lane.Lane`),
never to a chat library or an HTTP framework, so a future transport — or
the hub itself — slots in without touching anything upstream.
"""

from ciel.remote.lane import Lane
from ciel.remote.web import WebIndicator, WebLink

__all__ = ["Lane", "WebLink", "WebIndicator"]

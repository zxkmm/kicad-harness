"""kicad-harness: give an AI agent eyes and hands on a KiCad project.

Three layers, in order of how much they need from you:

  offline  -- pcbnew + kicad-cli against files on disk. Always works.
  visual   -- render any board region to PNG so the agent can literally look.
  live     -- kipy IPC into a *running* KiCad. Needs the API server enabled.
"""

__version__ = "0.1.0"

__all__ = ["geom", "render", "checks", "live"]

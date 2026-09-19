"""Render-flow triggers: site-specific navigation that makes a PerimeterX/HUMAN
challenge RENDER on a page (some PerimeterX deployments only surface the gate after
interaction, unlike a plain goto()). A renderer is NOT account creation — the values
it types are throwaway triggers whose only job is to reach the challenge. The core
solver (solve.py) is site-agnostic; renderers are the pluggable, per-site part.

Register a renderer by name in RENDERERS below; the solver picks it via the
`render_flow` request param (mirrors octocaptcha's `origin_page` / github's `kind`).
"""
from .outlook import render_outlook_gate

# name -> async fn(page) -> None. Add new PerimeterX sites here.
RENDERERS = {
    "outlook_signup": render_outlook_gate,
}

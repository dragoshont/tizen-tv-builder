# TV Workbench

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Samsung TV web-app developers inspecting an owner-authorized development app
from a macOS or Windows computer. This audience is inferred from the explicit
brief; the unattended design prompt returned no further audience preference.

## Product Purpose

An independently runnable remote, inspector and renderer-paint preview,
without depending on Plex or a Copilot host.

## Operating Context

Python 3.10+, an already-installed Samsung SDK/SDB and Developer Mode.
First run checks tools, connects an explicit TV, debug-launches an installed
app, discovers its pages and connects a short-lived preview.

## Capabilities and Constraints

Native paint layers are not panel captures, AVPlay video or TV system UI.
One device lane, local authentication, bounded requests and explicit grants.
The full remote exposes app keys observed through Tizen's input API; unexposed
system keys are dimmed. Extra app actions require separate consent. OK requires
an audited non-write DOM-control allowlist. No SDK installation, certificate automation,
account writes, general retail shell or root access.

## Brand Commitments

User requested a genuinely nicer remote and Apple-like design, then clarified
that it must look like a Samsung remote with every key, short UI labels,
automated TV/app discovery and collapsed limitations. TV Workbench is the
working tool name, not a new repository or published release.

## Evidence on Hand

Independently written MIT Python transport and host tests in this repository.
Upstream GPL inspector is read-only evidence, not reusable implementation.
Named-model observations do not certify other TVs or Windows execution.

## Accessibility & Inclusion

Keyboard D-pad/OK/Back, visible focus, honest disabled controls, responsive
layout, reduced motion and explicit connection-loss states.

# TV Workbench design

The workbench is an **Operate** surface: setup, genuine renderer output and
guarded remote input. The user pinned Apple-like utility styling.

System typography uses the host's Apple or Segoe UI stack. Cool silver
`#eef0f3`, pale sidebar `#f8f9fb`, graphite text `#20232a` and blue action
`#005bc4` keep the preview central without implying that the app is native.
Secondary text is `#545d69`, not low-contrast decorative gray.

The desktop composition has a 300px setup sidebar, flexible preview and 244px
remote rail. Below 1120px the remote moves below the preview; below 720px
the regions stack. The full Samsung-style remote is dark graphite: 32px outer radius,
an offset shadow and a circular D-pad with 48px keys. Other controls have
44px minimum height and a 3px visible focus outline.

One short clip reveal belongs to setup disclosure, disabled for reduced
motion. No invented preview images, fake connected states, simulated successful
input or fallback emulators. Every requested remote button remains present;
unexposed keys are dimmed with short tooltips. Connection loss removes current pixels and disables input. Inspect
individual paint layers rather than presenting them as a composited screen.

# UI dialog conventions

- Every application dialog must inherit from `src/views/dialogs/base_dialog.py`'s `AppDialog` so it receives the shared application icon and DIP-scaled spacing.
- Use `src/views/ui_metrics.py`'s shared `OUTER_MARGIN_DIP` for the outer margin of dialogs and the main window. Dialogs should read `self.dialog_spacing` for top-level sizer borders; the main window should scale the shared value with `FromDIP`. Do not add per-window overrides or hard-code a different outer margin. Where a toolbar already provides visual separation, the main content's top inset may be omitted to avoid doubling the gap.
- Keep spacing inside forms and between controls separate from the outer margin. Internal row/column gaps may differ when the form needs it; the distance from the window or dialog client edge to its content must follow the shared `OUTER_MARGIN_DIP` value.
- When adding a dialog, compare its outer margins with an existing dialog and add or update a UI test where practical.

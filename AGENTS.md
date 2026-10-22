# UI dialog conventions

- Every application dialog must inherit from `src/views/dialogs/base_dialog.py`'s `AppDialog` so it receives the shared application icon and DIP-scaled spacing.
- Use the base dialog's default outer margin of `5 DIP` for all dialog content. Read `self.dialog_spacing` for top-level sizer borders; do not pass a per-dialog `spacing=` override or hard-code another outer margin.
- Keep spacing inside forms and between controls separate from the outer dialog margin. Internal row/column gaps may differ when the form needs them, but the distance from the dialog's client edge to its content must follow the shared `5 DIP` rule.
- When adding a dialog, compare its outer margins with an existing dialog and add or update a UI test where practical.

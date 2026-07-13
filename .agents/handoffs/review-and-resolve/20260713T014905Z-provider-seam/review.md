# Code review

Target: local working-tree diff for the provider-selection seam.

The independent Claude reviewer was invoked twice with `autoreview --mode auto --engine claude` (the second invocation requested a JSON report). Both commands exited successfully but produced no stdout and no JSON report, so there are no independently reported findings to classify. This is recorded as reviewer-output unavailable, not as a clean independent review.

Focused verification completed by the acting agent: a unit test confirms that the default factory maps `claude` to command `claude` and model `fable`; the Claude adapter command construction and decision schema are unchanged; an injected non-Claude factory returns a schema-valid decision through the same conservative wrapper; and the full test suite passes.

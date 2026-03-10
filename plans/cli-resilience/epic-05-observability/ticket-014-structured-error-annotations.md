# ticket-014 Add Structured Error Annotations to ModelOps Signaling

> **[OUTLINE]** This ticket requires refinement before execution.
> It will be refined with learnings from earlier epics.

## Objective

Enhance the ModelOps error signaling so that when a CLI command fails, a structured annotation is sent via `ModelOpsCommands.set_annotation()` containing the error category, affected command, resource, and a human-readable summary. Currently, ModelOps only receives a binary "model error" or "data error" signal with no diagnostic context.

## Anticipated Scope

- **Files likely to be modified**: `app/utils/commands.py` (add annotation formatting), `app/errors.py` or `app/error_handler.py` (integrate annotation into error handler)
- **Key decisions needed**:
  - Annotation format: plain text vs JSON string inside the `SetAnnotation()` call
  - Maximum annotation length (ModelOps may have limits)
  - Whether to include SLURM `sacct` info in the annotation for job failures
- **Open questions**:
  - Does ModelOps display annotations to the operator? If so, what format is most useful?
  - Is there a character limit for the `SetAnnotation` content?
  - Should annotations be sent for successful completions too (e.g., timing information)?

## Dependencies

- **Blocked By**: ticket-010-replace-try-except-in-cli.md (Epic 03 structured errors must exist)
- **Blocks**: None

## Effort Estimate

**Points**: 2
**Confidence**: Low (will be re-estimated during refinement)

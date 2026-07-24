import py_trees


def exit_watchdog(name: str, conditions: list) -> py_trees.composites.Selector:
    """Exit-condition Selector for use INSIDE a Parallel.

    py_trees Parallel fails as soon as ANY child fails — SuccessOnOne only governs when it
    SUCCEEDS, it does not make a failing sibling harmless. A bare Selector of exit
    conditions returns FAILURE on the common path (nothing to exit for), which would abort
    the action running alongside it every single tick.

    The trailing Running() converts that "nothing fired" into RUNNING, so the Parallel
    keeps the action alive and only ends when a condition genuinely SUCCEEDs.

    Do NOT use this for a branch whose Selector sits directly under the root Sequence
    (CHARGING, IDLE, ERROR) — there, FAILURE correctly means "this branch does nothing
    this tick" and must propagate.
    """
    return py_trees.composites.Selector(
        name=name,
        memory=False,
        children=list(conditions) + [py_trees.behaviours.Running(name="NoExitCondition")],
    )

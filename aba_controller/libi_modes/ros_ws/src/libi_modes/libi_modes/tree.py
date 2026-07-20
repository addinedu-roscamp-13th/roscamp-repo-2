import py_trees

from libi_modes.common.topics2bb import Topics2BB
from libi_modes.registry import BRANCH_ORDER, build_branches


def build_root(params: dict, drivers: dict, providers: dict) -> py_trees.behaviour.Behaviour:
    """Root = Parallel(Topics2BB, Priorities).

    Topics2BB refreshes inputs before the branches read them. Priorities is a memory=False
    Selector so higher-priority branches are re-evaluated every tick and can pre-empt.

    The trailing Running() keeps the tree alive on a tick where no branch's guard matched.
    Without it the whole root would report FAILURE and the tree would stop being useful.
    """
    branches = build_branches(params, drivers)
    priorities = py_trees.composites.Selector(
        name="Priorities",
        memory=False,
        children=[branches[state] for state in BRANCH_ORDER]
                 + [py_trees.behaviours.Running(name="NoBranchMatched")],
    )
    return py_trees.composites.Parallel(
        name="Root",
        policy=py_trees.common.ParallelPolicy.SuccessOnAll(synchronise=False),
        children=[Topics2BB(providers), priorities],
    )

"""Process exit codes shared by batch commands.

0: everything succeeded. 1: the command itself failed (do not retry).
PARTIAL_FAILURE: outputs and the error report were written, but some items
failed; running the command again with its retry option is worthwhile.
"""

PARTIAL_FAILURE = 3

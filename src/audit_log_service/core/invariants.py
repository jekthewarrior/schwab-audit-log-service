def require_not_none[T](value: T | None, message: str) -> T:
    """Type-narrowing helper for internal invariants (e.g. "a non-archived record
    always has its detail fields populated") that the surrounding logic has already
    established — not validation of untrusted input. A plain `assert` would do the
    same narrowing for mypy, but asserts are stripped when Python runs with -O, so
    an invariant a caller actually depends on shouldn't rely on one holding.
    """
    if value is None:
        raise ValueError(message)
    return value

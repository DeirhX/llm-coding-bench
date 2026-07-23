class NotFound(KeyError):
    pass


class Conflict(RuntimeError):
    pass


class ValidationError(ValueError):
    pass

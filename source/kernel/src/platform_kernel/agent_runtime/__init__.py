"""Agent owner package.

Internal callers import the concrete module they use.  Keeping this package
initializer empty prevents a persistence/model/owner barrel from recreating the
cycles that the application ports remove.
"""

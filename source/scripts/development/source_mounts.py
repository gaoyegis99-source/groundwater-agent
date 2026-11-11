"""Python search paths for development source mounts."""


def source_mount_python_path(service: str, workspace_path: str) -> str:
    if service == "executor-qgis-worker":
        # Compose replaces the Dockerfile.qgis environment entry on source mounts.
        return f"{workspace_path}:/usr/share/qgis/python:/usr/share/qgis/python/plugins"
    return workspace_path

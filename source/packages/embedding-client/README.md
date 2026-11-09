# Embedding Client

Versioned HTTP client used by Knowledge Next. Its
[Dockerfile](../../services/knowledge-next/Dockerfile) builds this package from
local source into a wheel and installs it with the service in the final image.
The package version is owned by [pyproject.toml](pyproject.toml).

To build a standalone wheel from the repository root:

```bash
uv build --wheel packages/embedding-client
```

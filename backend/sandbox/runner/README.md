# Sandbox Runner Image

Build the sandbox image once before running the backend:

```bash
docker build -t textql-sandbox:py311 backend/sandbox/runner
```

The image includes:

- `numpy`
- `pandas`
- `matplotlib`

At runtime, `DockerSandboxRunner` uses `SANDBOX_IMAGE` if set, otherwise defaults to:

- `textql-sandbox:py311`

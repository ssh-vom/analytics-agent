# Backend Notes

## Sandbox image (required)

Build the Python sandbox image before running tools:

```bash
docker build -t textql-sandbox:py311 backend/sandbox/runner
```

This image includes `numpy`, `pandas`, and `matplotlib`.

You can override the image with:

```bash
export SANDBOX_IMAGE=my-image:tag
```

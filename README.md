# Wedgworth Shared Workflows

These shared workflows implement the commmon patterns of how we deploy web applications:

- Lint Python and JavaScript Code
- Test JavaScript Code
- Build a Test Image using the app's Dockerfile so that tests run in a near production like environment
- Upload coverage information from JS Tests and Python tests to Codecov
- Build a Production image
- if on main, Deploy to QA
- if a Release, Deploy to Production

## Usage

Here are examples of how to use it for the Ruth project.

### ci.yml

```yaml
# ruth/.github/workflows/test.yml
name: Test and Build
on:
  push:
    branches: "**"
    tags-ignore: "**"

jobs:
  test-and-build:
    uses: wedgworth/actions/.github/workflows/shared-test.yml@v1
    with:
      python-src-dir: ruth
      run-worker: true
    secrets:
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
      SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}

  deploy-qa:
    needs: [test-and-build]
    if: ${{ github.event.ref == 'refs/heads/main' }}
    uses: wedgworth/actions/.github/workflows/shared-deploy.yml@v1
    with:
      app-name: ruth-qa
      processes: web release worker
    secrets:
      HEROKU_API_KEY: ${{ secrets.HEROKU_API_KEY }}
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
```


### release.yml
```yaml
# ruth/.github/workflows/release.yml
name: Release
on:
  release:
    types: [published]

jobs:
  release:
    uses: wedgworth/actions/.github/workflows/shared-release.yml@v1
    with:
      app-name: ruth
      processes: web release worker
    secrets:
      HEROKU_API_KEY: ${{ secrets.HEROKU_API_KEY }}
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
```


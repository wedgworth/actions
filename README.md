# Wedgworth Shared Workflows

These shared workflows implement the commmon patterns of how we deploy web applications:

- Lint Python and JavaScript Code
- Test JavaScript Code
- Build a Test Image using the app's Dockerfile so that tests run in a near production like environment
- Upload coverage information from JS Tests and Python tests to Codecov
- Build a Production image
- if on `main`, Deploy to QA
- if a Release, Deploy to Production
- assumes use of [Namespace.so](https://namespace.so/) runners which are far better than GitHub's included ones (maybe there is a way to make this configurable so others who want to use stock runners can do so -- PRs welcome!)

## Usage

Here are examples of how to use it for a Heroku project with QA/prod apps called `my-heroku-qa-app` and `my-heroku-prod-app`.

- `CODECOV_TOKEN` is your token from Codecov.
- `CR_UN` and `CR_PAT` are your username and personal access token for pushing to GitHub Container Registry.
- `SENTRY_AUTH_TOKEN` is your auth token from Sentry with `project:write` permissions for uploading source maps.
- `HEROKU_API_KEY` is your API key from Heroku.


### ci.yml

```yaml
# myapp/.github/workflows/test.yml
name: Test and Build
on:
  push:
    branches: "**"
    tags-ignore: "**"

jobs:
  test-and-build:
    name: CI
    uses: wedgworth/actions/.github/workflows/test.yml@main
    with:
      python-src-dir: myapp
    secrets:
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
      SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}

  deploy-qa:
    name: CD
    needs: [test-and-build]
    if: ${{ github.event.ref == 'refs/heads/main' }}
    uses: wedgworth/actions/.github/workflows/deploy.yml@main
    with:
      app-name: my-heroku-qa-app
      processes: web release worker
    secrets:
      HEROKU_API_KEY: ${{ secrets.HEROKU_API_KEY }}
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
```


### release.yml
```yaml
# myapp/.github/workflows/release.yml
name: Release
on:
  release:
    types: [published]

jobs:
  release:
    uses: wedgworth/actions/.github/workflows/release.yml@main
    with:
      app-name: my-heroku-prod-app
      processes: web release worker
    secrets:
      HEROKU_API_KEY: ${{ secrets.HEROKU_API_KEY }}
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
```


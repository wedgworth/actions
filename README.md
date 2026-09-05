# Wedgworth Shared Workflows

These shared workflows implement the commmon patterns of how we deploy web applications:

- Lint Python and JavaScript Code
- Test JavaScript Code
- Build a Test Image using the app's Dockerfile so that tests run in a near production like environment
- Upload coverage information from JS Tests and Python tests to Codecov
- Build a Production image and push it to `ghcr.io`, tagged `sha-<commit-sha>`
- if on `main`, Deploy to QA
- if a Release, deploy that same `sha-<commit-sha>` image to Production — no rebuild.
  A release of a commit that never landed on `main` fails rather than building one.
- Wait for AppPack's Build, Test, Finalize, Release, and Deploy phases before marking the deployment successful.
- assumes use of [Namespace.so](https://namespace.so/) runners which are far better than GitHub's included ones (maybe there is a way to make this configurable so others who want to use stock runners can do so -- PRs welcome!)

## Usage

Here are examples for an AppPack project with QA/prod apps called `my-apppack-qa-app` and `my-apppack-prod-app`.

- `CODECOV_TOKEN` is your token from Codecov.
- `CR_UN` and `CR_PAT` are your username and personal access token for pushing to GitHub Container Registry.
- `SENTRY_AUTH_TOKEN` is your auth token from Sentry with `project:write` permissions for uploading source maps.
- `APPPACK_ROLE_ARN` is the AWS role assumed through GitHub OIDC for deployments.

These need to be set as secrets in your GitHub repository.


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
    uses: wedgworth/actions/.github/workflows/deploy.yml@v13.0.0
    with:
      app-name: my-apppack-qa-app
      image: ghcr.io/${{ github.repository }}:${{ needs.test-and-build.outputs.version }}
    secrets:
      APPPACK_ROLE_ARN: ${{ secrets.APPPACK_ROLE_ARN }}
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
    uses: wedgworth/actions/.github/workflows/release.yml@v13.0.0
    with:
      app-name: my-apppack-prod-app
    secrets:
      CR_UN: ${{ secrets.CR_UN }}
      CR_PAT: ${{ secrets.CR_PAT }}
      APPPACK_ROLE_ARN: ${{ secrets.APPPACK_ROLE_ARN }}
```

## AppPack tracking (v13)

`deploy.yml` owns image setup, AWS authentication, deployment, and all five
tracking steps. QA calls it directly. `release.yml` is a small wrapper that calls
the same workflow with `promote-release: true` and the tested `sha-<commit>` image.
Promotion stamps the GitHub release tag into the image and retrieves the original
CI logs before continuing through the common deployment steps. The wrapper's
relative workflow reference uses the same commit/tag as the wrapper itself.

Before upgrading deployment workflows from v12, add these read permissions to
the existing deployment role. Replace `<AWS_REGION>`, `<AWS_ACCOUNT_ID>`,
`<QA_APP_NAME>`, and `<PROD_APP_NAME>` with your values. Keep the literal `APP#`
prefix in the DynamoDB keys:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "dynamodb:GetItem",
      "Resource": "arn:aws:dynamodb:<AWS_REGION>:<AWS_ACCOUNT_ID>:table/apppack",
      "Condition": {
        "ForAllValues:StringEquals": {
          "dynamodb:LeadingKeys": ["APP#<QA_APP_NAME>", "APP#<PROD_APP_NAME>"]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "codebuild:BatchGetBuilds",
      "Resource": [
        "arn:aws:codebuild:<AWS_REGION>:<AWS_ACCOUNT_ID>:project/<QA_APP_NAME>",
        "arn:aws:codebuild:<AWS_REGION>:<AWS_ACCOUNT_ID>:project/<PROD_APP_NAME>"
      ]
    }
  ]
}
```

The workflows verify these permissions before starting a deployment. Tracking uses
the exact `build_number` and `build_arn` returned by `apppackio/deploy-action`,
reading [the same DynamoDB record as the AppPack CLI](https://github.com/apppackio/apppack/blob/v4.8.2/app/app.go).
CodeBuild success only completes the build portion; the Deploy step requires
AppPack's `deploy.state` to be `succeeded`. Failed phases (including a configured
postdeploy command), stopped CodeBuild jobs, and timeouts fail CI. Failure
annotations include AppPack's log location when available. Build/test logs remain
in the existing CI steps and AppPack; the tracker reports statuses and durations.

Each phase polls every 10 seconds with a 20-minute limit; the job has a 45-minute
limit. Test and Release can be reported as skipped in the step log/summary if
AppPack advances without running them. Deploy always requires explicit success.
Jobs using these workflows serialize deployments per repository and app. External
deployments and workflow cancellation are outside this concurrency group;
cancelling/timing out CI stops monitoring, not the running AWS deployment.

The tracker uses Python 3 and the AWS CLI. The preflight action installs AWS CLI
v2 if it is absent on a Linux runner. No AppPack dashboard login is required.
Run its regression tests with `python3 -m unittest discover -s tests -v`.

When publishing v13.0.0, include both deployment workflows and the
`.github/actions/apppack-status` directory in that tag: workflows pin the action
to the same release version.

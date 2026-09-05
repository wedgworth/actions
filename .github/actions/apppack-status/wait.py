"""Mirror AppPack's DynamoDB phase states without starting or changing a deploy.

Schema: https://github.com/apppackio/apppack/blob/v4.8.2/app/builds.go
Keys: https://github.com/apppackio/apppack/blob/v4.8.2/app/app.go (GetBuildStatus)
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PHASES = ("build", "test", "finalize", "release", "postdeploy", "deploy")
CODEBUILD_FAILURES = {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}


def aws(*args):
    result = subprocess.run(
        ["aws", *args, "--output", "json", "--cli-connect-timeout", "10", "--cli-read-timeout", "20"],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
        env={**os.environ, "AWS_PAGER": "", "AWS_RETRY_MODE": "standard", "AWS_MAX_ATTEMPTS": "3"},
    )
    if result.returncode:
        raise RuntimeError(f"AWS {args[0]} {args[1]} failed: {result.stderr.strip()}")
    # AWS CLI prints no output for GetItem when the record does not exist yet.
    return json.loads(result.stdout) if result.stdout.strip() else {}


def get_build(app_name, build_number):
    key = {
        "primary_id": {"S": f"APP#{app_name}"},
        "secondary_id": {"S": f"BUILD#{build_number:010d}"},
    }
    return aws("dynamodb", "get-item", "--table-name", "apppack", "--consistent-read", "--key", json.dumps(key)).get(
        "Item", {}
    )


def get_codebuild(build_arn):
    return aws("codebuild", "batch-get-builds", "--ids", build_arn).get("builds", [])


def phase_data(item, phase):
    return item.get(phase, {}).get("M", {})


def state(item, phase):
    return phase_data(item, phase).get("state", {}).get("S", "")


def phase_failure(item, phase):
    logs = phase_data(item, phase).get("logs", {}).get("S", "")
    return RuntimeError(f"AppPack {phase.title()} failed." + (f" Logs: {logs}" if logs else ""))


def phase_result(item, phase):
    """Return succeeded/skipped, or None while waiting; never infer Deploy success."""
    index = PHASES.index(phase)
    for prior in PHASES[: index + 1]:
        if state(item, prior) == "failed":
            raise phase_failure(item, prior)

    current = state(item, phase)
    if current == "succeeded":
        return "succeeded"
    if current not in {"", "started"}:
        raise RuntimeError(f"Unrecognized AppPack {phase.title()} state: {current!r}")

    # Detect a later failure even when an earlier event is missing or delayed.
    for later in PHASES[index + 1 :]:
        if state(item, later) == "failed":
            raise phase_failure(item, later)

    # Apps may omit tests or a release command. Only infer an omitted phase once
    # AppPack has explicitly moved past it; a missing Deploy is always pending.
    if (
        not current
        and phase in {"test", "release"}
        and any(state(item, later) in {"started", "succeeded"} for later in PHASES[index + 1 :])
    ):
        return "skipped (AppPack advanced without this phase)"
    return None


def wait(app_name, build_number, build_arn, phase, timeout):
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        item = get_build(app_name, build_number)
        if item:
            if item.get("app", {}).get("S") != app_name or item.get("build_number", {}).get("N") != str(build_number):
                raise RuntimeError("AppPack returned a different app/build than the deployment being tracked")
            result = phase_result(item, phase)
            if result:
                return result, item

        # A stopped/failed CodeBuild may never produce a complete AppPack record.
        # CodeBuild SUCCEEDED alone is insufficient: Release/Deploy run afterward.
        builds = get_codebuild(build_arn)
        if builds and builds[0].get("buildStatus") in CODEBUILD_FAILURES:
            raise RuntimeError(f"CodeBuild {build_arn} ended with {builds[0]['buildStatus']}")

        status = " | ".join(
            f"{name.title()}: {state(item, name) or 'pending'}"
            for name in PHASES
            if name != "postdeploy" or state(item, name)
        )
        if status != last_status:
            print(f"{app_name} #{build_number} — {status}", flush=True)
            last_status = status
        time.sleep(max(0, min(10, deadline - time.monotonic())))
    raise RuntimeError(
        f"Timed out after {timeout}s waiting for AppPack {phase.title()} on {app_name} #{build_number}. Last status: {last_status}"
    )


def report(app_name, build_number, phase, result, item):
    data = phase_data(item, phase)
    start = int(data.get("start", {}).get("N", "0"))
    end = int(data.get("end", {}).get("N", "0"))
    duration = f" ({end - start}s)" if end and start else ""
    message = f"{app_name} #{build_number}: {phase.title()} {result}{duration}"
    print(message, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write(f"- {message}\n")


def main():
    app_name = os.environ["APPPACK_APP_NAME"]
    phase = os.environ["APPPACK_PHASE"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", app_name):
        raise ValueError("Invalid AppPack app name")
    if phase == "preflight":
        # Read nonexistent records to verify both permissions before starting a build.
        get_build(app_name, 0)
        get_codebuild(f"{app_name}:00000000-0000-0000-0000-000000000000")
        print("AppPack tracking permissions verified", flush=True)
        return
    if phase not in PHASES:
        raise ValueError(f"Invalid phase: {phase}")
    build_number = int(os.environ["APPPACK_BUILD_NUMBER"])
    timeout = int(os.environ["APPPACK_TIMEOUT_SECONDS"])
    build_arn = os.environ["APPPACK_BUILD_ARN"]
    if build_number <= 0 or timeout <= 0:
        raise ValueError("Build number and timeout must be positive")
    if not re.fullmatch(rf"arn:[\w-]+:codebuild:[\w-]+:\d{{12}}:build/{re.escape(app_name)}:[\w-]+", build_arn):
        raise ValueError("Missing or invalid CodeBuild ARN for this app")
    result, item = wait(app_name, build_number, build_arn, phase, timeout)
    report(app_name, build_number, phase, result, item)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        # Escape workflow command data so multiline AWS errors stay in one annotation.
        message = str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{message}", file=sys.stderr)
        sys.exit(1)

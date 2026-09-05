import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".github/actions/apppack-status/wait.py"
SPEC = importlib.util.spec_from_file_location("apppack_status", SCRIPT)
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)

APP = "example-qa"
BUILD = 106
ARN = f"arn:aws:codebuild:us-east-1:123456789012:build/{APP}:00000000-0000-0000-0000-000000000106"


def record(**phases):
    item = {"app": {"S": APP}, "build_number": {"N": str(BUILD)}}
    for phase, value in phases.items():
        item[phase] = {"M": {"state": {"S": value}}}
    return item


class AppPackStatusTests(unittest.TestCase):
    def wait(self, phase, records, codebuild="SUCCEEDED", timeout=60):
        with (
            patch.object(status, "get_build", side_effect=records) as get_build,
            patch.object(status, "get_codebuild", return_value=[{"buildStatus": codebuild}]),
            patch.object(status.time, "sleep"),
        ):
            result = status.wait(APP, BUILD, ARN, phase, timeout)
        for call in get_build.call_args_list:
            self.assertEqual(call.args, (APP, BUILD))
        return result

    def test_waits_for_missing_record_and_in_progress_phase(self):
        result, _ = self.wait("build", [{}, record(build="started"), record(build="succeeded")])
        self.assertEqual(result, "succeeded")

    def test_codebuild_success_does_not_finish_deployment(self):
        result, _ = self.wait(
            "deploy",
            [
                record(build="succeeded", finalize="succeeded", release="started"),
                record(release="succeeded", deploy="started"),
                record(release="succeeded", deploy="succeeded"),
            ],
        )
        self.assertEqual(result, "succeeded")

    def test_each_phase_failure_fails_its_step(self):
        for phase in status.PHASES:
            with self.subTest(phase=phase), self.assertRaisesRegex(RuntimeError, f"{phase.title()} failed"):
                self.wait(phase, [record(**{phase: "failed"})])

    def test_prior_release_failure_blocks_deploy_even_if_deploy_says_success(self):
        with self.assertRaisesRegex(RuntimeError, "Release failed"):
            self.wait("deploy", [record(release="failed", deploy="succeeded")])

    def test_postdeploy_failure_blocks_deploy(self):
        with self.assertRaisesRegex(RuntimeError, "Postdeploy failed"):
            self.wait("deploy", [record(postdeploy="failed", deploy="succeeded")])

    def test_completed_build_step_stays_successful_when_release_failed(self):
        self.assertEqual(status.phase_result(record(build="succeeded", release="failed"), "build"), "succeeded")

    def test_later_failure_is_detected_when_an_earlier_event_is_missing(self):
        with self.assertRaisesRegex(RuntimeError, "Release failed"):
            self.wait("finalize", [record(release="failed")])

    def test_failure_message_includes_apppack_log_location(self):
        item = record(release="failed")
        item["release"]["M"]["logs"] = {"S": "s3://example/106/release.log"}
        with self.assertRaisesRegex(RuntimeError, "s3://example/106/release.log"):
            status.phase_result(item, "release")

    def test_stopped_failed_and_timed_out_codebuild_fail_without_events(self):
        for codebuild in status.CODEBUILD_FAILURES:
            with self.subTest(status=codebuild), self.assertRaisesRegex(RuntimeError, codebuild):
                self.wait("build", [{}], codebuild=codebuild)

    def test_optional_phases_only_skip_after_apppack_advances(self):
        self.assertIsNone(status.phase_result(record(build="succeeded"), "test"))
        self.assertIsNone(status.phase_result(record(finalize="succeeded"), "release"))
        self.assertIn("skipped", status.phase_result(record(finalize="started"), "test"))
        self.assertIn("skipped", status.phase_result(record(deploy="started"), "release"))

    def test_required_phases_never_infer_success_from_a_later_phase(self):
        for phase in ("build", "finalize", "deploy"):
            self.assertIsNone(status.phase_result(record(release="succeeded"), phase))

    def test_unknown_status_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Unrecognized"):
            self.wait("deploy", [record(deploy="cancelled")])

    def test_timeout_fails_instead_of_turning_green(self):
        with (
            patch.object(status.time, "monotonic", side_effect=[0, 0, 60, 60]),
            self.assertRaisesRegex(RuntimeError, "Timed out.*Deploy"),
        ):
            self.wait("deploy", [record(deploy="started")])

    def test_another_app_or_build_cannot_satisfy_wait(self):
        for field, value in (("app", {"S": "other-app"}), ("build_number", {"N": "107"})):
            item = record(deploy="succeeded")
            item[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "different app/build"):
                self.wait("deploy", [item])

    def test_queries_exact_build_key_with_consistent_read(self):
        with patch.object(status, "aws", return_value={}) as aws:
            status.get_build(APP, BUILD)
        args = aws.call_args.args
        self.assertIn("--consistent-read", args)
        self.assertEqual(
            json.loads(args[args.index("--key") + 1]),
            {
                "primary_id": {"S": f"APP#{APP}"},
                "secondary_id": {"S": "BUILD#0000000106"},
            },
        )

    def test_preflight_checks_both_permissions_without_writes(self):
        with (
            patch.dict(os.environ, {"APPPACK_APP_NAME": APP, "APPPACK_PHASE": "preflight"}),
            patch.object(status, "aws", return_value={}) as aws,
        ):
            status.main()
        self.assertEqual(
            [call.args[:2] for call in aws.call_args_list],
            [
                ("dynamodb", "get-item"),
                ("codebuild", "batch-get-builds"),
            ],
        )

    def test_aws_access_denied_fails_immediately(self):
        with (
            patch.object(
                status.subprocess, "run", return_value=subprocess.CompletedProcess([], 254, "", "AccessDeniedException")
            ),
            self.assertRaisesRegex(RuntimeError, "AccessDeniedException"),
        ):
            status.wait(APP, BUILD, ARN, "build", 60)

    def test_empty_cli_output_means_record_not_created_yet(self):
        with patch.object(status.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertEqual(status.get_build(APP, BUILD), {})

    def test_missing_build_number_or_arn_never_falls_back_to_latest(self):
        env = {
            "APPPACK_APP_NAME": APP,
            "APPPACK_PHASE": "deploy",
            "APPPACK_BUILD_NUMBER": str(BUILD),
            "APPPACK_BUILD_ARN": ARN,
            "APPPACK_TIMEOUT_SECONDS": "60",
        }
        for field in ("APPPACK_BUILD_NUMBER", "APPPACK_BUILD_ARN"):
            with patch.dict(os.environ, {**env, field: ""}), self.assertRaises(ValueError):
                status.main()

    def test_summary_uses_apppack_duration(self):
        item = record(deploy="succeeded")
        item["deploy"]["M"].update({"start": {"N": "100"}, "end": {"N": "384"}})
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
                status.report(APP, BUILD, "deploy", "succeeded", item)
            self.assertIn("Deploy succeeded (284s)", summary.read_text())


if __name__ == "__main__":
    unittest.main()

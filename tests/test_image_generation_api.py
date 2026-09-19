import argparse
import json
import base64
import importlib.util
from io import BytesIO
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "image_generation_api.py"
ONE_PIXEL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def load_script_module():
    spec = importlib.util.spec_from_file_location("image_generation_api_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load image_generation_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeImageHandler(BaseHTTPRequestHandler):
    response_plan: list[tuple[int, dict[str, object] | str]] = []
    requests_seen: list[dict[str, object]] = []
    model_details: set[str] = {"gpt-image-2"}
    model_list: list[str | dict[str, object]] | None = ["gpt-image-2"]

    def do_GET(self) -> None:
        FakeImageHandler.requests_seen.append(
            {
                "method": "GET",
                "path": self.path,
                "user_agent": self.headers.get("User-Agent"),
            }
        )
        if self.path == "/v1/models":
            if FakeImageHandler.model_list is None:
                self._send_json(404, {"error": {"message": "not found"}})
            else:
                self._send_json(
                    200,
                    {
                        "data": [
                            model if isinstance(model, dict) else {"id": model, "object": "model"}
                            for model in FakeImageHandler.model_list
                        ]
                    },
                )
            return
        if self.path.startswith("/v1/models/"):
            model_id = self.path.removeprefix("/v1/models/")
            if model_id in FakeImageHandler.model_details:
                self._send_json(200, {"id": model_id, "object": "model"})
            else:
                self._send_json(404, {"error": {"message": "not found"}})
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw_bytes = self.rfile.read(length)
        raw_body = raw_bytes.decode("utf-8", errors="replace")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            body: object = json.loads(raw_body)
        else:
            body = raw_body
        FakeImageHandler.requests_seen.append(
            {
                "method": "POST",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "content_type": content_type,
                "user_agent": self.headers.get("User-Agent"),
                "body": body,
            }
        )
        status, payload = FakeImageHandler.response_plan.pop(0)
        if isinstance(payload, str):
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
            return
        self._send_json(status, payload)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeImageServer:
    def __init__(
        self,
        response_plan: list[tuple[int, dict[str, object] | str]],
        model_details: set[str] | None = None,
        model_list: list[str | dict[str, object]] | None = None,
        model_list_supported: bool = True,
    ):
        FakeImageHandler.response_plan = list(response_plan)
        FakeImageHandler.requests_seen = []
        FakeImageHandler.model_details = set(model_details if model_details is not None else {"gpt-image-2"})
        FakeImageHandler.model_list = (
            list(model_list if model_list is not None else ["gpt-image-2"])
            if model_list_supported
            else None
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeImageHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeImageServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    @property
    def requests_seen(self) -> list[dict[str, object]]:
        return FakeImageHandler.requests_seen


class GptImageApiCliTest(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        for key in list(child_env):
            if key.startswith("IMAGE_GENERATION_") or key.startswith("GPT_IMAGE_") or key.startswith("OPENAI_"):
                child_env.pop(key, None)
        child_env["IMAGE_GENERATION_DISABLE_USER_ENV"] = "1"
        child_env["GPT_IMAGE_DISABLE_USER_ENV"] = "1"
        if env:
            child_env.update(env)
        with tempfile.TemporaryDirectory() as tmpdir:
            return subprocess.run(
                [sys.executable, str(SCRIPT_PATH), *args],
                cwd=cwd or tmpdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
                input=input_text,
                check=False,
            )

    def configure_catalog(
        self,
        server: "FakeImageServer",
        catalog_path: Path,
        *,
        cwd: str | None = None,
        api_key: str = "provider-secret-configure",
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "configure",
            "--base-url",
            server.base_url,
            "--api-key",
            api_key,
            "--catalog",
            str(catalog_path),
            cwd=cwd,
        )

    def test_dry_run_uses_third_party_base_url_and_masks_key(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "test poster",
            "--base-url",
            "https://gateway.example.com/openai/v1",
            "--api-key",
            "sk-third-party-secret-1234",
            "--output",
            "poster.png",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["url"], "https://gateway.example.com/openai/v1/images/generations")
        self.assertEqual(payload["auth"], "set tail=1234")
        self.assertNotIn("sk-third-party-secret", result.stdout)
        self.assertEqual(payload["request"]["model"], "gpt-image-2")

    def test_dotenv_supports_provider_prefixed_third_party_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            (workdir / ".env").write_text(
                "\n".join(
                    [
                        "GPT_IMAGE_API_KEY=provider-key-9999",
                        "GPT_IMAGE_BASE_URL=https://provider.example/v1",
                        "GPT_IMAGE_MODEL=gpt-image-2",
                        "GPT_IMAGE_USER_AGENT=shared-image-client/2.0",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "generate",
                "--prompt",
                "test",
                "--output",
                "out.webp",
                "--dry-run",
                cwd=str(workdir),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["url"], "https://provider.example/v1/images/generations")
        self.assertEqual(payload["auth"], "set tail=9999")
        self.assertEqual(payload["request"]["model"], "gpt-image-2")
        self.assertEqual(payload["user_agent"], "shared-image-client/2.0")

    def test_windows_user_environment_fallback_precedes_openai_process_values(self) -> None:
        module = load_script_module()
        args = argparse.Namespace(env_file=None, command="generate")
        user_values = {
            "GPT_IMAGE_API_KEY": "user-provider-key-4545",
            "GPT_IMAGE_BASE_URL": "https://user-provider.example/v1",
            "GPT_IMAGE_MODEL": "user-image-model",
            "GPT_IMAGE_USER_AGENT": "windows-image-client/2.0",
        }
        process_values = {
            "OPENAI_API_KEY": "process-openai-key",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_IMAGE_MODEL": "openai-image-model",
        }

        with mock.patch.dict(module.os.environ, process_values, clear=True), mock.patch.object(
            module, "load_windows_user_environment", return_value=user_values
        ):
            config = module.resolve_config(args)

        self.assertEqual(config["api_key"], "user-provider-key-4545")
        self.assertEqual(config["base_url"], "https://user-provider.example/v1")
        self.assertEqual(config["model"], "gpt-image-2")
        self.assertEqual(config["configured_model_preference"], "user-image-model")
        self.assertEqual(config["user_agent"], "windows-image-client/2.0")
        self.assertTrue(config["windows_user_environment_loaded"])

    def test_classify_403_as_non_retriable_permission_error(self) -> None:
        result = self.run_cli("classify-error", "--status", "403", "--body", '{"error":{"message":"forbidden"}}')

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["category"], "permission_or_gateway_forbidden")
        self.assertIn("base_url", payload["next_steps"][0])

    def test_classify_524_as_retryable_gateway_timeout(self) -> None:
        result = self.run_cli("classify-error", "--status", "524", "--body", "<html>timeout</html>")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["category"], "gateway_timeout")
        self.assertIn("Cloudflare", payload["summary"])

    def test_invalid_base_url_rejected_before_request(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "test",
            "--base-url",
            "https://gateway.example.com",
            "--api-key",
            "sk-test",
            "--output",
            "out.png",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must include /v1", result.stderr)

    def test_user_agent_rejects_header_injection(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "test poster",
            "--base-url",
            "https://provider.example/v1",
            "--output",
            "poster.png",
            "--dry-run",
            env={"GPT_IMAGE_USER_AGENT": "image-client/1.0\r\nX-Injected: true"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("printable ASCII", result.stderr)

    def test_options_lists_presets_and_parameter_values(self) -> None:
        result = self.run_cli("options")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("fast", payload["presets"])
        self.assertIn("quality", payload["presets"])
        self.assertIn("high", payload["quality"])
        self.assertIn("1536x1024", payload["sizes"])
        self.assertIn("webp", payload["formats"])
        self.assertIn("normalize", payload["size_policies"])
        self.assertEqual(payload["defaults"]["size_policy"], "normalize")
        self.assertEqual(payload["defaults"]["model"], "gpt-image-2")
        self.assertEqual(payload["defaults"]["required_default_model"], "gpt-image-2")
        self.assertIn("2048x2048", payload["sizes"])
        self.assertIn("3840x2160", payload["sizes"])
        self.assertIn("grok-wide", payload["routing_modes"])
        self.assertIn("openai", payload["provider_profiles"])

    def test_capabilities_keeps_gpt_image_2_as_primary_default(self) -> None:
        result = self.run_cli("capabilities")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["default_model"], "gpt-image-2")
        self.assertEqual(payload["default_model_guard"], "gpt-image-2")
        self.assertFalse(payload["automatic_fallback"])
        self.assertEqual(payload["models"][0]["id"], "gpt-image-2")
        self.assertTrue(payload["models"][0]["transparent_background"])

    def test_final_mode_routes_to_gpt_image_2_with_2k_defaults(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "final poster",
            "--mode",
            "final",
            "--base-url",
            "https://provider.example/v1",
            "--output",
            "final.png",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["provider_profile"], "openai")
        self.assertEqual(payload["routing_mode"], "final")
        self.assertEqual(payload["request"]["model"], "gpt-image-2")
        self.assertEqual(payload["request"]["size"], "2048x2048")
        self.assertEqual(payload["request"]["quality"], "high")

    def test_draft_mode_uses_grok_request_shape_only_when_explicit(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "draft composition",
            "--mode",
            "draft",
            "--base-url",
            "https://provider.example/v1",
            "--output",
            "draft.webp",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        request_payload = payload["request"]
        self.assertEqual(payload["provider_profile"], "grok")
        self.assertEqual(request_payload["model"], "grok-imagine-image")
        self.assertEqual(request_payload["aspect_ratio"], "1:1")
        self.assertEqual(request_payload["resolution"], "1k")
        self.assertEqual(request_payload["response_format"], "b64_json")
        self.assertNotIn("size", request_payload)
        self.assertNotIn("output_format", request_payload)

    def test_explicit_gpt_image_2_remains_available_over_draft_mode(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "explicit primary model",
            "--mode",
            "draft",
            "--model",
            "gpt-image-2",
            "--base-url",
            "https://provider.example/v1",
            "--output",
            "primary.png",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["provider_profile"], "openai")
        self.assertEqual(payload["request"]["model"], "gpt-image-2")
        self.assertIn("size", payload["request"])
        self.assertNotIn("aspect_ratio", payload["request"])

    def test_gpt_image_2_accepts_4k_and_streaming_partial_images(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "4k landscape",
            "--model",
            "gpt-image-2",
            "--size",
            "3840x2160",
            "--format",
            "webp",
            "--stream",
            "--partial-images",
            "2",
            "--output",
            "landscape.webp",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        request_payload = json.loads(result.stdout)["request"]
        self.assertEqual(request_payload["size"], "3840x2160")
        self.assertTrue(request_payload["stream"])
        self.assertEqual(request_payload["partial_images"], 2)

    def test_gpt_image_2_rejects_invalid_size_constraints(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "invalid size",
            "--model",
            "gpt-image-2",
            "--size",
            "1000x1000",
            "--output",
            "invalid.png",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("multiples of 16", result.stderr)

    def test_gpt_image_2_transparent_background_requires_png_or_webp(self) -> None:
        accepted = self.run_cli(
            "generate",
            "--prompt",
            "transparent product",
            "--mode",
            "transparent",
            "--output",
            "transparent.png",
            "--dry-run",
        )
        rejected = self.run_cli(
            "generate",
            "--prompt",
            "transparent product",
            "--model",
            "gpt-image-2",
            "--background",
            "transparent",
            "--format",
            "jpeg",
            "--output",
            "transparent.jpg",
            "--dry-run",
        )

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["request"]["background"], "transparent")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("png or webp", rejected.stderr)

    def test_grok_image_2_rejects_high_quality(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "grok quality validation",
            "--model",
            "grok-imagine-image-2.0",
            "--quality",
            "high",
            "--output",
            "grok.webp",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("low or medium", result.stderr)

    def test_preset_applies_defaults_and_cli_overrides(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "wide banner",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "sk-test-2222",
            "--preset",
            "wide",
            "--quality",
            "medium",
            "--output",
            "banner.webp",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        request = payload["request"]
        self.assertEqual(payload["preset"], "wide")
        self.assertEqual(request["size"], "1536x1024")
        self.assertEqual(request["quality"], "medium")
        self.assertEqual(request["output_format"], "webp")

    def test_generate_merges_extra_json_and_saves_redacted_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            request_path = Path(tmpdir) / "request.json"
            result = self.run_cli(
                "generate",
                "--prompt",
                "provider field test",
                "--base-url",
                "https://provider.example/v1",
                "--api-key",
                "provider-secret-5656",
                "--output",
                "provider.png",
                "--extra-json",
                '{"provider_mode":"relaxed","api_key":"body-secret-9999"}',
                "--save-request",
                str(request_path),
                "--dry-run",
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("provider-secret", result.stdout)
            self.assertNotIn("body-secret", result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["request"]["provider_mode"], "relaxed")
            self.assertEqual(payload["request"]["api_key"], "set tail=9999")
            self.assertEqual(Path(payload["request_saved"]).resolve(), request_path.resolve())
            saved_request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_request["auth"], "set tail=5656")
            self.assertEqual(saved_request["request"]["api_key"], "set tail=9999")

    def test_generate_interactive_confirms_defaults_before_dry_run(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "interactive poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-1234",
            "--output",
            "poster.png",
            "--interactive",
            "--dry-run",
            input_text="\n\n\ny\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("size", result.stderr)
        self.assertIn("Proceed?", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["request"]["size"], "1024x1024")
        self.assertEqual(payload["request"]["quality"], "low")
        self.assertEqual(payload["request"]["output_format"], "png")

    def test_generate_interactive_strips_windows_pipe_bom_prefix(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "interactive poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-1234",
            "--output",
            "poster.png",
            "--interactive",
            "--dry-run",
            input_text="\ufeff\n\n\ny\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["request"]["size"], "1024x1024")

    def test_generate_interactive_strips_mojibake_bom_prefix(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "interactive poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-1234",
            "--output",
            "poster.png",
            "--interactive",
            "--dry-run",
            input_text="ďť?\n\n\ny\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["request"]["size"], "1024x1024")

    def test_generate_interactive_strips_windows_surrogate_bom_prefix(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "interactive poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-1234",
            "--output",
            "poster.png",
            "--interactive",
            "--dry-run",
            input_text="\u9518\udcbf\n\n\ny\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["request"]["size"], "1024x1024")

    def test_generate_interactive_cancel_stops_before_dry_run(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "interactive poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-1234",
            "--output",
            "poster.png",
            "--interactive",
            "--dry-run",
            input_text="\n\n\n\n",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cancelled", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_edit_interactive_overrides_image_parameters_before_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "ref.png"
            image_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            result = self.run_cli(
                "edit",
                "--prompt",
                "edit prompt",
                "--image",
                str(image_path),
                "--base-url",
                "https://provider.example/v1",
                "--api-key",
                "provider-secret-1234",
                "--output",
                str(Path(tmpdir) / "edited.jpg"),
                "--interactive",
                "--dry-run",
                input_text="1536x1024\nhigh\njpeg\ny\n",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        fields = payload["multipart"]["fields"]
        self.assertEqual(fields["size"], "1536x1024")
        self.assertEqual(fields["quality"], "high")
        self.assertEqual(fields["output_format"], "jpeg")

    def test_responses_interactive_overrides_tool_parameters_before_dry_run(self) -> None:
        result = self.run_cli(
            "responses",
            "--input-text",
            "make a product visual",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-1234",
            "--model",
            "gpt-5.4",
            "--tool-model",
            "gpt-image-2",
            "--output",
            "responses.webp",
            "--interactive",
            "--dry-run",
            input_text="1024x1536\nmedium\nwebp\ny\n",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        tool = payload["request"]["tools"][0]
        self.assertEqual(tool["size"], "1024x1536")
        self.assertEqual(tool["quality"], "medium")
        self.assertEqual(tool["output_format"], "webp")

    def test_doctor_reports_config_without_leaking_key(self) -> None:
        with FakeImageServer([]) as server:
            result = self.run_cli(
                "doctor",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-7777",
                "--model",
                "gpt-image-2",
                env={"GPT_IMAGE_USER_AGENT": "shared-image-client/2.0"},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["auth"], "set tail=7777")
        self.assertNotIn("provider-secret", result.stdout)
        self.assertEqual(payload["model_check"]["status"], 200)
        self.assertEqual(payload["user_agent"], "shared-image-client/2.0")
        self.assertEqual(server.requests_seen[0]["user_agent"], "shared-image-client/2.0")

    def test_doctor_falls_back_to_model_list_when_detail_probe_is_unsupported(self) -> None:
        with FakeImageServer([], model_details=set(), model_list=["gpt-image-2"]) as server:
            result = self.run_cli(
                "doctor",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-8888",
                "--model",
                "gpt-image-2",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_check"]["category"], "ok_via_model_list")
        self.assertFalse(payload["model_check"]["warning"])

    def test_doctor_rejects_model_missing_from_supported_model_list(self) -> None:
        with FakeImageServer([], model_details=set(), model_list=["other-model"]) as server:
            result = self.run_cli(
                "doctor",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-8888",
                "--model",
                "unknown-model",
            )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["model_check"]["category"], "model_not_listed")

    def test_doctor_treats_missing_detail_and_list_endpoints_as_gateway_warning(self) -> None:
        with FakeImageServer([], model_details=set(), model_list_supported=False) as server:
            result = self.run_cli(
                "doctor",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-8888",
                "--model",
                "unknown-model",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_check"]["category"], "model_probe_not_supported")
        self.assertTrue(payload["model_check"]["warning"])

    def test_models_classifies_live_list_and_guards_gpt_image_2(self) -> None:
        model_list = ["gpt-image-2", "gpt-image-2-4k", "grok-imagine-image-2.0", "DeepSeek-V4-Flash"]
        with FakeImageServer([], model_list=model_list) as server:
            result = self.run_cli(
                "models",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-4545",
                "--image-only",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["gpt_image_2_available"])
        self.assertEqual(payload["total_model_count"], 4)
        returned = {item["id"]: item for item in payload["models"]}
        self.assertIn("gpt-image-2", returned)
        self.assertEqual(returned["gpt-image-2"]["status"], "primary")
        self.assertEqual(returned["grok-imagine-image-2.0"]["provider_profile"], "grok")
        self.assertNotIn("DeepSeek-V4-Flash", returned)

    def test_models_warns_when_gateway_omits_gpt_image_2_without_disabling_it(self) -> None:
        with FakeImageServer([], model_list=["grok-imagine-image-2.0"]) as server:
            result = self.run_cli(
                "models",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-4646",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["gpt_image_2_available"])
        self.assertEqual(payload["default_model"], "gpt-image-2")
        self.assertEqual(payload["default_model_guard"]["category"], "gpt_image_2_not_listed")
        self.assertTrue(payload["default_model_guard"]["warning"])
        self.assertTrue(payload["gpt_image_2_status"]["warning"])

    def test_models_keeps_nested_capability_vendor_as_discovery_only(self) -> None:
        model_list = [
            {
                "id": "canvas-v7",
                "owned_by": "Acme Images",
                "capabilities": {"image": True, "image_editing": True},
                "output_modalities": ["image"],
            }
        ]
        with FakeImageServer([], model_list=model_list) as server:
            result = self.run_cli(
                "models",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-5656",
                "--image-only",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["image_model_count"], 1)
        self.assertEqual(payload["executable_image_model_count"], 0)
        self.assertEqual(payload["discovery_only_image_model_count"], 1)
        self.assertEqual(payload["vendor_count"], 0)
        self.assertEqual(payload["vendors"], [])
        self.assertEqual(payload["models"][0]["id"], "canvas-v7")
        self.assertEqual(payload["models"][0]["reported_vendor"], "acme-images")
        self.assertFalse(payload["models"][0]["enabled_for_execution"])
        self.assertEqual(payload["models"][0]["execution_status"], "discovered_not_enabled")
        self.assertTrue(payload["models"][0]["generation"])
        self.assertTrue(payload["models"][0]["editing"])

    def test_extract_model_records_skips_empty_data_wrapper(self) -> None:
        module = load_script_module()
        payload = {
            "data": [],
            "models": [{"id": "canvas-v7", "capabilities": {"image": True}}],
        }

        self.assertEqual(module.extract_model_ids(payload), ["canvas-v7"])
        records = module.extract_model_records(payload)
        self.assertEqual(records[0]["metadata"]["id"], "canvas-v7")

    def test_video_model_id_is_not_image_candidate_without_image_metadata(self) -> None:
        module = load_script_module()

        self.assertFalse(module.is_image_model_id("grok-imagine-video"))
        self.assertFalse(module.is_image_model_id("cogvideox"))

    def test_video_model_with_explicit_camel_case_image_capability_is_retained(self) -> None:
        module = load_script_module()
        metadata = {
            "ownedBy": "Acme Video Lab",
            "capabilityMap": {"imageGeneration": True},
        }

        self.assertTrue(module.is_image_model_id("grok-imagine-video", metadata))
        self.assertEqual(module.infer_model_vendor("gpt-image-2", metadata), "acme-video-lab")

    def test_video_only_task_metadata_is_not_image_capability(self) -> None:
        module = load_script_module()

        self.assertFalse(
            module.is_image_model_id(
                "grok-imagine-video",
                {"tasks": ["image-to-video"], "modalities": ["video"]},
            )
        )

    def test_explicit_negative_image_capability_overrides_image_like_id(self) -> None:
        module = load_script_module()

        self.assertFalse(
            module.is_image_model_id(
                "custom-image-model",
                {"capabilities": {"imageGeneration": {"supported": False}}},
            )
        )

    def test_nested_vendor_metadata_overrides_model_id_inference(self) -> None:
        module = load_script_module()
        metadata = {"provider": {"name": "Acme Images", "ownedBy": "acme-images"}}

        self.assertEqual(module.infer_model_vendor("gpt-image-2", metadata), "acme-images")

    def test_explicit_vendor_metadata_wins_over_lower_priority_provider_hint(self) -> None:
        module = load_script_module()
        metadata = {"vendor": "Acme", "provider": "OpenAI-compatible"}

        self.assertEqual(module.infer_model_vendor("gpt-image-2", metadata), "acme")

    def test_qwen_capability_is_discovery_only(self) -> None:
        module = load_script_module()

        record = module.capability_for_model(
            "qwen-image-2512",
            {"owned_by": "Alibaba", "capabilities": {"image": True}},
        )

        self.assertEqual(record["reported_vendor"], "alibaba")
        self.assertEqual(record["vendor"], "alibaba")
        self.assertFalse(record["enabled_for_execution"])
        self.assertEqual(record["execution_status"], "discovered_not_enabled")

    def test_live_generate_auto_selects_only_model_from_single_vendor(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        model_list = ["grok-imagine-image"]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=model_list) as server:
            output_path = Path(tmpdir) / "auto.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "single model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-5757",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
                env={"IMAGE_GENERATION_MODEL": "gpt-image-2"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["model_source"], "default")
            self.assertEqual(payload["selected_vendor"], "openai")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            post_requests = [item for item in server.requests_seen if item["method"] == "POST"]
            self.assertEqual(len(post_requests), 1)
            self.assertEqual(post_requests[0]["body"]["model"], "gpt-image-2")

    def test_generate_defaults_without_catalog_even_when_multiple_models_are_listed(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        model_list = [
            "grok-imagine-image",
            "grok-imagine-image-2.0",
        ]
        with FakeImageServer([(200, response)], model_list=model_list) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "choose a model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-5858",
                "--output",
                "out.png",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_configure_catalog_excludes_discovery_only_models(self) -> None:
        model_list = [
            {"id": "qwen-image-2512", "owned_by": "Alibaba", "capabilities": {"image": True}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=model_list) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            result = self.run_cli(
                "configure",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-5958",
                "--catalog",
                str(catalog_path),
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertNotIn("qwen-image-2512", [item["model"] for item in payload["choices"]])
            self.assertIn("gpt-image-2", [item["model"] for item in payload["choices"]])
            self.assertEqual([item["method"] for item in server.requests_seen], ["GET"])

    def test_requested_vendor_does_not_override_no_choice_default(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        model_list = [
            "gpt-image-2",
            "gpt-image-2-4k",
            "grok-imagine-image",
        ]
        with FakeImageServer([(200, response)], model_list=model_list) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "choose openai model",
                "--vendor",
                "openai",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-5959",
                "--output",
                "out.png",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["selected_vendor"], "openai")
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_multiple_vendors_still_use_default_without_choice(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with FakeImageServer([(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "choose vendor",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6060",
                "--output",
                "out.png",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_configured_model_variable_cannot_override_default(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "configured model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6161",
                "--output",
                str(Path(tmpdir) / "configured.png"),
                "--size-policy",
                "provider",
                cwd=tmpdir,
                env={"IMAGE_GENERATION_MODEL": "gpt-image-2"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_configured_vendor_does_not_override_default(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with FakeImageServer([(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "configured vendor preference",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6162",
                "--output",
                "out.png",
                env={"IMAGE_GENERATION_VENDOR": "openai"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_explicit_cli_model_still_skips_discovery(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "explicit model",
                "--model",
                "gpt-image-2",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6163",
                "--output",
                str(Path(tmpdir) / "explicit.png"),
                "--size-policy",
                "provider",
                cwd=tmpdir,
                env={"IMAGE_GENERATION_MODEL": "grok-imagine-image"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["model_source"], "cli")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_configured_routing_mode_cannot_override_no_choice_default(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "configured draft mode",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6262",
                "--output",
                str(Path(tmpdir) / "draft.webp"),
                "--size-policy",
                "provider",
                cwd=tmpdir,
                env={"IMAGE_GENERATION_ROUTING_MODE": "draft"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["routing_mode"], "auto")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_select_model_interactive_skips_vendor_prompt_for_single_vendor(self) -> None:
        model_list = [
            "grok-imagine-image",
            "grok-imagine-image-2.0",
        ]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=model_list) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6363",
                "--catalog",
                str(catalog_path),
                "--interactive",
                input_text="3\n",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected_vendor"], "xai")
        self.assertEqual(payload["selected_model"], "grok-imagine-image-2.0")
        self.assertNotIn("Available image vendors", result.stderr)
        self.assertIn("Available catalog image models", result.stderr)

    def test_select_model_interactive_uses_one_flat_choice(self) -> None:
        model_list = [
            "grok-imagine-image",
            "gpt-image-2",
        ]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=model_list) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6463",
                "--catalog",
                str(catalog_path),
                "--interactive",
                input_text="2\n",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected_vendor"], "xai")
        self.assertEqual(payload["selected_model"], "grok-imagine-image")
        self.assertIn("Available catalog image models", result.stderr)
        self.assertIn("Choose one number, exact model id, or alias", result.stderr)
        self.assertNotIn("Choose vendor number", result.stderr)

    def test_select_model_emits_flat_choices_and_tiers(self) -> None:
        model_list = [
            "gpt-image-2",
            "gpt-image-2.5",
            "gpt-image-2-4k",
            "grok-imagine-image",
            "grok-imagine-image-2.0",
            {"id": "qwen-image-2512", "owned_by": "Alibaba", "capabilities": {"image": True}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=model_list) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6464",
                "--catalog",
                str(catalog_path),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selection_mode"], "catalog")
        choices = payload["choices"]
        self.assertEqual([item["number"] for item in choices], [1, 2, 3, 4, 5])
        self.assertEqual(
            [(item["vendor"], item["model"]) for item in choices],
            [
                ("openai", "gpt-image-2"),
                ("openai", "gpt-image-2.5"),
                ("openai", "gpt-image-2-4k"),
                ("xai", "grok-imagine-image"),
                ("xai", "grok-imagine-image-2.0"),
            ],
        )
        self.assertTrue(choices[0]["recommended"])
        self.assertFalse(any(item["recommended"] for item in choices[1:3]))
        self.assertTrue(all(item["recommended"] for item in choices[3:]))
        self.assertEqual(payload["recommended_choices"], [1, 4, 5])
        self.assertEqual(payload["more_choices"], [2, 3])
        self.assertEqual([item["vendor"] for item in choices], ["openai", "openai", "openai", "xai", "xai"])
        self.assertIn("gpt2", choices[0]["aliases"])
        self.assertNotIn("qwen-image-2512", [item["model"] for item in choices])

    def test_select_model_choice_accepts_alias_without_second_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer(
            [],
            model_list=["gpt-image-2", "grok-imagine-image", "grok-imagine-image-2.0"],
        ) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6465",
                "--catalog",
                str(catalog_path),
                "--choice",
                "grok2",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected_vendor"], "xai")
        self.assertEqual(payload["selected_model"], "grok-imagine-image-2.0")
        self.assertEqual(payload["selection_reason"], "catalog_choice")
        self.assertEqual(payload["selection_choice"], "grok2")
        self.assertEqual(payload["selection_choice_alias"], "grok2")
        self.assertEqual(payload["next_command_args"], ["--choice", "3"])
        self.assertEqual([item["method"] for item in server.requests_seen], ["GET"])

    def test_select_model_vendor_scopes_numeric_choice_and_choice_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer(
            [],
            model_list=["gpt-image-2", "grok-imagine-image", "grok-imagine-image-2.0"],
        ) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6469",
                "--catalog",
                str(catalog_path),
                "--vendor",
                "xai",
                "--choice",
                "2",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected_vendor"], "xai")
        self.assertEqual(payload["selected_model"], "grok-imagine-image")
        self.assertEqual(payload["selection_reason"], "catalog_choice")
        self.assertEqual([item["number"] for item in payload["choices"]], [2, 3])
        self.assertEqual({item["vendor"] for item in payload["choices"]}, {"xai"})
        self.assertEqual(payload["recommended_choices"], [2, 3])
        self.assertEqual(payload["more_choices"], [])
        self.assertEqual([item["method"] for item in server.requests_seen], ["GET"])

    def test_live_generate_choice_resolves_and_posts_canonical_model(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer(
            [(200, response)],
            model_list=["gpt-image-2", "grok-imagine-image-2.0"],
        ) as server:
            output_path = Path(tmpdir) / "choice.webp"
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "generate",
                "--prompt",
                "one-shot choice",
                "--choice",
                "2",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6466",
                "--catalog",
                str(catalog_path),
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model"], "grok-imagine-image-2.0")
        self.assertEqual(payload["selection_reason"], "catalog_choice")
        self.assertIsNone(payload["selection_choice_alias"])
        self.assertEqual([item["method"] for item in server.requests_seen], ["GET", "POST"])
        self.assertEqual(server.requests_seen[-1]["body"]["model"], "grok-imagine-image-2.0")

    def test_explicit_model_alias_is_resolved_before_live_request(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer(
            [(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]
        ) as server:
            output_path = Path(tmpdir) / "alias.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "alias choice",
                "--model",
                "gpt2",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6467",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["model_requested"], "gpt2")
        self.assertEqual(payload["model_alias"], "gpt2")
        self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])
        self.assertEqual(server.requests_seen[0]["body"]["model"], "gpt-image-2")

    def test_generate_choice_requires_discovery_before_dry_run(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "choice dry run",
            "--choice",
            "grok2",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-6468",
            "--output",
            "choice.png",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configure", result.stderr)

    def test_responses_uses_default_image_tool_model_without_choice(self) -> None:
        response = {"output": [{"type": "image_generation_call", "result": ONE_PIXEL_PNG_B64}]}
        model_list = [
            "gpt-image-2",
            "gpt-image-2-4k",
            "gpt-5.4",
        ]
        with FakeImageServer([(200, response)], model_list=model_list) as server:
            result = self.run_cli(
                "responses",
                "--input-text",
                "create a poster",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6464",
                "--model",
                "gpt-5.4",
                "--output",
                "poster.png",
                env={"GPT_IMAGE_TOOL_MODEL": "gpt-image-2"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual([item["method"] for item in server.requests_seen], ["POST"])

    def test_responses_choice_sets_image_tool_model_only(self) -> None:
        response = {"output": [{"type": "image_generation_call", "result": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer(
            [(200, response)],
            model_list=["gpt-image-2", "gpt-image-2-4k", "gpt-5.4"],
        ) as server:
            output_path = Path(tmpdir) / "responses-choice.png"
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "responses",
                "--input-text",
                "create a poster",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6470",
                "--model",
                "gpt-5.4",
                "--choice",
                "gpt2",
                "--catalog",
                str(catalog_path),
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["model_requested"], "gpt-image-2")
        self.assertEqual(payload["selection_reason"], "catalog_choice")
        self.assertEqual(payload["selection_choice_alias"], "gpt2")
        self.assertEqual([item["method"] for item in server.requests_seen], ["GET", "POST"])
        request_seen = server.requests_seen[-1]
        self.assertEqual(request_seen["body"]["model"], "gpt-5.4")
        self.assertEqual(request_seen["body"]["tools"][0]["model"], "gpt-image-2")
        self.assertEqual(payload["saved"], [str(output_path.resolve())])

    def test_select_model_ignores_configured_model_as_completed_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=["gpt-image-2", "grok-imagine-image"]) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6467",
                "--catalog",
                str(catalog_path),
                env={"GPT_IMAGE_MODEL": "gpt-image-2"},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["default_model"], "gpt-image-2")
        self.assertEqual(payload["selected_model"], "gpt-image-2")
        self.assertEqual(payload["selection_reason"], "catalog_default")
        self.assertEqual([item["method"] for item in server.requests_seen], ["GET"])

    def test_select_model_rejects_explicit_qwen_as_not_enabled(self) -> None:
        model_list = [
            {"id": "qwen-image-2512", "owned_by": "Alibaba", "capabilities": {"image": True}},
            "gpt-image-2",
        ]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=model_list) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            result = self.run_cli(
                "select-model",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6465",
                "--catalog",
                str(catalog_path),
                "--model",
                "qwen-image-2512",
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["category"], "catalog_selection_error")
        self.assertIn("not in the catalog", payload["error"]["summary"])
        self.assertEqual([item["method"] for item in server.requests_seen], ["GET"])

    def test_generate_qwen_dry_run_is_rejected_locally(self) -> None:
        result = self.run_cli(
            "generate",
            "--prompt",
            "qwen gate",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-6466",
            "--model",
            "qwen-image-2512",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("discovery-only", result.stderr)
        self.assertIn("only GPT Image", result.stderr)

    def test_generate_saves_image_from_fake_provider_and_prompt_file(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            workdir = Path(tmpdir)
            prompt_path = workdir / "prompt.txt"
            output_path = workdir / "out.png"
            prompt_path.write_text("prompt from file", encoding="utf-8")
            result = self.run_cli(
                "generate",
                "--prompt-file",
                str(prompt_path),
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-3333",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=str(workdir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(Path(payload["saved"][0]).read_bytes(), base64.b64decode(ONE_PIXEL_PNG_B64))
            self.assertEqual(payload["artifacts"][0]["provider_size"], [1, 1])
            self.assertEqual(payload["artifacts"][0]["final_size"], [1, 1])
            self.assertFalse(payload["artifacts"][0]["normalized"])
            self.assertEqual(server.requests_seen[0]["path"], "/v1/images/generations")
            post_requests = [item for item in server.requests_seen if item["method"] == "POST"]
            self.assertEqual(len(post_requests), 1)
            self.assertEqual(post_requests[0]["path"], "/v1/images/generations")
            self.assertEqual(post_requests[0]["user_agent"], "gpt-image-client/1.0")
            self.assertEqual(post_requests[0]["body"]["prompt"], "prompt from file")

    def test_provider_encoding_is_normalized_to_output_extension(self) -> None:
        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (2, 2), "white").save(buffer, format="JPEG")
        response = {"data": [{"b64_json": base64.b64encode(buffer.getvalue()).decode("ascii")}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            output_path = Path(tmpdir) / "converted.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "format normalization",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-2929",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            artifact = payload["artifacts"][0]
            self.assertEqual(artifact["provider_format"], "jpeg")
            self.assertEqual(artifact["final_format"], "png")
            self.assertTrue(artifact["format_normalized"])
            with Image.open(output_path) as image:
                self.assertEqual(image.format, "PNG")

    def test_default_size_policy_normalizes_same_aspect_provider_output(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            output_path = Path(tmpdir) / "normalized.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "normalize test",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-3030",
                "--size",
                "4x4",
                "--model",
                "gpt-image-test",
                "--output",
                str(output_path),
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            artifact = payload["artifacts"][0]
            self.assertEqual(artifact["provider_size"], [1, 1])
            self.assertEqual(artifact["final_size"], [4, 4])
            self.assertTrue(artifact["normalized"])
            from PIL import Image

            with Image.open(output_path) as image:
                self.assertEqual(image.size, (4, 4))

    def test_default_size_policy_rejects_provider_aspect_ratio_change(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            output_path = Path(tmpdir) / "wrong-aspect.png"
            error_path = Path(tmpdir) / "error.json"
            result = self.run_cli(
                "generate",
                "--prompt",
                "aspect test",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-3131",
                "--size",
                "4x2",
                "--model",
                "gpt-image-test",
                "--output",
                str(output_path),
                "--save-error",
                str(error_path),
                cwd=tmpdir,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output_path.exists())
            error_payload = json.loads(error_path.read_text(encoding="utf-8"))
            self.assertEqual(error_payload["category"], "output_aspect_ratio_mismatch")
            self.assertFalse(error_payload["retryable"])

    def test_retryable_524_retries_then_saves_image(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(524, "<html>timeout</html>"), (200, response)]) as server:
            output_path = Path(tmpdir) / "retry.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "retry test",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-4444",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                "--retries",
                "1",
                "--retry-delay",
                "0",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["attempt"], 2)
        self.assertEqual(len([item for item in server.requests_seen if item["method"] == "GET"]), 0)
        self.assertEqual(len([item for item in server.requests_seen if item["method"] == "POST"]), 2)

    def test_403_does_not_retry_and_saves_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(403, {"error": {"message": "forbidden"}})]) as server:
            error_path = Path(tmpdir) / "error.json"
            result = self.run_cli(
                "generate",
                "--prompt",
                "forbidden test",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-5555",
                "--output",
                str(Path(tmpdir) / "out.png"),
                "--retries",
                "3",
                "--retry-delay",
                "0",
                "--save-error",
                str(error_path),
                cwd=tmpdir,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len([item for item in server.requests_seen if item["method"] == "GET"]), 0)
            self.assertEqual(len([item for item in server.requests_seen if item["method"] == "POST"]), 1)
            saved_error = json.loads(error_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_error["category"], "permission_or_gateway_forbidden")
            self.assertFalse(saved_error["retryable"])

    def test_edit_sends_multipart_images_mask_and_prompt_file(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            workdir = Path(tmpdir)
            image_a = workdir / "a.png"
            image_b = workdir / "b.png"
            mask = workdir / "mask.png"
            prompt_file = workdir / "edit-prompt.txt"
            for path in [image_a, image_b, mask]:
                path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            prompt_file.write_text("replace the sky", encoding="utf-8")
            output_path = workdir / "edit.png"

            result = self.run_cli(
                "edit",
                "--prompt-file",
                str(prompt_file),
                "--image",
                str(image_a),
                "--image",
                str(image_b),
                "--mask",
                str(mask),
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-6666",
                "--user-agent",
                "shared-image-client/3.0",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=str(workdir),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(Path(payload["saved"][0]).read_bytes(), base64.b64decode(ONE_PIXEL_PNG_B64))
            request_seen = next(item for item in server.requests_seen if item["method"] == "POST")
            self.assertEqual(request_seen["path"], "/v1/images/edits")
            self.assertIn("multipart/form-data", request_seen["content_type"])
            self.assertEqual(request_seen["user_agent"], "shared-image-client/3.0")
            body = str(request_seen["body"])
            self.assertIn('name="prompt"', body)
            self.assertIn("replace the sky", body)
            self.assertIn('name="image[]"', body)
            self.assertIn('name="mask"', body)

    def test_edit_extra_json_file_adds_provider_multipart_fields(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            workdir = Path(tmpdir)
            image_path = workdir / "ref.png"
            extra_path = workdir / "extra.json"
            output_path = workdir / "edit-extra.png"
            image_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            extra_path.write_text(
                json.dumps({"strength": 0.8, "provider_metadata": {"project": "skill"}}),
                encoding="utf-8",
            )

            result = self.run_cli(
                "edit",
                "--prompt",
                "edit with gateway fields",
                "--image",
                str(image_path),
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-8989",
                "--extra-json-file",
                str(extra_path),
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            body = str(next(item for item in server.requests_seen if item["method"] == "POST")["body"])
            self.assertIn('name="strength"', body)
            self.assertIn("0.8", body)
            self.assertIn('name="provider_metadata"', body)
            self.assertIn('"project": "skill"', body)

    def test_edit_dry_run_hides_file_bytes_and_lists_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            image_path = workdir / "ref.png"
            image_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            result = self.run_cli(
                "edit",
                "--prompt",
                "edit dry run",
                "--image",
                str(image_path),
                "--base-url",
                "https://provider.example/v1",
                "--api-key",
                "provider-secret-1212",
                "--output",
                "edit.png",
                "--dry-run",
                cwd=str(workdir),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["url"], "https://provider.example/v1/images/edits")
        self.assertEqual(payload["multipart"]["files"][0]["field"], "image[]")
        self.assertEqual(payload["auth"], "set tail=1212")
        self.assertNotIn(ONE_PIXEL_PNG_B64, result.stdout)

    def test_grok_edit_uses_json_and_data_url_for_up_to_three_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            image_a = workdir / "a.png"
            image_b = workdir / "b.png"
            image_a.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            image_b.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            result = self.run_cli(
                "edit",
                "--prompt",
                "combine references",
                "--image",
                str(image_a),
                "--image",
                str(image_b),
                "--model",
                "grok-imagine-image-2.0",
                "--size",
                "2048x1152",
                "--quality",
                "medium",
                "--base-url",
                "https://provider.example/v1",
                "--output",
                "grok-edit.webp",
                "--dry-run",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        request_payload = payload["request"]
        self.assertEqual(payload["provider_profile"], "grok")
        self.assertEqual(request_payload["aspect_ratio"], "16:9")
        self.assertEqual(request_payload["resolution"], "2k")
        self.assertEqual(len(request_payload["images"]), 2)
        self.assertEqual(request_payload["images"][0]["type"], "image_url")
        self.assertIn("<base64 omitted>", request_payload["images"][0]["url"])
        self.assertNotIn("multipart", payload)

    def test_grok_edit_live_transport_is_json(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            image_path = Path(tmpdir) / "ref.png"
            output_path = Path(tmpdir) / "grok-edit.png"
            image_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            result = self.run_cli(
                "edit",
                "--prompt",
                "restyle reference",
                "--image",
                str(image_path),
                "--model",
                "grok-imagine-image-2.0",
                "--quality",
                "medium",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-4747",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=tmpdir,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model"], "grok-imagine-image-2.0")
        self.assertEqual(payload["provider_profile"], "grok")
        request_seen = server.requests_seen[0]
        self.assertIn("application/json", request_seen["content_type"])
        self.assertEqual(request_seen["body"]["model"], "grok-imagine-image-2.0")
        self.assertTrue(request_seen["body"]["image"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("size", request_seen["body"])

    def test_grok_edit_rejects_mask_and_keeps_gpt_image_2_for_masked_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "ref.png"
            mask_path = Path(tmpdir) / "mask.png"
            image_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            mask_path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_B64))
            result = self.run_cli(
                "edit",
                "--prompt",
                "masked edit",
                "--image",
                str(image_path),
                "--mask",
                str(mask_path),
                "--model",
                "grok-imagine-image-2.0",
                "--output",
                "grok-mask.webp",
                "--dry-run",
                cwd=tmpdir,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("use gpt-image-2", result.stderr)

    def test_responses_dry_run_uses_text_model_and_image_tool_model(self) -> None:
        result = self.run_cli(
            "responses",
            "--input-text",
            "create a poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-2323",
            "--model",
            "gpt-5.4",
            "--tool-model",
            "gpt-image-2",
            "--preset",
            "quality",
            "--output",
            "poster.png",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        request_payload = payload["request"]
        self.assertEqual(payload["url"], "https://provider.example/v1/responses")
        self.assertEqual(request_payload["model"], "gpt-5.4")
        self.assertEqual(request_payload["tools"][0]["type"], "image_generation")
        self.assertEqual(request_payload["tools"][0]["model"], "gpt-image-2")
        self.assertEqual(request_payload["tools"][0]["quality"], "high")

    def test_official_openai_responses_can_omit_implicit_tool_model(self) -> None:
        result = self.run_cli(
            "responses",
            "--input-text",
            "create an image",
            "--base-url",
            "https://api.openai.com/v1",
            "--api-key",
            "provider-secret-2424",
            "--model",
            "gpt-5.4",
            "--output",
            "official.png",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        tool = json.loads(result.stdout)["request"]["tools"][0]
        self.assertNotIn("model", tool)
        self.assertEqual(tool["type"], "image_generation")

    def test_responses_extra_json_patches_top_level_and_tool_fields(self) -> None:
        result = self.run_cli(
            "responses",
            "--input-text",
            "create a poster",
            "--base-url",
            "https://provider.example/v1",
            "--api-key",
            "provider-secret-6767",
            "--model",
            "gpt-5.4",
            "--tool-model",
            "gpt-image-2",
            "--extra-json",
            '{"metadata":{"job":"abc"}}',
            "--tool-extra-json",
            '{"partial_images":2,"background":"transparent"}',
            "--output",
            "poster.png",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        request_payload = payload["request"]
        self.assertEqual(request_payload["metadata"]["job"], "abc")
        self.assertEqual(request_payload["tools"][0]["partial_images"], 2)
        self.assertEqual(request_payload["tools"][0]["background"], "transparent")

    def test_responses_rejects_gpt_image_as_top_level_model(self) -> None:
        result = self.run_cli(
            "responses",
            "--input-text",
            "create a poster",
            "--model",
            "gpt-image-2",
            "--output",
            "poster.png",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("text-capable Responses model", result.stderr)

    def test_responses_saves_image_result_from_fake_provider(self) -> None:
        response = {"output": [{"type": "image_generation_call", "result": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            output_path = Path(tmpdir) / "responses.png"
            result = self.run_cli(
                "responses",
                "--input-text",
                "create a poster",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-3434",
                "--model",
                "gpt-5.4",
                "--tool-model",
                "gpt-image-2",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=str(tmpdir),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(Path(payload["saved"][0]).read_bytes(), base64.b64decode(ONE_PIXEL_PNG_B64))
            request_seen = server.requests_seen[0]
            self.assertEqual(request_seen["path"], "/v1/responses")
            self.assertEqual(request_seen["body"]["model"], "gpt-5.4")
            self.assertEqual(request_seen["body"]["tools"][0]["model"], "gpt-image-2")

    def test_responses_saves_data_url_image_result_from_fake_provider(self) -> None:
        response = {"output": [{"type": "image_generation_call", "result": f"data:image/png;base64,{ONE_PIXEL_PNG_B64}"}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            output_path = Path(tmpdir) / "responses-data-url.png"
            result = self.run_cli(
                "responses",
                "--input-text",
                "create a poster",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-7878",
                "--model",
                "gpt-5.4",
                "--tool-model",
                "gpt-image-2",
                "--output",
                str(output_path),
                "--size-policy",
                "provider",
                cwd=str(tmpdir),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(Path(payload["saved"][0]).read_bytes(), base64.b64decode(ONE_PIXEL_PNG_B64))

    def test_sse_parser_collects_partial_and_final_images(self) -> None:
        module = load_script_module()
        raw = "\n".join(
            [
                "event: response.image_generation_call.partial_image",
                f'data: {{"type":"response.image_generation_call.partial_image","partial_image_b64":"{ONE_PIXEL_PNG_B64}"}}',
                "",
                "event: response.completed",
                f'data: {{"type":"response.completed","response":{{"output":[{{"result":"{ONE_PIXEL_PNG_B64}"}}]}}}}',
                "",
                "data: [DONE]",
                "",
            ]
        )

        payload = module.parse_sse_payload(raw)
        self.assertEqual(len(payload["events"]), 2)
        records = module.collect_response_image_records(payload)
        self.assertEqual(len(records), 2)

    def test_configure_writes_numbered_catalog_once_without_api_key(self) -> None:
        model_list = [
            "grok-imagine-image-2.0",
            {"id": "qwen-image-2512", "owned_by": "Alibaba", "capabilities": {"image": True}},
            "gpt-image-2-4k",
        ]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=model_list) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            result = self.configure_catalog(server, catalog_path, cwd=tmpdir, api_key="super-secret-9797")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            catalog_text = catalog_path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret-9797", catalog_text)
            self.assertEqual([item["method"] for item in server.requests_seen], ["GET"])
            self.assertEqual([item["number"] for item in payload["choices"]], [1, 2, 3])
            self.assertEqual(payload["choices"][0]["model"], "gpt-image-2")
            self.assertTrue(payload["choices"][0]["default"])
            self.assertFalse(any(item["model"].startswith("qwen-") for item in payload["choices"]))

    def test_catalog_choice_dry_run_is_offline_after_configuration(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer(
            [(200, response)], model_list=["gpt-image-2", "grok-imagine-image"]
        ) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(server, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            server.requests_seen.clear()
            result = self.run_cli(
                "generate",
                "--prompt",
                "offline catalog choice",
                "--choice",
                "2",
                "--base-url",
                server.base_url,
                "--catalog",
                str(catalog_path),
                "--output",
                str(Path(tmpdir) / "choice.png"),
                "--dry-run",
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["request"]["model"], "grok-imagine-image")
            self.assertEqual(payload["selection_mode"], "catalog")
            self.assertEqual(server.requests_seen, [])

    def test_grok_environment_model_cannot_change_no_choice_default(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)]) as server:
            result = self.run_cli(
                "generate",
                "--prompt",
                "default guard",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-9898",
                "--output",
                str(Path(tmpdir) / "default.png"),
                "--size-policy",
                "provider",
                cwd=tmpdir,
                env={"IMAGE_GENERATION_MODEL": "grok-imagine-image"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["selection_reason"], "default_gpt_image_2")
            self.assertEqual(server.requests_seen[0]["body"]["model"], "gpt-image-2")

    def test_catalog_base_url_mismatch_is_rejected_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([], model_list=["gpt-image-2"]) as first:
            catalog_path = Path(tmpdir) / "catalog.json"
            configured = self.configure_catalog(first, catalog_path, cwd=tmpdir)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            with FakeImageServer([], model_list=["gpt-image-2"]) as second:
                result = self.run_cli(
                    "generate",
                    "--prompt",
                    "mismatched endpoint",
                    "--choice",
                    "1",
                    "--base-url",
                    second.base_url,
                    "--catalog",
                    str(catalog_path),
                    "--output",
                    "mismatch.png",
                    "--dry-run",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("different Base URL", result.stderr)
                self.assertEqual(second.requests_seen, [])

    def test_catalog_rejects_non_gpt_or_grok_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([]) as server:
            catalog_path = Path(tmpdir) / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "base_url": server.base_url,
                        "default_model": "gpt-image-2",
                        "default_choice": 1,
                        "choices": [
                            {
                                "number": 1,
                                "model": "qwen-image-2512",
                                "vendor": "alibaba",
                                "generation": True,
                                "editing": True,
                            }
                        ],
                        "vendors": [],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "generate",
                "--prompt",
                "catalog gate",
                "--choice",
                "1",
                "--base-url",
                server.base_url,
                "--catalog",
                str(catalog_path),
                "--output",
                "blocked.png",
                "--dry-run",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-executable model", result.stderr)
            self.assertEqual(server.requests_seen, [])

    def test_non_image_api_helper_commands_are_not_part_of_skill(self) -> None:
        for command in ("outline", "analyze-copy", "compose-slide"):
            result = self.run_cli(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid choice", result.stderr)

    def test_generate_writes_sidecar_reproducibility_record(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=["gpt-image-2"]) as server:
            output_path = Path(tmpdir) / "hero.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "a lone convenience store on a rain-soaked street",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-sidecar-1",
                "--preset",
                "quality",
                "--output",
                str(output_path),
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["sidecars"]), 1)
            sidecar_path = Path(payload["sidecars"][0])
            self.assertTrue(sidecar_path.is_file())
            self.assertEqual(sidecar_path.name, "hero.png.json")
            record = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(record["record_type"], "image-generation-sidecar")
            self.assertEqual(record["command"], "generate")
            self.assertEqual(record["model"], "gpt-image-2")
            self.assertEqual(record["prompt"], "a lone convenience store on a rain-soaked street")
            self.assertEqual(record["base_url"], server.base_url)
            self.assertEqual(record["parameters"]["preset"], "quality")
            self.assertEqual(record["output"]["path"], str(output_path.resolve()))
            self.assertEqual(record["output"]["bytes"], output_path.stat().st_size)
            self.assertEqual(len(record["output"]["sha256"]), 64)
            self.assertNotIn("provider-secret-sidecar-1", sidecar_path.read_text(encoding="utf-8"))

    def test_generate_no_sidecar_flag_skips_record(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=["gpt-image-2"]) as server:
            output_path = Path(tmpdir) / "hero.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "no ledger please",
                "--base-url",
                server.base_url,
                "--api-key",
                "provider-secret-sidecar-2",
                "--no-sidecar",
                "--output",
                str(output_path),
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["sidecars"], [])
            self.assertTrue(output_path.is_file())
            self.assertFalse(output_path.with_name("hero.png.json").exists())

    def test_generate_sidecar_records_catalog_choice(self) -> None:
        response = {"data": [{"b64_json": ONE_PIXEL_PNG_B64}]}
        model_list = ["gpt-image-2", "grok-imagine-image"]
        with tempfile.TemporaryDirectory() as tmpdir, FakeImageServer([(200, response)], model_list=model_list) as server:
            workdir = Path(tmpdir)
            catalog_path = workdir / "catalog.json"
            configured = self.configure_catalog(server, catalog_path)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            output_path = workdir / "chosen.png"
            result = self.run_cli(
                "generate",
                "--prompt",
                "catalog choice record",
                "--choice",
                "2",
                "--base-url",
                server.base_url,
                "--catalog",
                str(catalog_path),
                "--api-key",
                "provider-secret-sidecar-3",
                "--output",
                str(output_path),
                cwd=tmpdir,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            sidecar_path = Path(payload["sidecars"][0])
            record = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(record["model"], "grok-imagine-image")
            self.assertEqual(record["choice"], "2")
            self.assertNotIn("provider-secret-sidecar-3", sidecar_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

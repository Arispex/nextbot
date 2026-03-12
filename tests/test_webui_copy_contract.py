from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from next_bot.db import Base, Group, Server, User
from server.pages.console_page import (
    render_groups_page,
    render_servers_page,
    render_users_page,
)
from server.routes.webui import _build_session_cookie
from server.routes.webui_settings import SettingsValidationError
from server.server_config import WebServerSettings
from server.web_server import create_app


class WebuiCopyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )
        self.settings = WebServerSettings(
            host="127.0.0.1",
            port=18081,
            public_base_url="http://127.0.0.1:18081",
            webui_token="test-token",
            session_secret="test-session-secret",
            auth_file_path=str(Path(self.temp_dir.name) / ".webui_auth.json"),
            auth_file_created=False,
        )
        app = create_app(self.settings)
        self.client = TestClient(app)
        self.client.cookies.set(
            self.settings.cookie_name,
            _build_session_cookie(self.settings.session_secret),
        )

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _get_session(self):
        return self.session_factory()

    def test_create_success_messages_are_consistent(self) -> None:
        with (
            patch("server.routes.webui_servers.get_session", side_effect=self._get_session),
            patch("server.routes.webui_users.get_session", side_effect=self._get_session),
            patch("server.routes.webui_groups.get_session", side_effect=self._get_session),
        ):
            session = self.session_factory()
            try:
                session.add_all(
                    [
                        Group(name="guest", permissions="", inherits=""),
                        Group(name="default", permissions="", inherits="guest"),
                    ]
                )
                session.commit()
            finally:
                session.close()

            server_response = self.client.post(
                "/webui/api/servers",
                json={
                    "name": "主服",
                    "ip": "127.0.0.1",
                    "game_port": 7777,
                    "restapi_port": 7878,
                    "token": "abc123",
                },
            )
            user_response = self.client.post(
                "/webui/api/users",
                json={
                    "user_id": "10001",
                    "name": "Alice",
                    "coins": 0,
                    "group": "default",
                    "permissions": "",
                },
            )
            group_response = self.client.post(
                "/webui/api/groups",
                json={
                    "name": "moderator",
                    "permissions": "server.list",
                    "inherits": "default",
                },
            )

        self.assertEqual(server_response.status_code, 200)
        self.assertEqual(server_response.json()["message"], "创建成功")
        self.assertEqual(user_response.status_code, 200)
        self.assertEqual(user_response.json()["message"], "创建成功")
        self.assertEqual(group_response.status_code, 200)
        self.assertEqual(group_response.json()["message"], "创建成功")

    def test_settings_validation_error_preserves_raw_message_and_field(self) -> None:
        with patch(
            "server.routes.webui_settings.save_settings",
            side_effect=SettingsValidationError(
                "web_server_host 不能为空",
                field="web_server_host",
            ),
        ):
            response = self.client.put(
                "/webui/api/settings",
                json={"data": {"web_server_host": ""}},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 422)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["message"], "保存失败，web_server_host 不能为空")
        self.assertEqual(payload["field"], "web_server_host")

    def test_settings_unexpected_error_uses_action_result_reason_format(self) -> None:
        with patch(
            "server.routes.webui_settings.save_settings",
            side_effect=Exception("Connection timeout"),
        ):
            response = self.client.put(
                "/webui/api/settings",
                json={"data": {"web_server_host": "127.0.0.1"}},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 500)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["message"], "保存失败，Connection timeout")


class ConsolePageCopyTest(unittest.TestCase):
    def test_render_servers_page_uses_consistent_create_copy(self) -> None:
        html = render_servers_page()
        self.assertIn("创建服务器", html)
        self.assertNotIn("新增服务器", html)

    def test_render_users_page_uses_consistent_create_copy(self) -> None:
        html = render_users_page()
        self.assertIn("创建用户", html)
        self.assertNotIn("新增用户", html)

    def test_render_groups_page_uses_consistent_create_copy(self) -> None:
        html = render_groups_page()
        self.assertIn("创建身份组", html)
        self.assertNotIn("新增身份组", html)


if __name__ == "__main__":
    unittest.main()

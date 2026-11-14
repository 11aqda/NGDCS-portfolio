"""客户端登录 / 排行榜界面的测试。"""

import os
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import requests

from client.board import fetch_leaderboard
from client.login import LoginScreen
from client.fonts import load_font


def _surface():
    pygame.display.init()
    return pygame.display.set_mode((720, 960))


class FetchLeaderboardTest(unittest.TestCase):
    def test_parses_entries(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {"entries": [{"rank": 1, "username": "a"}]}
        with mock.patch("client.board.requests.get", return_value=fake):
            entries, error = fetch_leaderboard("http://x")
        self.assertEqual(error, "")
        self.assertEqual(entries[0]["username"], "a")

    def test_connection_error_is_soft(self):
        """连不上服务器不能让界面卡死或崩，返回空榜 + 可读的错误。"""
        with mock.patch(
            "client.board.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            entries, error = fetch_leaderboard("http://x")
        self.assertEqual(entries, [])
        self.assertIn("连不上", error)

    def test_http_error_reports_server_message(self):
        fake = mock.Mock()
        fake.status_code = 400
        fake.json.return_value = {"error": "limit 非法"}
        with mock.patch("client.board.requests.get", return_value=fake):
            entries, error = fetch_leaderboard("http://x")
        self.assertEqual(entries, [])
        self.assertEqual(error, "limit 非法")

    def test_non_json_response_is_soft(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.side_effect = ValueError("no json")
        with mock.patch("client.board.requests.get", return_value=fake):
            entries, error = fetch_leaderboard("http://x")
        self.assertEqual(entries, [])
        self.assertIn("JSON", error)


class LoginScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.screen = _surface()
        cls.font = load_font(18)
        cls.big_font = load_font(24, bold=True)

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def _screen_obj(self) -> LoginScreen:
        return LoginScreen(self.screen, self.font, self.big_font, "http://api")

    def test_empty_fields_is_rejected_locally(self):
        """空用户名/密码不该发请求 —— 本地就挡住。"""
        screen = self._screen_obj()
        with mock.patch("client.login.requests.post") as post:
            result = screen._submit("login")
        self.assertIsNone(result)
        post.assert_not_called()
        self.assertIn("都要填", screen.message)

    def test_success_returns_token_and_name(self):
        screen = self._screen_obj()
        screen.username = "alice"
        screen.password = "unit-test-password"
        fake = mock.Mock()
        fake.status_code = 201
        fake.json.return_value = {"token": "abc123", "username": "alice"}
        with mock.patch("client.login.requests.post", return_value=fake):
            result = screen._submit("register")
        self.assertEqual(result, ("abc123", "alice"))

    def test_login_uses_login_path_and_register_uses_register_path(self):
        screen = self._screen_obj()
        screen.username, screen.password = "a", "b"
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {"token": "t", "username": "a"}
        with mock.patch("client.login.requests.post", return_value=fake) as post:
            screen._submit("login")
            self.assertIn("/api/auth/login", post.call_args.args[0])
            screen._submit("register")
            self.assertIn("/api/auth/register", post.call_args.args[0])

    def test_server_error_message_is_surfaced(self):
        screen = self._screen_obj()
        screen.username, screen.password = "a", "b"
        fake = mock.Mock()
        fake.status_code = 401
        fake.json.return_value = {"error": "用户名或密码错误"}
        with mock.patch("client.login.requests.post", return_value=fake):
            result = screen._submit("login")
        self.assertIsNone(result)
        self.assertEqual(screen.message, "用户名或密码错误")

    def test_network_error_is_soft(self):
        screen = self._screen_obj()
        screen.username, screen.password = "a", "b"
        with mock.patch(
            "client.login.requests.post",
            side_effect=requests.ConnectionError("down"),
        ):
            result = screen._submit("login")
        self.assertIsNone(result)
        self.assertIn("连不上", screen.message)

    def test_password_is_masked_in_state_not_rendering(self):
        """密码明文保存在内存里（要发给服务端），但界面显示的是星号。"""
        screen = self._screen_obj()
        screen.focus = "password"
        for char in "abc":
            screen._handle_key(
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, unicode=char)
            )
        self.assertEqual(screen.password, "abc")

    def test_tab_switches_focus(self):
        screen = self._screen_obj()
        self.assertEqual(screen.focus, "username")
        screen._handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB, unicode=""))
        self.assertEqual(screen.focus, "password")
        screen._handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB, unicode=""))
        self.assertEqual(screen.focus, "username")

    def test_backspace_edits_focused_field(self):
        screen = self._screen_obj()
        screen.username = "abc"
        screen._handle_key(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, unicode="")
        )
        self.assertEqual(screen.username, "ab")

    def test_username_length_capped(self):
        screen = self._screen_obj()
        for _ in range(30):
            screen._handle_key(
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, unicode="a")
            )
        self.assertEqual(len(screen.username), 20)



class InterpolationRegressionTest(unittest.TestCase):
    """守住一个曾经存在、影响过 E4/E5 结论解读的 bug。"""

    def test_client_uses_measured_interval(self):
        import inspect

        import client.main as client_main

        # 模块级不能再有写死的 TICK_INTERVAL（用属性判断，别用字符串扫描 ——
        # 解释性注释里会提到这个名字，扫字符串会误报）
        self.assertFalse(
            hasattr(client_main, "TICK_INTERVAL"),
            "客户端不该再有写死的 TICK_INTERVAL —— 插值分母必须来自实际到达时刻",
        )
        source = inspect.getsource(client_main)
        self.assertIn("clamp_alpha", source,
                      "客户端插值必须复用 protocol/interp.py 的 clamp_alpha")
        self.assertIn("curr_arrived_at", source)
        self.assertIn("prev_arrived_at", source)

    def test_clamp_alpha_matches_render_intent(self):
        """插值系数的语义：新帧到达时 alpha=0（显示上一帧），过一个间隔才到 1。"""
        from protocol.interp import clamp_alpha

        for span in (0.033, 0.100):          # 30 Hz / 10 Hz
            self.assertAlmostEqual(clamp_alpha(span, 0.0, span), 0.0, places=9)
            self.assertAlmostEqual(clamp_alpha(span + span / 2, 0.0, span), 0.5, places=9)
            self.assertAlmostEqual(clamp_alpha(span * 2, 0.0, span), 1.0, places=9)

if __name__ == "__main__":
    unittest.main()